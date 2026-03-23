# NetBox Endpoint Locator

A NetBox plugin for locating an endpoint by IP or MAC via LibreNMS ARP/FDB/Ports APIs.

## Features

- Lookup by IPv4 or MAC
- Resolve IP -> ARP -> MAC -> FDB/Port
- Show access switch and interface
- Link matched NetBox device by management IP
- Integrated into NetBox plugin menu

## Requirements
- NetBox 4.4.x（当前代码已按 `min_version=4.0.0` 兼容，但请以 4.4 验证为准）
- LibreNMS API token
- LibreNMS 已经收集了 ARP / FDB / 端口相关数据

## 安装（用户克隆后）
假设你的 NetBox 是在同一套 Python 环境中运行（即在该环境里 `pip` 就是用于 NetBox）。

1. 克隆仓库并安装插件
```bash
git clone https://github.com/Jaycelu/netbox_-endpoint_locator.git
cd netbox_-endpoint_locator
pip install -e .
```

2. 在 NetBox 配置中启用插件

在你的 `configuration.py`/`settings.py` 中设置（具体文件名以你的 NetBox 部署方式为准）：
```python
PLUGINS = ["netbox_endpoint_locator"]

PLUGINS_CONFIG = {
    "netbox_endpoint_locator": {
        # LibreNMS 地址（例如 https://librenms.example.com）
        "librenms_url": "https://librenms.example.com",
        # LibreNMS API Token（通过 LibreNMS 用户/API 页面生成）
        "librenms_token": "YOUR_TOKEN",

        # 可选：是否校验证书（LibreNMS 使用自签证书时可设为 False）
        "verify_ssl": False,

        # 可选：请求超时（秒）
        "timeout": 15,

        # 可选：是否把入口放到“顶级菜单”（默认为 False）
        "top_level_menu": False,
    }
}
```

3. 重启 NetBox
```bash
# 根据你的部署方式重启（示例）
systemctl restart netbox
# 或者 docker-compose restart
```

4. 使用插件
登录 NetBox 后，在插件菜单中进入 `Endpoint Locator`，然后访问 `Lookup` 页面查询 IP 或 MAC。

## LibreNMS 侧准备
- 你需要确保 LibreNMS 已经能通过 API 返回数据：
  - ARP：`/api/v0/resources/ip/arp/<ip>`
  - FDB：`/api/v0/resources/fdb/<mac>/detail`
  - 端口：`/api/v0/ports/mac/<mac>?filter=first`
- 若查询返回找不到接口，通常是 LibreNMS 采集的数据不包含该端点，或返回字段与你的 LibreNMS 版本不一致。