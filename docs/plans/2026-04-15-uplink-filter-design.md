# Uplink Filter Design

## Goal

Keep the existing `IP -> MAC -> FDB -> port` lookup flow, but stop selecting aggregation or interconnect ports when a terminal-facing access port is also present in LibreNMS.

## Approved Behavior

1. Use ARP primarily to translate `IP -> MAC`.
2. Treat routed ARP interfaces such as `Vlan-interface102` as context only, not as authoritative endpoint-port hints.
3. Query FDB by MAC and build all matching candidates.
4. Filter candidates in this order when alternatives exist:
   - aggregate or routed interfaces
   - uplink / trunk-like ports
   - physical ports carrying multiple VLANs or explicit trunk/hybrid mode
   - non-physical ports
5. Only after that filtered pass, score the remaining candidates and select the winner.
6. Keep topology expansion as a fallback only; missing `links` or `port_stack` data must never surface as a user-facing error.

## Safety Rules

- If filtering leaves no candidates, fall back to the unfiltered FDB set.
- If `devices/<id>/links` or `devices/<id>/port_stack` returns `404 Not Found`, treat that as absent topology data instead of a lookup failure.
- Use LibreNMS `device_id` for `devices/<id>/...` endpoints instead of hostnames or management IPs.
