# NetBox Endpoint Locator（LibreNMS 端口定位插件）

通过查询 LibreNMS 的 ARP / FDB / 端口相关 API，在 NetBox 中为给定的 **IP 或 MAC** 找到对应的 **接入交换机/接口**，并尽可能关联到 NetBox 里的 `Device`。

英文首页入口：[README.md](./README.md)

---

## 功能概览

- 输入 `IPv4` 或 `MAC`
- 查询链路：`IP -> ARP -> MAC -> FDB/端口`
- 输出：交换机/设备名、接口、VLAN（若可获取）
- 可根据“管理 IP”匹配到 NetBox 中的 `Device`
- 集成到 NetBox 插件菜单

---

## 架构说明

### 1) 组件划分

- `netbox_endpoint_locator/__init__.py`
  - 负责 `PluginConfig` 注册（**必须在此文件中定义**，NetBox 才能正确解析默认的 `navigation.menu` / `navigation.menu_items` 路径）、`PLUGINS_CONFIG` 必填项、base_url 等
- `netbox_endpoint_locator/navigation.py`
  - 定义插件菜单项 `Endpoint Locator -> Lookup`
- `netbox_endpoint_locator/urls.py`
  - 映射路由到视图：`lookup/`
- `netbox_endpoint_locator/views.py`
  - 处理请求：解析表单输入（IP/MAC）、调用 LibreNMS 查询逻辑、组装结果并渲染模板
- `netbox_endpoint_locator/forms.py`
  - 定义输入表单（`q`：IP 或 MAC）
- `netbox_endpoint_locator/librenms.py`
  - 封装 LibreNMS API 请求与结果处理逻辑（包括 IP/MAC 判断、MAC 归一化、结果优选）
- `netbox_endpoint_locator/templates/netbox_endpoint_locator/lookup.html`
  - 页面展示查询表单与结果（包含原始返回的 JSON 预览）

### 2) 数据流（Data Flow）

```text
用户在 NetBox UI 输入 IP/MAC
        |
        v
views.py 解析 q 的类型
  - IP: 调用 lookup_arp_by_ip
        从 ARP 记录中提取 MAC
        再调用 lookup_fdb_detail_by_mac
        若 FDB 无结果，再调用 lookup_port_by_mac
  - MAC: 直接调用 lookup_fdb_detail_by_mac
        若 FDB 无结果，再调用 lookup_port_by_mac
        |
        v
librenms.py 返回“候选结果列表”
        |
        v
views.py 使用 pick_best_result 选择最佳候选
        |
        v
views.py 尝试按“管理 IP”匹配 NetBox Device
        |
        v
渲染 lookup.html 展示接口/VLAN/设备信息
```

---

## LibreNMS API 前置条件

你需要确保 LibreNMS 已经能通过 API 返回以下数据（并且数据字段能被插件代码正确解析）：

- ARP：
  - `/api/v0/resources/ip/arp/<ip>`
- FDB：
  - `/api/v0/resources/fdb/<mac>/detail`
- 端口（按 MAC 查询）：
  - `/api/v0/ports/mac/<mac>?filter=first`

如果查询返回“找不到接口”，通常原因包括：

- LibreNMS 尚未采集/同步该终端相关的 ARP/FDB/端口数据
- LibreNMS 返回字段结构与插件的 key 假设不一致（不同版本可能字段名不同）

---

## 配置项（NetBox）

在你的 NetBox 配置中设置：

```python
PLUGINS = ["netbox_endpoint_locator"]

PLUGINS_CONFIG = {
    "netbox_endpoint_locator": {
        "librenms_url": "https://librenms.example.com",
        "librenms_token": "YOUR_TOKEN",

        # 可选
        "verify_ssl": False,   # LibreNMS 使用自签证书时常见
        "timeout": 15,         # 请求超时时间（秒）
        "top_level_menu": False,
    }
}
```

插件在缺少必填项时会在请求阶段给出清晰的错误信息（避免 import 阶段直接导致插件整体不可用）。

---

## 安装与启用（面向用户）

假设你的 NetBox 是在同一套 Python 环境中运行（在该环境里使用 `pip` 安装即可）。

1. 克隆仓库并安装插件

```bash
git clone https://github.com/Jaycelu/netbox_-endpoint_locator.git
cd netbox_-endpoint_locator
pip install -e .
```

2. 在 NetBox 启用插件并填写 `PLUGINS_CONFIG`

参照上面的“配置项（NetBox）”。

3. 重启 NetBox

示例：

```bash
systemctl restart netbox
```

4. 使用插件

- 登录 NetBox UI
- 在菜单中进入：`Endpoint Locator` -> `Lookup`
- 输入 IP 或 MAC 即可查询

---

## 兼容性与版本建议

- 目标：NetBox `4.4.x`
- 插件在 `__init__.py` 的 `PluginConfig` 中声明 `min_version = 4.0.0`
- 但实际建议以你运行的具体版本进行验证

你当前 NetBox 是 `v4.4.10`：建议优先按该版本验证。

---

## 常见问题（Troubleshooting）

1. 页面提示“未配置 PLUGINS_CONFIG / 缺少 librenms_url 或 librenms_token”
   - 检查 NetBox 的 `PLUGINS_CONFIG['netbox_endpoint_locator']` 是否已填写

2. 查到了 MAC，但无法定位到交换机接口
   - 通常是 LibreNMS FDB/端口数据中没有候选，或字段结构无法被插件优选逻辑命中
   - 可以在页面的“原始返回”中查看 `raw` / `raw_pretty`（页面会展示 JSON）

3. 匹配不到 NetBox Device（“未匹配”）
   - 插件会尝试用“管理 IP”匹配 `primary_ip4`
   - 确保 NetBox 设备的 `primary_ip4` 与 LibreNMS 的 `hostname`/管理 IP 关联方式一致

4. 能通过 `/plugins/endpoint-locator/lookup/` 打开页面，但左侧主导航没有 “Endpoint Locator”
   - 若 `PluginConfig` 写在单独的 `plugin.py` 里，NetBox 会按 `netbox_endpoint_locator.plugin.navigation` 去找 `navigation.py`，路径错误会导致**菜单永远不注册**（URL 仍可能正常）。
   - 本仓库已改为在包根目录的 `__init__.py` 中定义 `PluginConfig`（与官方 Diode 插件一致）。更新代码后请**重启 NetBox**。

