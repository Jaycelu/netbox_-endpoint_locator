# NetBox Endpoint Locator

NetBox plugin for locating the access switch, interface, VLAN, and related IP/MAC information of an endpoint by querying LibreNMS.

Current documented release: `0.4.0`

中文说明：[`README_CN.md`](./README_CN.md)

## What It Does

- Lookup by `IPv4` or `MAC`
- Correlate `IP / MAC / VLAN / port` from the same LibreNMS record chain as closely as possible
- Show switch/device name, interface, terminal VLAN, and related IPv4
- Optionally map the LibreNMS management address back to a NetBox `Device`
- Expose raw API data in the UI for troubleshooting

## Compatibility

- Tested target: NetBox `4.4.x`
- Plugin `min_version`: `4.0.0`
- Python: `>= 3.10`

## Installation

Install the plugin into the same Python environment used by NetBox:

```bash
git clone https://github.com/Jaycelu/netbox_-endpoint_locator.git
cd netbox_-endpoint_locator
pip install -e .
```

If you already installed an older version and only want to upgrade:

```bash
cd netbox_-endpoint_locator
git pull
pip install -e .
```

## NetBox Configuration

Add the plugin in `configuration.py`:

```python
PLUGINS = ["netbox_endpoint_locator"]

PLUGINS_CONFIG = {
    "netbox_endpoint_locator": {
        "librenms_url": "https://librenms.example.com",
        "librenms_token": "YOUR_TOKEN",

        # optional
        "verify_ssl": False,
        "timeout": 15,
        "top_level_menu": False,
    }
}
```

Required settings:

- `librenms_url`
- `librenms_token`

Optional settings:

- `verify_ssl`
- `timeout`
- `top_level_menu`

After updating the config, restart the NetBox web workers for your deployment model.

Examples:

```bash
sudo systemctl restart netbox netbox-rq
```

Or restart the relevant containers / Gunicorn / uWSGI processes in your environment.

## How To Use

1. Open NetBox
2. Go to `Endpoint Locator -> Lookup`
3. Enter an IPv4 address or MAC address
4. Review the returned:
   - MAC
   - related IPv4
   - LibreNMS host
   - switch/device name
   - interface
   - VLAN
   - NetBox device match

Accepted MAC input styles include plain hex, colon-separated, dash-separated, and dotted formats.

## How Correlation Works In 0.4.0

The plugin now prefers a canonical correlation path instead of mixing unrelated fields from multiple LibreNMS responses.

Starting in `0.4.0`, the plugin keeps the canonical `IP / MAC / VLAN` relationship intact, then separately tries to localize the displayed switch and interface to the nearest access-side device instead of stopping at an upstream aggregation interface when topology evidence exists.

For IP lookups:

1. Query ARP by IP
2. Extract MAC and ARP `port_id`
3. Query FDB by MAC
4. Score FDB candidates using ARP `port_id`, device, interface, and VLAN hints
5. Read the winning port details for:
   - device relation
   - VLAN relation
6. Resolve the display VLAN from LibreNMS VLAN resources

For MAC lookups:

1. Query ARP by MAC
2. Query FDB by MAC
3. Score candidates with ARP/device/interface hints
4. Enrich the selected FDB row with port and device details

This is what fixes the earlier mismatch where `IP -> MAC` looked correct but `MAC -> IP/VLAN/port` drifted to another interface.

## LibreNMS API Requirements

LibreNMS must already have fresh ARP, FDB, port, and VLAN data. The plugin only performs on-demand read queries.

Primary API endpoints used:

- `GET /api/v0/resources/ip/arp/<ip-or-mac>`
- `GET /api/v0/resources/fdb/<mac>`
- `GET /api/v0/resources/fdb/<mac>/detail`
- `GET /api/v0/ports/<port_id>?with=device`
- `GET /api/v0/ports/<port_id>?with=vlans`
- `GET /api/v0/resources/vlans?hostname=<device>`

## NetBox Device Matching

The plugin tries to match the LibreNMS management address back to NetBox by looking for a NetBox device whose `primary_ip4` matches the LibreNMS management IP / hostname field.

If the NetBox device is shown as `Unmatched`, verify that:

- LibreNMS is returning the management IP you expect
- the NetBox device has the correct `primary_ip4`
- both systems refer to the same management address

## Troubleshooting

If switch/device name is empty:

- LibreNMS may not be returning the device relation on that port
- open the `Raw Response` section in the plugin UI and inspect `port` and `detail`

If VLAN is empty:

- LibreNMS may not expose a resolvable FDB `vlan_id -> vlan_vlan` mapping for that device
- the plugin intentionally avoids guessing a wrong default VLAN

If `MAC -> IP` is empty:

- LibreNMS may not currently have an ARP entry for that MAC
- the FDB match can still succeed even if ARP is stale or missing

If no interface is found:

- verify that LibreNMS web UI can see the endpoint in ARP/FDB tables first
- then compare the plugin `Raw Response` output with the corresponding LibreNMS page

## Repository Notes

- Package metadata version: `0.4.0`
- Plugin config class: [`netbox_endpoint_locator/__init__.py`](./netbox_endpoint_locator/__init__.py)
- Detailed Chinese deployment and troubleshooting guide: [`README_CN.md`](./README_CN.md)
