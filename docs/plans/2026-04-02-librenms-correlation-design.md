# LibreNMS Endpoint Correlation Design

## Goal

Make `IP -> MAC -> VLAN -> port` and `MAC -> IP -> VLAN -> port` resolve through the same canonical LibreNMS record so the plugin matches the LibreNMS web UI more closely.

## Problem

The previous implementation merged fields from multiple APIs:

- `resources/ip/arp/:query`
- `resources/fdb/:mac`
- `resources/fdb/:mac/detail`
- `ports/mac/:search?filter=first`

That produced plausible-looking results, but the fields were not guaranteed to come from the same `port_id` or VLAN context.

## Constraints from Official API

- `resources/ip/arp/:query` is authoritative for `ip + mac + port_id`
- `resources/fdb/:mac` is authoritative for `mac + device_id + port_id + vlan_id`
- `ports/:portid?with=device,vlans` is authoritative for interface and device metadata
- `devices/:hostname/vlans` maps LibreNMS internal `vlan_id` to human-readable `vlan_vlan`
- `ports/mac/:search?filter=first` is a ranked port search, not a canonical endpoint record

## Chosen Design

1. Query ARP first for IP lookups, and also for MAC lookups when available.
2. Use ARP `port_id` and `device_id` only as hints.
3. Query FDB by MAC and build candidates from FDB rows only.
4. For each FDB row, enrich the exact `port_id` with:
   - `ports/:portid?with=device,vlans`
   - `devices/:hostname/vlans`
5. Resolve display VLAN by mapping FDB internal `vlan_id` to device `vlan_vlan`.
6. Score candidates with strong preference for matching ARP `port_id`, then device, then VLAN/interface hints.
7. Render the final result from the winning `FDB + port` pair without cross-record field merging.

## Expected Outcome

- IP lookup and MAC lookup converge on the same `port_id`
- VLAN display uses the real device VLAN number when possible
- `fdb/detail` is no longer treated as a source of truth for correlation
