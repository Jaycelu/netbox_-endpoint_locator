import json

from django.shortcuts import render
from django.views.generic import View
from dcim.models import Device
from ipam.models import IPAddress

from .forms import EndpointLookupForm
from .librenms import (
    collect_port_vlan_values,
    extract_vlan_from_interface_fields,
    is_ip,
    normalize_mac,
    lookup_arp_by_mac,
    lookup_arp_by_ip,
    lookup_device_vlans,
    lookup_fdb_by_mac,
    lookup_fdb_detail_by_mac,
    lookup_port_by_id,
    parse_ip_from_arp,
    parse_mac_from_arp,
    pick_arp_record,
    pick_scored_candidate,
    resolve_fdb_vlan,
    score_fdb_candidate,
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
    def _get_device_lookup_key(port_info, fallback_device_id=None):
        if isinstance(port_info, dict):
            device = port_info.get("device")
            if isinstance(device, dict):
                for key in ("hostname", "sysName", "display", "name"):
                    value = device.get(key)
                    if value:
                        return str(value).strip()

            for key in ("hostname", "device_hostname", "device_id"):
                value = port_info.get(key)
                if value:
                    return str(value).strip()

        if fallback_device_id:
            return str(fallback_device_id).strip()

        return None

    def _get_port(self, port_id):
        if not port_id:
            return None

        cache_key = str(port_id).strip()
        if cache_key not in self._port_cache:
            self._port_cache[cache_key] = lookup_port_by_id(
                port_id,
                with_relations=["device", "vlans"],
            )

        return self._port_cache[cache_key]

    def _get_device_vlans_cached(self, device_key):
        if not device_key:
            return []

        cache_key = str(device_key).strip()
        if cache_key not in self._device_vlan_cache:
            self._device_vlan_cache[cache_key] = lookup_device_vlans(device_key)

        return self._device_vlan_cache[cache_key]

    @staticmethod
    def _interface_names(*records):
        names = set()

        for record in records:
            if not isinstance(record, dict):
                continue
            for key in ("ifName", "ifDescr", "ifAlias", "port", "port_label", "portName"):
                value = record.get(key)
                if value:
                    names.add(str(value).strip().lower())

        return names

    def _pick_matching_detail_record(self, records, fdb_record, port_info):
        if not records:
            return None

        target_ifnames = self._interface_names(fdb_record, port_info)
        target_hostnames = {
            str(value).strip().lower()
            for value in (
                ((port_info or {}).get("device") or {}).get("hostname") if isinstance((port_info or {}).get("device"), dict) else "",
                ((port_info or {}).get("device") or {}).get("sysName") if isinstance((port_info or {}).get("device"), dict) else "",
                (fdb_record or {}).get("hostname"),
                (fdb_record or {}).get("device_hostname"),
            )
            if value
        }

        interface_and_host_matches = []
        interface_matches = []
        hostname_matches = []

        for item in records:
            ifname = str(
                item.get("ifName")
                or item.get("ifDescr")
                or item.get("ifAlias")
                or item.get("port")
                or ""
            ).strip().lower()
            hostname = str(item.get("hostname") or item.get("sysName") or "").strip().lower()

            same_ifname = bool(target_ifnames and ifname in target_ifnames)
            same_hostname = bool(target_hostnames and hostname in target_hostnames)

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
        return records[0]

    def _collect_arp_context(self, arp_records):
        context = {
            "records": [],
            "port_ids": set(),
            "device_ids": set(),
            "vlans": set(),
            "ips": [],
            "ip_by_port_id": {},
            "interfaces": set(),
        }

        for arp_record in arp_records or []:
            if not isinstance(arp_record, dict):
                continue

            port_id = arp_record.get("port_id")
            port_info = self._get_port(port_id) if port_id else None
            record_ip = parse_ip_from_arp(arp_record)

            if port_id:
                port_id_text = str(port_id).strip()
                context["port_ids"].add(port_id_text)
                if record_ip and port_id_text not in context["ip_by_port_id"]:
                    context["ip_by_port_id"][port_id_text] = record_ip

            device_id = arp_record.get("device_id") or (port_info or {}).get("device_id")
            if device_id:
                context["device_ids"].add(str(device_id).strip())

            vlan_hint = extract_vlan_from_interface_fields(arp_record)
            if not vlan_hint and port_info:
                vlan_hint = extract_vlan_from_interface_fields(port_info)
            if vlan_hint:
                context["vlans"].add(vlan_hint)

            if record_ip and record_ip not in context["ips"]:
                context["ips"].append(record_ip)

            context["interfaces"].update(self._interface_names(arp_record, port_info))
            context["records"].append({"arp": arp_record, "port": port_info})

        return context

    def _pick_related_ip(self, arp_context, fdb_record):
        port_id = str(fdb_record.get("port_id") or "").strip()
        if port_id and port_id in arp_context["ip_by_port_id"]:
            return arp_context["ip_by_port_id"][port_id]

        device_id = str(fdb_record.get("device_id") or "").strip()
        for item in arp_context["records"]:
            arp_record = item["arp"]
            port_info = item["port"] or {}

            if port_id and str(arp_record.get("port_id") or "").strip() == port_id:
                return parse_ip_from_arp(arp_record)

            if device_id and str(port_info.get("device_id") or arp_record.get("device_id") or "").strip() == device_id:
                record_ip = parse_ip_from_arp(arp_record)
                if record_ip:
                    return record_ip

        return arp_context["ips"][0] if arp_context["ips"] else ""

    def _build_fdb_candidate(self, fdb_record, arp_context, fdb_detail_records):
        port_info = self._get_port(fdb_record.get("port_id"))
        device_key = self._get_device_lookup_key(port_info, fallback_device_id=fdb_record.get("device_id"))
        device_vlans = self._get_device_vlans_cached(device_key)
        detail_record = self._pick_matching_detail_record(fdb_detail_records, fdb_record, port_info)
        vlan = resolve_fdb_vlan(
            fdb_record,
            device_vlans=device_vlans,
            port_info=port_info,
            arp_record=arp_context["records"][0]["arp"] if arp_context["records"] else None,
        )
        score = score_fdb_candidate(
            fdb_record,
            preferred_port_ids=arp_context["port_ids"],
            preferred_device_ids=arp_context["device_ids"],
            preferred_vlans=arp_context["vlans"],
            candidate_vlan=vlan,
        )

        if port_info:
            port_names = self._interface_names(port_info)
            if port_names and port_names & arp_context["interfaces"]:
                score += 40

        if vlan and vlan in collect_port_vlan_values(port_info):
            score += 20

        related_ip = self._pick_related_ip(arp_context, fdb_record)

        return {
            "score": score,
            "updated_at": str(fdb_record.get("updated_at") or ""),
            "port_id": str(fdb_record.get("port_id") or ""),
            "vlan": vlan,
            "fdb": dict(fdb_record),
            "port": port_info,
            "detail": detail_record,
            "related_ip": related_ip,
            "arp_records": [item["arp"] for item in arp_context["records"]],
        }

    def locate_by_mac(self, mac, arp_records=None):
        arp_context = self._collect_arp_context(arp_records or [])
        fdb_records = lookup_fdb_by_mac(mac)
        fdb_detail_records = lookup_fdb_detail_by_mac(mac)
        candidates = [self._build_fdb_candidate(item, arp_context, fdb_detail_records) for item in fdb_records]

        return pick_scored_candidate(candidates)

    @staticmethod
    def _device_names(port_info, fallback_record, detail_record=None):
        device = (port_info or {}).get("device") if isinstance(port_info, dict) else None
        hostname = ""
        device_name = ""

        if isinstance(device, dict):
            hostname = str(
                device.get("hostname")
                or device.get("sysName")
                or device.get("display")
                or device.get("name")
                or ""
            ).strip()
            device_name = str(
                device.get("sysName")
                or device.get("display")
                or device.get("name")
                or hostname
            ).strip()

        if not hostname:
            hostname = str(
                (fallback_record or {}).get("hostname")
                or (fallback_record or {}).get("device_hostname")
                or (detail_record or {}).get("hostname")
                or ""
            ).strip()

        if not device_name:
            device_name = str(
                (fallback_record or {}).get("sysName")
                or (fallback_record or {}).get("device_name")
                or (detail_record or {}).get("sysName")
                or hostname
            ).strip()

        return hostname, device_name

    def build_result(self, q, query_type, mac, candidate):
        port_info = candidate.get("port") or {}
        fdb_record = candidate.get("fdb") or {}
        detail_record = candidate.get("detail") or {}
        hostname, device_name = self._device_names(port_info, fdb_record, detail_record=detail_record)

        netbox_device = self.get_netbox_device_by_mgmt_ip(hostname)
        display_ip = q if query_type == "ip" else candidate.get("related_ip", "")
        interface = str(
            port_info.get("ifName")
            or port_info.get("ifDescr")
            or port_info.get("ifAlias")
            or detail_record.get("ifName")
            or detail_record.get("ifDescr")
            or detail_record.get("ifAlias")
            or fdb_record.get("ifName")
            or fdb_record.get("port")
            or ""
        ).strip()
        raw = {
            "score": candidate.get("score"),
            "related_ip": candidate.get("related_ip"),
            "fdb": fdb_record,
            "port": port_info,
            "detail": detail_record,
            "arp_records": candidate.get("arp_records", []),
        }

        return {
            "query": q,
            "query_type": query_type,
            "mac": mac,
            "ip": display_ip,
            "hostname": hostname,
            "device_name": device_name,
            "interface": interface,
            "vlan": candidate.get("vlan", ""),
            "netbox_device": netbox_device,
            "raw": raw,
            "raw_pretty": self._pretty_json(raw),
        }

    @staticmethod
    def _pretty_json(obj) -> str:
        try:
            return json.dumps(obj, ensure_ascii=False, indent=2)
        except TypeError:
            return str(obj)

    def get(self, request):
        self._port_cache = {}
        self._device_vlan_cache = {}

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

                candidate = self.locate_by_mac(mac, arp_records=[arp_record] if arp_record else [])

                if not candidate:
                    context["error"] = f"找到了 MAC {mac}，但未在 FDB/端口表中定位到交换机接口"
                    return render(request, self.template_name, context)

                context["result"] = self.build_result(q, "ip", mac, candidate)
                return render(request, self.template_name, context)

            mac = normalize_mac(q)
            candidate = self.locate_by_mac(mac, arp_records=lookup_arp_by_mac(mac))

            if not candidate:
                context["error"] = f"未在 LibreNMS FDB/端口表中找到 MAC {mac}"
                return render(request, self.template_name, context)

            context["result"] = self.build_result(q, "mac", mac, candidate)
            return render(request, self.template_name, context)

        except Exception as exc:
            context["error"] = str(exc)
            return render(request, self.template_name, context)
