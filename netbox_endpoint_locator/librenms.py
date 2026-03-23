import ipaddress
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

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
    for key in ("arp", "ports_fdb", "ports", "data", "items", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    if isinstance(data, list):
        return data
    return []


def lookup_arp_by_ip(ip: str) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/resources/ip/arp/{ip}"))


def lookup_fdb_detail_by_mac(mac: str) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/resources/fdb/{mac}/detail"))


def lookup_port_by_mac(mac: str) -> List[Dict[str, Any]]:
    return _records(_get(f"/api/v0/ports/mac/{mac}?filter=first"))


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