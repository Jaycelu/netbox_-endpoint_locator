# NetBox Endpoint Locator

A NetBox plugin for locating an endpoint by IP or MAC via LibreNMS ARP/FDB/Ports APIs.

## Features

- Lookup by IPv4 or MAC
- Resolve IP -> ARP -> MAC -> FDB/Port
- Show access switch and interface
- Link matched NetBox device by management IP
- Integrated into NetBox plugin menu

## Requirements

- NetBox 4.x
- LibreNMS API token
- LibreNMS has already polled ARP/FDB data