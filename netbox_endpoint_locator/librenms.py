import ipaddress
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import quote, urljoin

import requests
from django.conf import settings

PLUGIN_SLUG = "netbox_endpoint_locator"


def _get_plugin_cfg() -> Dict[str, Any]:
    """
    Return plugin config from NetBox settings.

    IMPORTANT: Do not access required keys at import-time; NetBox imports plugins
    early, and missing config should fail gracefully at request time.
    """

    cfg = (settings.PLUGINS_CONFIG or {}).get(PLUGIN_SLUG, {}) or {}
    missing = [k for k in ("librenms_url", "librenms_token") if not cfg.get(k)]
    if missing:
        raise RuntimeError(
            "EndpointLocator 插件配置缺失："
            f"{', '.join(missing)}。请在 NetBox 的 PLUGINS_CONFIG['{PLUGIN_SLUG}'] 中设置。"
        )
    return cfg


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def normalize_mac(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^0-9a-f]", "", value)
    if len(value) != 12:
        raise ValueError("无效的 MAC 地址格式")
    return value


def format_mac_readable(value: str) -> str:
    normalized = normalize_mac(value)
    return ":".join(normalized[index : index + 2] for index in range(0, 12, 2))


def format_mac_ui(value: str) -> str:
    normalized = normalize_mac(value)
    return "-".join(normalized[index : index + 4] for index in range(0, 12, 4))


def _get(path: str) -> Dict[str, Any]:
    cfg = _get_plugin_cfg()
    base_url = str(cfg["librenms_url"]).rstrip("/") + "/"
    url = urljoin(base_url, path.lstrip("/"))

    headers = {
        "X-Auth-Token": cfg["librenms_token"],
        "Accept": "application/json",
    }
    verify_ssl = cfg.get("verify_ssl", False)
    timeout = cfg.get("timeout", 15)

    resp = requests.get(url, headers=headers, verify=verify_ssl, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _records(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("arp", "ports_fdb", "port", "ports", "vlans", "data", "items", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
    if isinstance(data, list):
        return data
    return []


def lookup_arp(query: str) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/resources/ip/arp/{quote(str(query), safe='')}"))


def lookup_arp_by_ip(ip: str) -> List[Dict[str, Any]]:
    return lookup_arp(ip)


def lookup_arp_by_mac(mac: str) -> List[Dict[str, Any]]:
    return lookup_arp(format_mac_readable(mac))


def lookup_fdb_by_mac(mac: str) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/resources/fdb/{mac}"))


def lookup_fdb_detail_by_mac(mac: str) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/resources/fdb/{mac}/detail"))


def lookup_device_fdb(device: Any) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/devices/{quote(str(device), safe='')}/fdb"))


def lookup_device_vlans(device: Any) -> List[Dict[str, Any]]:
    device_text = str(device).strip()
    return _records(_get(f"/api/v0/resources/vlans?hostname={quote(device_text, safe='')}"))


def lookup_device_links(device: Any) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/devices/{quote(str(device), safe='')}/links"))


def lookup_device_port_stack(device: Any) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/devices/{quote(str(device), safe='')}/port_stack"))


def lookup_port_by_mac(mac: str) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/ports/mac/{mac}?filter=first"))


def lookup_port_by_id(port_id: Any, with_relations: Optional[Sequence[str]] = None) -> Optional[Dict[str, Any]]:
    allowed = [str(item).strip() for item in with_relations or [] if str(item).strip()]
    if not allowed:
        records = _records(_get(f"/api/v0/ports/{port_id}"))
        if not records:
            return None
        return records[0]

    merged: Dict[str, Any] = {}
    for relation in allowed:
        records = _records(_get(f"/api/v0/ports/{port_id}?with={quote(relation, safe='')}"))
        if not records:
            continue

        for key, value in records[0].items():
            if value is not None:
                merged[key] = value

    if merged:
        return merged

    records = _records(_get(f"/api/v0/ports/{port_id}"))
    if not records:
        return None
    return records[0]


def parse_mac_from_arp(records: List[Dict[str, Any]]) -> Optional[str]:
    for item in records:
        for key in ("mac_address", "mac", "ifPhysAddress", "phys_address"):
            value = item.get(key)
            if value:
                try:
                    return normalize_mac(str(value))
                except ValueError:
                    continue
    return None


def parse_ip_from_arp(record: Optional[Dict[str, Any]]) -> str:
    if not isinstance(record, dict):
        return ""

    for key in ("ipv4_address", "ip", "address"):
        value = record.get(key)
        if value:
            return str(value).strip()

    return ""


def pick_arp_record(records: List[Dict[str, Any]], ip: Optional[str] = None) -> Optional[Dict[str, Any]]:
    valid_records: List[Dict[str, Any]] = []
    ip_text = str(ip).strip() if ip else ""

    for item in records:
        mac = parse_mac_from_arp([item])
        if not mac:
            continue

        record_ip = str(item.get("ipv4_address") or item.get("ip") or item.get("address") or "").strip()
        if ip_text and record_ip == ip_text:
            return item

        valid_records.append(item)

    if valid_records:
        return valid_records[0]
    return None


def _walk_key_values(obj: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key).lower(), value
            yield from _walk_key_values(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_key_values(item)


def _normalize_vlan_value(value: Any) -> Optional[str]:
    if value is None or isinstance(value, (dict, list, tuple, set, bool)):
        return None

    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "n/a", "unknown"}:
        return None

    return text


def _extract_vlan_from_interface_name(value: Any) -> Optional[str]:
    if value is None or isinstance(value, (dict, list, tuple, set, bool)):
        return None

    text = str(value).strip()
    if not text:
        return None

    patterns = (
        r"(?i)^vlan[-\s_]*interface[-\s_]*(\d+)$",
        r"(?i)^vlan[-\s_]*(\d+)$",
        r"(?i)^vlan\s+(\d+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return match.group(1)

    return None


def extract_vlan_from_interface_fields(record: Any) -> str:
    """
    Extract VLAN hints from explicit interface-name fields only.

    This is safer for ARP responses, which may include unrelated nested VLAN
    fields that describe the local routed interface rather than the endpoint's
    L2 VLAN in the FDB table.
    """

    interface_keys = (
        "port",
        "ifName",
        "ifDescr",
        "ifAlias",
        "port_label",
        "portName",
        "remote_interface",
        "remote_port",
    )

    for key in interface_keys:
        value = None
        if isinstance(record, dict):
            value = record.get(key)
        if value is None:
            continue

        if isinstance(value, dict):
            nested_vlan = extract_vlan_from_interface_fields(value)
            if nested_vlan:
                return nested_vlan
            continue

        vlan = _extract_vlan_from_interface_name(value)
        if vlan:
            return vlan

    return ""


def extract_terminal_vlan(*sources: Any) -> str:
    """
    Extract the endpoint VLAN strictly from ordered LibreNMS responses.

    The first valid VLAN wins. Callers should pass the most authoritative
    source first, e.g. the matched FDB row before broader fallback records.
    """

    vlan_keys = {
        "vlan",
        "vlan_id",
        "vlan_vid",
        "vlan_vlan",
        "ifvlan",
        "dot1qvlanfdbid",
    }

    for source in sources:
        for key, value in _walk_key_values(source):
            if key in vlan_keys:
                vlan = _normalize_vlan_value(value)
                if vlan:
                    return vlan

            if key in {"ifname", "ifdescr", "ifalias", "port", "port_label", "portname"}:
                vlan = _extract_vlan_from_interface_name(value)
                if vlan:
                    return vlan

    return ""


def collect_port_vlan_values(port_info: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(port_info, dict):
        return []

    values: List[str] = []
    for item in port_info.get("vlans") or []:
        if not isinstance(item, dict):
            continue
        vlan = _normalize_vlan_value(item.get("vlan"))
        if vlan and vlan not in values:
            values.append(vlan)

    return values


def _extract_untagged_port_vlan(port_info: Optional[Dict[str, Any]]) -> str:
    if not isinstance(port_info, dict):
        return ""

    for item in port_info.get("vlans") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("untagged") or "").strip() not in {"1", "true", "True"}:
            continue

        vlan = _normalize_vlan_value(item.get("vlan"))
        if vlan:
            return vlan

    return ""


def _normalized_id(value: Any) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    return text


def _same_id(left: Any, right: Any) -> bool:
    left_value = _normalized_id(left)
    right_value = _normalized_id(right)
    if not left_value or not right_value:
        return False
    return left_value == right_value


def filter_fdb_records_by_mac(records: List[Dict[str, Any]], mac: str) -> List[Dict[str, Any]]:
    matched: List[Dict[str, Any]] = []
    normalized_target = normalize_mac(mac)

    for item in records:
        value = item.get("mac_address") or item.get("mac")
        if not value:
            continue

        try:
            normalized_value = normalize_mac(str(value))
        except ValueError:
            continue

        if normalized_value == normalized_target:
            matched.append(item)

    return matched


def build_device_vlan_map(vlan_records: List[Dict[str, Any]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}

    for item in vlan_records:
        if not isinstance(item, dict):
            continue

        vlan_row_id = _normalized_id(item.get("vlan_id"))
        vlan_number = _normalize_vlan_value(item.get("vlan_vlan") or item.get("vlan"))
        if vlan_row_id and vlan_number:
            mapping[vlan_row_id] = vlan_number

    return mapping


def resolve_fdb_vlan(
    fdb_record: Optional[Dict[str, Any]],
    device_vlans: Optional[List[Dict[str, Any]]] = None,
    port_info: Optional[Dict[str, Any]] = None,
    arp_record: Optional[Dict[str, Any]] = None,
) -> str:
    if not isinstance(fdb_record, dict):
        return ""

    explicit_vlan = _normalize_vlan_value(fdb_record.get("vlan_vlan") or fdb_record.get("vlan"))
    if explicit_vlan:
        return explicit_vlan

    vlan_map = build_device_vlan_map(device_vlans or [])
    vlan_row_id = _normalized_id(fdb_record.get("vlan_id"))
    if vlan_row_id and vlan_row_id in vlan_map:
        return vlan_map[vlan_row_id]

    port_vlans = collect_port_vlan_values(port_info)
    arp_vlan = extract_vlan_from_interface_fields(arp_record)
    if not arp_vlan:
        arp_vlan = extract_vlan_from_interface_fields(port_info)

    if arp_vlan and (not port_vlans or arp_vlan in port_vlans):
        return arp_vlan

    # If FDB already references a VLAN row but we can't map it back to a real
    # VLAN number, do not guess from the port's access/default VLAN.
    if vlan_row_id:
        return ""

    untagged_vlan = _extract_untagged_port_vlan(port_info)
    if untagged_vlan:
        return untagged_vlan

    if len(port_vlans) == 1:
        return port_vlans[0]

    if arp_vlan:
        return arp_vlan

    return extract_terminal_vlan(fdb_record, port_info)


def score_fdb_candidate(
    record: Dict[str, Any],
    preferred_port_ids: Optional[Iterable[Any]] = None,
    preferred_device_ids: Optional[Iterable[Any]] = None,
    preferred_vlans: Optional[Iterable[Any]] = None,
    candidate_vlan: Optional[str] = None,
) -> int:
    score = 0
    port_id = _normalized_id(record.get("port_id"))
    device_id = _normalized_id(record.get("device_id"))

    normalized_port_ids = {_normalized_id(value) for value in preferred_port_ids or []}
    normalized_port_ids.discard(None)
    normalized_device_ids = {_normalized_id(value) for value in preferred_device_ids or []}
    normalized_device_ids.discard(None)
    normalized_vlans = {_normalize_vlan_value(value) for value in preferred_vlans or []}
    normalized_vlans.discard(None)

    if port_id and port_id in normalized_port_ids:
        score += 1000

    if device_id and device_id in normalized_device_ids:
        score += 250

    if candidate_vlan and candidate_vlan in normalized_vlans:
        score += 100

    if port_id:
        score += 5

    return score


def pick_scored_candidate(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None

    def sort_key(item: Dict[str, Any]) -> tuple[int, str, str]:
        return (
            int(item.get("score") or 0),
            str(item.get("updated_at") or ""),
            str(item.get("port_id") or ""),
        )

    return sorted(candidates, key=sort_key, reverse=True)[0]


def pick_best_result(records: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not records:
        return None

    # 优先选 up / connected / access 相关结果，第一版简单处理
    preferred_keywords = ["access", "up", "connected"]
    for item in records:
        text = " ".join(str(v).lower() for v in item.values() if v is not None)
        if any(k in text for k in preferred_keywords):
            return item

    return records[0]
