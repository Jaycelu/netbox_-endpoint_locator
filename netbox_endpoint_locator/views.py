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
    lookup_device_links,
    lookup_device_port_stack,
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
from .topology import build_port_stack_members, candidate_id, pick_edge_candidate


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
    def _get_device_lookup_key(port_info, *fallback_records):
        if isinstance(port_info, dict):
            device = port_info.get("device")
            if isinstance(device, dict):
                for key in ("hostname", "device_id", "sysName", "display", "name"):
                    value = device.get(key)
                    if value:
                        return str(value).strip()

            for key in ("hostname", "device_hostname", "device_id"):
                value = port_info.get(key)
                if value:
                    return str(value).strip()

        for record in fallback_records:
            if not isinstance(record, dict):
                if record:
                    return str(record).strip()
                continue

            for key in ("hostname", "device_hostname", "device_id"):
                value = record.get(key)
                if value:
                    return str(value).strip()

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

    def _get_device_links_cached(self, device_key, cache_key=None):
        lookup_key = str(device_key).strip() if device_key else ""
        cache_token = str(cache_key or lookup_key).strip()
        if not cache_token or not lookup_key:
            return []

        if cache_token not in self._device_link_cache:
            self._device_link_cache[cache_token] = lookup_device_links(lookup_key)

        return self._device_link_cache[cache_token]

    def _get_device_port_stack_cached(self, device_key, cache_key=None):
        lookup_key = str(device_key).strip() if device_key else ""
        cache_token = str(cache_key or lookup_key).strip()
        if not cache_token or not lookup_key:
            return []

        if cache_token not in self._device_port_stack_cache:
            self._device_port_stack_cache[cache_token] = lookup_device_port_stack(lookup_key)

        return self._device_port_stack_cache[cache_token]

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
        detail_record = self._pick_matching_detail_record(fdb_detail_records, fdb_record, port_info)
        device_key = self._get_device_lookup_key(port_info, fdb_record, detail_record)
        device_vlans = self._get_device_vlans_cached(device_key)
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
        hostname, device_name = self._device_names(port_info, fdb_record, detail_record=detail_record)
        interface = str(
            (port_info or {}).get("ifName")
            or (port_info or {}).get("ifDescr")
            or (port_info or {}).get("ifAlias")
            or (detail_record or {}).get("ifName")
            or (detail_record or {}).get("ifDescr")
            or (detail_record or {}).get("ifAlias")
            or fdb_record.get("ifName")
            or fdb_record.get("port")
            or ""
        ).strip()
        description = str(
            (port_info or {}).get("ifAlias")
            or (detail_record or {}).get("ifAlias")
            or (detail_record or {}).get("ifDescr")
            or fdb_record.get("description")
            or fdb_record.get("port_descr")
            or ""
        ).strip()
        device_id = str(
            fdb_record.get("device_id")
            or (port_info or {}).get("device_id")
            or (((port_info or {}).get("device") or {}).get("device_id") if isinstance((port_info or {}).get("device"), dict) else "")
            or (detail_record or {}).get("device_id")
            or ""
        ).strip()
        port_id = str(fdb_record.get("port_id") or "").strip()
        candidate_token = f"{device_id}:{port_id}" if device_id and port_id else port_id

        return {
            "candidate_id": candidate_token,
            "score": score,
            "updated_at": str(fdb_record.get("updated_at") or ""),
            "device_id": device_id,
            "device_key": str(device_key or device_id or "").strip(),
            "hostname": hostname,
            "device_name": device_name,
            "interface": interface,
            "description": description,
            "port_id": port_id,
            "vlan": vlan,
            "fdb": dict(fdb_record),
            "port": port_info,
            "detail": detail_record,
            "related_ip": related_ip,
            "arp_records": [item["arp"] for item in arp_context["records"]],
        }

    @staticmethod
    def _candidate_summary(candidate):
        if not isinstance(candidate, dict):
            return {}

        return {
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "device_id": str(candidate.get("device_id") or ""),
            "device_key": str(candidate.get("device_key") or ""),
            "hostname": str(candidate.get("hostname") or ""),
            "device_name": str(candidate.get("device_name") or ""),
            "interface": str(candidate.get("interface") or ""),
            "description": str(candidate.get("description") or ""),
            "port_id": str(candidate.get("port_id") or ""),
            "vlan": str(candidate.get("vlan") or ""),
            "related_ip": str(candidate.get("related_ip") or ""),
            "score": candidate.get("score"),
            "updated_at": str(candidate.get("updated_at") or ""),
        }

    def _build_topology_context(self, canonical_candidate, candidates):
        if not canonical_candidate:
            return {
                "selected": None,
                "graph": {},
                "path": [],
                "scores": {},
                "candidates": [],
                "links_by_device": {},
                "stack_members_by_device": {},
            }

        links_by_device = {}
        stack_members_by_device = {}

        for candidate in candidates:
            device_id = str(candidate.get("device_id") or "").strip()
            device_key = str(candidate.get("device_key") or device_id).strip()
            if not device_id or not device_key:
                continue

            if device_id not in links_by_device:
                links_by_device[device_id] = self._get_device_links_cached(device_key, cache_key=device_id)

            if device_id not in stack_members_by_device:
                stack_records = self._get_device_port_stack_cached(device_key, cache_key=device_id)
                stack_members_by_device[device_id] = build_port_stack_members(stack_records)

        selection = pick_edge_candidate(
            canonical_candidate,
            candidates,
            links_by_device,
            stack_members_by_device,
        )

        return {
            "selected": selection.get("selected"),
            "graph": {
                node_id: sorted(neighbors)
                for node_id, neighbors in (selection.get("graph") or {}).items()
            },
            "path": list(selection.get("path") or []),
            "scores": dict(selection.get("scores") or {}),
            "candidates": list(selection.get("candidates") or []),
            "links_by_device": links_by_device,
            "stack_members_by_device": {
                device_id: {
                    port_id: sorted(member_ids)
                    for port_id, member_ids in port_map.items()
                }
                for device_id, port_map in stack_members_by_device.items()
            },
        }

    def _candidate_path_summary(self, path_ids, candidates):
        by_id = {candidate_id(item): item for item in candidates if isinstance(item, dict)}
        return [
            self._candidate_summary(by_id[node_id])
            for node_id in path_ids or []
            if node_id in by_id
        ]

    def locate_by_mac(self, mac, arp_records=None):
        arp_context = self._collect_arp_context(arp_records or [])
        fdb_records = lookup_fdb_by_mac(mac)
        fdb_detail_records = lookup_fdb_detail_by_mac(mac)
        candidates = [self._build_fdb_candidate(item, arp_context, fdb_detail_records) for item in fdb_records]
        canonical_candidate = pick_scored_candidate(candidates)
        topology = self._build_topology_context(canonical_candidate, candidates)

        return {
            "canonical": canonical_candidate,
            "edge": topology.get("selected") or canonical_candidate,
            "candidates": candidates,
            "topology": topology,
        }

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

    def build_result(self, q, query_type, mac, localization):
        canonical_candidate = (localization or {}).get("canonical") or {}
        display_candidate = (localization or {}).get("edge") or canonical_candidate
        topology = (localization or {}).get("topology") or {}
        all_candidates = (localization or {}).get("candidates") or []

        canonical_port = canonical_candidate.get("port") or {}
        canonical_fdb = canonical_candidate.get("fdb") or {}
        canonical_detail = canonical_candidate.get("detail") or {}
        display_port = display_candidate.get("port") or {}
        display_fdb = display_candidate.get("fdb") or {}
        display_detail = display_candidate.get("detail") or {}

        hostname = str(
            display_candidate.get("hostname")
            or canonical_candidate.get("hostname")
            or ""
        ).strip()
        device_name = str(
            display_candidate.get("device_name")
            or canonical_candidate.get("device_name")
            or hostname
        ).strip()
        interface = str(
            display_candidate.get("interface")
            or canonical_candidate.get("interface")
            or ""
        ).strip()
        netbox_device = self.get_netbox_device_by_mgmt_ip(hostname)
        display_ip = q if query_type == "ip" else canonical_candidate.get("related_ip", "")
        selected_candidate_id = candidate_id(display_candidate) if display_candidate else ""
        canonical_candidate_id = candidate_id(canonical_candidate) if canonical_candidate else ""
        raw = {
            "canonical_result": self._candidate_summary(canonical_candidate),
            "edge_localized_result": self._candidate_summary(display_candidate),
            "display_overridden": bool(
                canonical_candidate_id
                and selected_candidate_id
                and canonical_candidate_id != selected_candidate_id
            ),
            "topology": {
                "canonical_candidate_id": canonical_candidate_id,
                "selected_candidate_id": selected_candidate_id,
                "path": self._candidate_path_summary(topology.get("path", []), all_candidates),
                "scores": topology.get("scores", {}),
                "graph": topology.get("graph", {}),
                "links_by_device": topology.get("links_by_device", {}),
                "stack_members_by_device": topology.get("stack_members_by_device", {}),
            },
            "candidates": [self._candidate_summary(item) for item in all_candidates],
            "canonical_evidence": {
                "fdb": canonical_fdb,
                "port": canonical_port,
                "detail": canonical_detail,
                "arp_records": canonical_candidate.get("arp_records", []),
            },
            "display_evidence": {
                "fdb": display_fdb,
                "port": display_port,
                "detail": display_detail,
            },
        }

        return {
            "query": q,
            "query_type": query_type,
            "mac": mac,
            "ip": display_ip,
            "hostname": hostname,
            "device_name": device_name,
            "interface": interface,
            "vlan": canonical_candidate.get("vlan", ""),
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
        self._device_link_cache = {}
        self._device_port_stack_cache = {}

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

                localization = self.locate_by_mac(mac, arp_records=[arp_record] if arp_record else [])

                if not localization or not localization.get("canonical"):
                    context["error"] = f"找到了 MAC {mac}，但未在 FDB/端口表中定位到交换机接口"
                    return render(request, self.template_name, context)

                context["result"] = self.build_result(q, "ip", mac, localization)
                return render(request, self.template_name, context)

            mac = normalize_mac(q)
            localization = self.locate_by_mac(mac, arp_records=lookup_arp_by_mac(mac))

            if not localization or not localization.get("canonical"):
                context["error"] = f"未在 LibreNMS FDB/端口表中找到 MAC {mac}"
                return render(request, self.template_name, context)

            context["result"] = self.build_result(q, "mac", mac, localization)
            return render(request, self.template_name, context)

        except Exception as exc:
            context["error"] = str(exc)
            return render(request, self.template_name, context)
