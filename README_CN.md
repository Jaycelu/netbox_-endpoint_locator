# NetBox Endpoint Locator（LibreNMS 终端定位插件）

这是一个基于 LibreNMS API 的 NetBox 插件，用来按 `IP` 或 `MAC` 查询终端所在的交换机、接口、VLAN，以及关联的 IPv4 信息。

当前文档对应版本：`0.4.1`

英文首页入口：[`README.md`](./README.md)

---

## 1. 功能概览

- 支持输入 `IPv4` 或 `MAC`
- 查询结果尽量与 LibreNMS Web 前端保持一致
- 显示：
  - MAC
  - 关联 IPv4
  - LibreNMS 主机
  - 交换机/设备名
  - 接口
  - VLAN
  - NetBox 设备匹配结果
- 页面支持展开“原始返回”查看 API 原始数据，便于排错

从 `0.4.1` 开始，插件除了保持 `IP / MAC / VLAN` 的闭环正确性之外，还会额外尝试把展示位置收敛到“最靠近终端接入层”的交换机和接口，而不是停留在核心或汇聚设备的聚合口上；当 LLDP 或端口聚合拓扑数据缺失时，还会利用上联接口描述里显式出现的下游设备信息进行回退识别。

---

## 2. 适用环境

- NetBox：建议 `4.4.x`
- 插件声明最小版本：`4.0.0`
- Python：`>= 3.10`

如果你的 NetBox 不是 `4.4.x`，也可以使用，但建议先在测试环境验证。

---

## 3. 安装插件

### 3.1 首次安装

请在 **NetBox 使用的同一个 Python 环境** 中安装：

```bash
git clone https://github.com/Jaycelu/netbox_-endpoint_locator.git
cd netbox_-endpoint_locator
pip install -e .
```

### 3.2 升级到最新版

如果你已经装过旧版本，可以这样升级：

```bash
cd netbox_-endpoint_locator
git pull
pip install -e .
```

如果你使用的是虚拟环境，请先激活 NetBox 对应的虚拟环境后再执行以上命令。

---

## 4. 在 NetBox 中启用插件

编辑 NetBox 的 `configuration.py`：

```python
PLUGINS = ["netbox_endpoint_locator"]

PLUGINS_CONFIG = {
    "netbox_endpoint_locator": {
        "librenms_url": "https://librenms.example.com",
        "librenms_token": "YOUR_TOKEN",

        # 可选项
        "verify_ssl": False,
        "timeout": 15,
        "top_level_menu": False,
    }
}
```

### 必填项

- `librenms_url`
- `librenms_token`

### 可选项

- `verify_ssl`
  - LibreNMS 使用自签证书时常见
- `timeout`
  - API 请求超时时间，单位秒
- `top_level_menu`
  - 是否放到顶层菜单

插件缺少必填项时，会在请求阶段给出明确错误，而不是在 NetBox 启动时直接崩掉。

---

## 5. 重启 NetBox

改完配置或升级插件后，需要重启 NetBox 服务。

常见 systemd 示例：

```bash
sudo systemctl restart netbox netbox-rq
```

如果你使用 Docker、Gunicorn、uWSGI 或其他部署方式，请重启对应的 Web / worker 进程。

---

## 6. 使用方法

1. 登录 NetBox
2. 打开菜单：`Endpoint Locator -> Lookup`
3. 输入一个 IPv4 地址或 MAC 地址
4. 查看查询结果

支持的 MAC 输入形式包括：

- `9ce89518ffd6`
- `9c:e8:95:18:ff:d6`
- `9c-e8-95-18-ff-d6`
- `9ce8.9518.ffd6`

---

## 7. 0.4.1 之后的查询逻辑

这是这次文档里最重要的部分，因为它解释了为什么现在结果更准确。

### 7.1 设计原则

插件现在优先围绕 **同一条 LibreNMS FDB 记录** 完成闭环关联，而不是把多个 API 返回中“看起来像对的字段”拼成一个结果。

换句话说，最终展示的：

- 交换机/设备
- 接口
- VLAN
- 关联 IP

会尽量来自同一条命中的 `FDB + Port` 关联链路。

### 7.2 输入 IP 时

查询主线：

`IP -> ARP -> MAC -> FDB -> Port -> VLAN`

具体过程：

1. 先查 `ARP`
2. 从 ARP 中拿到：
   - `mac_address`
   - `port_id`
3. 再按 MAC 查 `FDB`
4. 用 ARP 的 `port_id / device / 接口线索 / VLAN 线索` 给 FDB 候选打分
5. 选中最可信的一条 FDB 记录
6. 再按这条 FDB 的 `port_id` 读取端口详情和设备关系
7. 最后解析真实 VLAN 并展示

### 7.3 输入 MAC 时

查询主线：

`MAC -> ARP -> FDB -> Port -> VLAN`

具体过程：

1. 先按 MAC 查 `ARP`
2. 再按 MAC 查 `FDB`
3. 用 ARP 返回的端口/设备/IP 作为上下文
4. 对 FDB 候选排序
5. 再补端口详情和设备名

这就是为什么现在 `MAC 反查 IP`、`IP 反查 MAC`、`VLAN`、`接口` 能更一致。

---

## 8. 插件实际调用的 LibreNMS API

插件只做“实时按需查询”，不会自己做全量同步。

### 8.1 核心接口

- `GET /api/v0/resources/ip/arp/<ip-or-mac>`
  - 查询 ARP
  - 既可以按 IP 查，也可以按 MAC 查

- `GET /api/v0/resources/fdb/<mac>`
  - 查询某个 MAC 的 FDB 记录

- `GET /api/v0/resources/fdb/<mac>/detail`
  - 查询可读化的 FDB 详情
  - 主要用来补设备名、接口名

- `GET /api/v0/ports/<port_id>?with=device`
  - 查询端口所属设备

- `GET /api/v0/ports/<port_id>?with=vlans`
  - 查询端口的 VLAN 关联

- `GET /api/v0/resources/vlans?hostname=<device>`
  - 用来把 FDB 的内部 `vlan_id` 映射成可读的真实 VLAN 号

### 8.2 为什么不再依赖 `ports/mac` 作为真相来源

早期版本容易把：

- `ARP`
- `FDB`
- `FDB detail`
- `ports/mac`

的字段混在一起用。

但 LibreNMS 的 `ports/mac/:search?filter=first` 更像是“候选端口搜索结果”，并不适合直接当最终真相来源。  
现在插件会把它从主逻辑中降级，避免出现：

- `IP 查出来接口 A`
- `MAC 反查却跳到接口 B`
- VLAN 也跟着错

---

## 9. NetBox 设备匹配规则

插件会尝试把 LibreNMS 返回的管理地址匹配到 NetBox 的 `Device.primary_ip4`。

所以如果你希望页面里能看到“NetBox 设备”链接，需要保证：

- LibreNMS 返回的是设备管理 IP 或可映射到管理 IP 的 hostname
- NetBox 对应设备设置了正确的 `primary_ip4`

如果页面显示“未匹配”，通常说明两边引用的管理地址并不是同一个值。

---

## 10. 常见问题

### 10.1 查到了接口，但“交换机/设备”为空

常见原因：

- LibreNMS 对该端口没有返回 `device` 关系
- 只有 FDB 记录，没有完整设备信息

插件现在会优先用端口 `device` 关系，再用 `fdb/detail` 兜底补名字。

### 10.2 VLAN 为空

这是刻意设计的保守行为，不一定是 bug。

如果 LibreNMS 能返回 FDB 记录，但无法把内部 `vlan_id` 映射成真实 VLAN 号，插件现在会优先留空，而不是错误显示成默认 VLAN `1`。

这样虽然少了一些“猜测结果”，但不会误导你。

### 10.3 MAC 能查到接口，但查不到关联 IP

常见原因：

- LibreNMS 当前没有这条 MAC 的 ARP 数据
- FDB 还在，但 ARP 已经过期

这种情况下插件仍然可以定位交换机和接口，但“关联 IPv4”可能为空。

### 10.4 查不到 NetBox 设备

请检查：

- NetBox 设备是否设置了 `primary_ip4`
- 这个 `primary_ip4` 是否就是 LibreNMS 管理地址
- LibreNMS 返回的 hostname 是否真的是管理地址

### 10.5 页面能打开，但菜单里没有 `Endpoint Locator`

升级代码后请确认：

- 插件已加入 `PLUGINS`
- `configuration.py` 已生效
- NetBox 服务已重启

---

## 11. 排错建议

如果你发现某条数据和 LibreNMS Web 前端仍然对不上，最有效的方式是：

1. 在插件页面展开“原始返回”
2. 把同一个终端在 LibreNMS Web 的：
   - ARP Table
   - FDB Table
   - Ports
3. 三者对照起来看

通常只要拿到这两边的原始数据，就能很快看出是：

- LibreNMS 数据本身不同步
- 某台设备没有 VLAN 映射
- 某条 ARP 已经过期
- 某个字段名称和预期不一致

---

## 12. 仓库内关键文件

- [`netbox_endpoint_locator/__init__.py`](./netbox_endpoint_locator/__init__.py)
  - 插件配置入口，版本号、基础元信息都在这里
- [`netbox_endpoint_locator/librenms.py`](./netbox_endpoint_locator/librenms.py)
  - LibreNMS API 封装与查询优选逻辑
- [`netbox_endpoint_locator/views.py`](./netbox_endpoint_locator/views.py)
  - 页面请求处理与结果组装
- [`netbox_endpoint_locator/templates/netbox_endpoint_locator/lookup.html`](./netbox_endpoint_locator/templates/netbox_endpoint_locator/lookup.html)
  - 查询页面模板

---

如果你后面还想补：

- 批量查询
- 候选结果列表
- 结果导出
- 更细粒度的 LibreNMS 调试信息

也可以在这个版本基础上继续扩展。
