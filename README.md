# NetBox Endpoint Locator

A NetBox plugin that locates the access switch/port for a given endpoint by querying LibreNMS ARP/FDB/Ports APIs.

中文文档：[`README_CN.md`](./README_CN.md)

## Features

- Lookup by IPv4 or MAC
- Resolve flow: `IP -> ARP -> MAC -> FDB/Port`
- Display VLAN from LibreNMS when available
- Identify access device/interface and optionally link it to a NetBox `Device` via management IP
- Integrated into the NetBox plugin menu

## Compatibility

- Target: NetBox `4.4.x`
- The plugin declares `min_version = 4.0.0`, but you should primarily validate on your exact NetBox version.

## Quick Start

1. Clone and install (inside the same Python environment that runs NetBox)

```bash
git clone https://github.com/Jaycelu/netbox_-endpoint_locator.git
cd netbox_-endpoint_locator
pip install -e .
```

2. Enable the plugin in your NetBox config

```python
PLUGINS = ["netbox_endpoint_locator"]

PLUGINS_CONFIG = {
    "netbox_endpoint_locator": {
        "librenms_url": "https://librenms.example.com",
        "librenms_token": "YOUR_TOKEN",
        "verify_ssl": False,  # optional
        "timeout": 15,        # optional
        "top_level_menu": False,  # optional
    }
}
```

3. Restart NetBox

```bash
# systemctl restart netbox
```

4. Use the plugin
- Go to the NetBox UI menu: `Endpoint Locator` -> `Lookup`
- Enter an `IPv4` or `MAC` to find the access switch/interface

## Configuration Guide

Add the plugin to your NetBox `configuration.py`:

```python
PLUGINS = ["netbox_endpoint_locator"]
```

Then configure LibreNMS access:

```python
PLUGINS_CONFIG = {
    "netbox_endpoint_locator": {
        "librenms_url": "https://librenms.example.com",
        "librenms_token": "YOUR_TOKEN",
        "verify_ssl": False,
        "timeout": 15,
        "top_level_menu": False,
    }
}
```

Required keys:

- `librenms_url`
- `librenms_token`

Optional keys:

- `verify_ssl`
- `timeout`
- `top_level_menu`

## LibreNMS API Usage

The plugin performs only on-demand read queries and does not do any full inventory sync by itself.

IP lookup uses:

- `GET /api/v0/resources/ip/arp/<ip>`
- `GET /api/v0/resources/fdb/<mac>/detail`
- `GET /api/v0/resources/fdb/<mac>`
- `GET /api/v0/ports/mac/<mac>?filter=first`

MAC lookup uses:

- `GET /api/v0/resources/fdb/<mac>/detail`
- `GET /api/v0/resources/fdb/<mac>`
- `GET /api/v0/ports/mac/<mac>?filter=first`

This means the plugin depends on LibreNMS already having fresh ARP, FDB, and port correlation data, but the plugin itself only asks for the exact IP or MAC being searched.

For full configuration, architecture, and troubleshooting, see: [`README_CN.md`](./README_CN.md).
