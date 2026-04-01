import json

from django.shortcuts import render
from django.views.generic import View
from dcim.models import Device
from ipam.models import IPAddress

from .forms import EndpointLookupForm
from .librenms import (
    extract_terminal_vlan,
    is_ip,
    normalize_mac,
    lookup_arp_by_ip,
    lookup_fdb_by_mac,
    lookup_fdb_detail_by_mac,
    lookup_port_by_mac,
    parse_mac_from_arp,
    pick_best_result,
)


class EndpointLookupView(View):
    template_name = "netbox_endpoint_locator/lookup.html"

    def get_netbox_device_by_mgmt_ip(self, hostname: str):
        """
        你的 LibreNMS 是按管理 IP 同步设备，
        所以这里优先按 primary_ip4 去匹配 NetBox 设备。
        """
        if not hostname:
            return None

        mgmt_ip = str(hostname).strip()
        if "/" in mgmt_ip:
            ip_obj = IPAddress.objects.filter(address=mgmt_ip).first()
        else:
            ip_obj = IPAddress.objects.filter(address__startswith=f"{mgmt_ip}/").first()
        if not ip_obj:
            return None

        return Device.objects.filter(primary_ip4=ip_obj).first()

    def locate_by_mac(self, mac):
        fdb_detail_records = lookup_fdb_detail_by_mac(mac)
        fdb_records = lookup_fdb_by_mac(mac)
        best = pick_best_result(fdb_detail_records)
        port_records = []

        if not best:
            port_records = lookup_port_by_mac(mac)
            best = pick_best_result(port_records)

        vlan = extract_terminal_vlan(best, fdb_detail_records, fdb_records, port_records)
        return best, vlan

    def build_result(self, q, query_type, mac, best, vlan):
        hostname = str(
            best.get("hostname")
            or best.get("device_hostname")
            or best.get("device")
            or ""
        )

        netbox_device = self.get_netbox_device_by_mgmt_ip(hostname)

        return {
            "query": q,
            "query_type": query_type,
            "mac": mac,
            "hostname": hostname,
            "device_name": best.get("sysName") or best.get("device_name") or hostname,
            "interface": best.get("ifName") or best.get("port_label") or best.get("port") or "",
            "vlan": vlan,
            "netbox_device": netbox_device,
            "raw": best,
            "raw_pretty": self._pretty_json(best),
        }

    @staticmethod
    def _pretty_json(obj) -> str:
        try:
            return json.dumps(obj, ensure_ascii=False, indent=2)
        except TypeError:
            return str(obj)

    def get(self, request):
        form = EndpointLookupForm(request.GET or None)
        context = {
            "form": form,
            "result": None,
            "error": None,
        }

        if not form.is_valid():
            return render(request, self.template_name, context)

        q = form.cleaned_data["q"].strip()

        try:
            if is_ip(q):
                arp_records = lookup_arp_by_ip(q)
                mac = parse_mac_from_arp(arp_records)

                if not mac:
                    context["error"] = f"未在 LibreNMS ARP 中找到 {q}"
                    return render(request, self.template_name, context)

                best, vlan = self.locate_by_mac(mac)

                if not best:
                    context["error"] = f"找到了 MAC {mac}，但未在 FDB/端口表中定位到交换机接口"
                    return render(request, self.template_name, context)

                context["result"] = self.build_result(q, "ip", mac, best, vlan)
                return render(request, self.template_name, context)

            mac = normalize_mac(q)
            best, vlan = self.locate_by_mac(mac)

            if not best:
                context["error"] = f"未在 LibreNMS FDB/端口表中找到 MAC {mac}"
                return render(request, self.template_name, context)

            context["result"] = self.build_result(q, "mac", mac, best, vlan)
            return render(request, self.template_name, context)

        except Exception as exc:
            context["error"] = str(exc)
            return render(request, self.template_name, context)
