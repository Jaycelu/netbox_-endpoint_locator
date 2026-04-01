import json

from django.shortcuts import render
from django.views.generic import View
from dcim.models import Device
from ipam.models import IPAddress

from .forms import EndpointLookupForm
from .librenms import (
    extract_vlan_from_interface_fields,
    extract_terminal_vlan,
    is_ip,
    normalize_mac,
    lookup_arp_by_ip,
    lookup_fdb_by_mac,
    lookup_fdb_detail_by_mac,
    lookup_port_by_id,
    lookup_port_by_mac,
    parse_mac_from_arp,
    pick_arp_record,
    pick_best_result,
    pick_fdb_record,
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

    @staticmethod
    def _merge_best_record(base, extra):
        merged = dict(base or {})
        if not isinstance(extra, dict):
            return merged

        for key, value in extra.items():
            if value is not None and key != "device":
                merged[key] = value

        device = extra.get("device")
        if isinstance(device, dict):
            hostname = (
                device.get("hostname")
                or device.get("sysName")
                or device.get("display")
                or device.get("name")
                or ""
            )
            device_name = (
                device.get("sysName")
                or device.get("display")
                or device.get("name")
                or hostname
            )

            if hostname:
                merged.setdefault("hostname", hostname)
                merged.setdefault("device_hostname", hostname)
            if device_name:
                merged.setdefault("sysName", device_name)
                merged.setdefault("device_name", device_name)

        return merged

    @staticmethod
    def _pick_matching_detail_record(records, best):
        if not records or not best:
            return None

        target_ifnames = {
            str(best.get(key)).strip().lower()
            for key in ("ifName", "ifDescr", "ifAlias", "port", "port_label", "portName")
            if best.get(key)
        }
        target_hostnames = {
            str(best.get(key)).strip().lower()
            for key in ("hostname", "device_hostname")
            if best.get(key)
        }

        interface_and_host_matches = []
        interface_matches = []
        hostname_matches = []

        for item in records:
            item_ifname = str(
                item.get("ifName")
                or item.get("ifDescr")
                or item.get("ifAlias")
                or item.get("port")
                or ""
            ).strip().lower()
            item_hostname = str(
                item.get("hostname")
                or item.get("device_hostname")
                or item.get("device")
                or ""
            ).strip().lower()

            same_ifname = bool(target_ifnames and item_ifname in target_ifnames)
            same_hostname = bool(target_hostnames and item_hostname in target_hostnames)

            if same_ifname and same_hostname:
                interface_and_host_matches.append(item)
            elif same_ifname:
                interface_matches.append(item)
            elif same_hostname:
                hostname_matches.append(item)

        if interface_and_host_matches:
            return interface_and_host_matches[0]
        if interface_matches:
            return interface_matches[0]
        if hostname_matches:
            return hostname_matches[0]
        return None

    def _resolve_fdb_result(self, fdb_record, fdb_detail_records):
        best = dict(fdb_record or {})
        port_info = None

        if best.get("port_id"):
            port_info = lookup_port_by_id(best.get("port_id"), with_relations=["device"])
            best = self._merge_best_record(best, port_info)

        detail_record = self._pick_matching_detail_record(fdb_detail_records, best)
        if detail_record:
            best = self._merge_best_record(best, detail_record)

        vlan = extract_terminal_vlan(fdb_record, port_info, detail_record)
        return best, vlan

    def locate_by_mac(self, mac, preferred_device_id=None, preferred_vlan=None):
        fdb_records = lookup_fdb_by_mac(mac)
        port_records = lookup_port_by_mac(mac)
        fdb_detail_records = lookup_fdb_detail_by_mac(mac)
        fdb_record = pick_fdb_record(
            fdb_records,
            preferred_device_id=preferred_device_id,
            preferred_vlan=preferred_vlan,
            port_records=port_records,
        )

        if fdb_record:
            return self._resolve_fdb_result(fdb_record, fdb_detail_records)

        best = pick_best_result(fdb_detail_records) or pick_best_result(port_records)
        vlan = extract_terminal_vlan(best)
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
                arp_record = pick_arp_record(arp_records, q)
                mac = parse_mac_from_arp([arp_record] if arp_record else [])

                if not mac:
                    context["error"] = f"未在 LibreNMS ARP 中找到 {q}"
                    return render(request, self.template_name, context)

                preferred_device_id = arp_record.get("device_id") if arp_record else None
                preferred_vlan = extract_vlan_from_interface_fields(arp_record)

                if arp_record and arp_record.get("port_id"):
                    arp_port = lookup_port_by_id(
                        arp_record.get("port_id"),
                        with_relations=["device"],
                    )
                    if arp_port:
                        preferred_device_id = preferred_device_id or arp_port.get("device_id")
                        preferred_vlan = preferred_vlan or extract_vlan_from_interface_fields(arp_port)

                best, vlan = self.locate_by_mac(
                    mac,
                    preferred_device_id=preferred_device_id,
                    preferred_vlan=preferred_vlan,
                )

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
