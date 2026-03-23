# NetBox Endpoint Locator

A NetBox plugin that locates the access switch/port for a given endpoint by querying LibreNMS ARP/FDB/Ports APIs.

中文文档：[`README_CN.md`](./README_CN.md)

## Features

- Lookup by IPv4 or MAC
- Resolve flow: `IP -> ARP -> MAC -> FDB/Port`
- Identify access device/interface and optionally link it to a NetBox `Device` via management IP
- Integrated into the NetBox plugin menu

## Compatibility

- Target: NetBox `4.4.x`
- The plugin declares `min_version = 4.0.0`, but you should primarily validate on your exact NetBox version.

## Quick Start

See: [`README_CN.md`](./README_CN.md) for the full install/config steps and architecture details.