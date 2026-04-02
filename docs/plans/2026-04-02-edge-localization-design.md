# Edge Localization Design

## Goal

Preserve the existing correct `IP / MAC / VLAN` correlation while improving the displayed switch and interface so the plugin prefers the nearest access-side device instead of stopping at a core or distribution aggregate interface.

## Non-Goals

- Do not change the canonical `IP <-> MAC` relationship.
- Do not change the canonical VLAN chosen from LibreNMS FDB evidence.
- Do not guess when topology is ambiguous.

## Two-Layer Result Model

### 1. Canonical Result

This is the existing truth layer produced from ARP + FDB + VLAN evidence.

Fields owned by this layer:

- query
- mac
- related ip
- vlan
- canonical device_id
- canonical port_id
- raw supporting evidence

This layer must remain unchanged by any access-localization logic.

### 2. Edge Localized Result

This is a display-only optimization layer.

Fields owned by this layer:

- displayed LibreNMS host
- displayed switch/device
- displayed interface

It is allowed to override only when the downstream candidate:

- has the same MAC
- has the same VLAN as the canonical result
- is connected through explicit topology evidence
- wins the access-side ranking without ambiguity

## Candidate Collection

For one lookup, collect all matching FDB candidates for the same MAC, then enrich each candidate with:

- port info
- device name / hostname
- interface name / description
- device links
- device port-stack mappings

## Topology Rules

### Aggregate Expansion

If a candidate port is a virtual aggregate such as `Bridge-Aggregation`, expand it using `port_stack`:

- `port_id_high` is the aggregate port
- `port_id_low` are member ports

Candidate topology checks must evaluate both the aggregate port and its active member ports.

### Downstream Relationship

Use LibreNMS `device links` data to create candidate-to-candidate downstream relationships.

If candidate A is learned on a port (or aggregate members) that links to candidate device B, then treat that as:

`A -> B`

Meaning A is more upstream and B is more downstream.

## Selection Strategy

1. Build all candidates that share the canonical MAC and canonical VLAN.
2. Build downstream relationships between candidates.
3. Prefer candidates reachable downstream from the canonical candidate.
4. Prefer leaf candidates with no further downstream candidate.
5. Among leaf candidates, prefer:
   - non-aggregate ports
   - physical ports
   - non-uplink-like descriptions
6. If multiple candidates remain tied, fall back to the canonical display.

## Safety Rule

If access localization is incomplete or ambiguous, keep showing the canonical device and interface.

This ensures the new feature can improve display accuracy without breaking the already-correct `IP / MAC / VLAN` result.
