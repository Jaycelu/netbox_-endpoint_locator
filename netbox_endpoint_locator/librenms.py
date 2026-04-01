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
    for key in ("arp", "ports_fdb", "port", "ports", "data", "items", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
    if isinstance(data, list):
        return data
    return []


def lookup_arp_by_ip(ip: str) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/resources/ip/arp/{ip}"))


def lookup_fdb_by_mac(mac: str) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/resources/fdb/{mac}"))


def lookup_fdb_detail_by_mac(mac: str) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/resources/fdb/{mac}/detail"))


def lookup_device_fdb(device: Any) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/devices/{quote(str(device), safe='')}/fdb"))


def lookup_port_by_mac(mac: str) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/ports/mac/{mac}?filter=first"))


def lookup_port_by_id(port_id: Any, with_relations: Optional[Sequence[str]] = None) -> Optional[Dict[str, Any]]:
    query = ""
    if with_relations:
        allowed = [str(item).strip() for item in with_relations if str(item).strip()]
        if allowed:
            query = f"?with={','.join(allowed)}"

    records = _records(_get(f"/api/v0/ports/{port_id}{query}"))
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


def pick_fdb_record(
    records: List[Dict[str, Any]],
    preferred_device_id: Any = None,
    preferred_vlan: Optional[str] = None,
    port_records: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    if not records:
        return None

    normalized_vlan = _normalize_vlan_value(preferred_vlan)

    if preferred_device_id and normalized_vlan:
        for item in records:
            if _same_id(item.get("device_id"), preferred_device_id) and _same_id(item.get("vlan_id"), normalized_vlan):
                return item

    if normalized_vlan:
        for item in records:
            if _same_id(item.get("vlan_id"), normalized_vlan):
                return item

    if preferred_device_id:
        for item in records:
            if _same_id(item.get("device_id"), preferred_device_id):
                return item

    if port_records:
        for port in port_records:
            preferred_port_id = port.get("port_id")
            if not preferred_port_id:
                continue
            for item in records:
                if _same_id(item.get("port_id"), preferred_port_id):
                    return item

        for port in port_records:
            preferred_port_device_id = port.get("device_id")
            if not preferred_port_device_id:
                continue
            for item in records:
                if _same_id(item.get("device_id"), preferred_port_device_id):
                    return item

    return records[0]


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
