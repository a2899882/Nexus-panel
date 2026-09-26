# Nexus-panel

面向自托管中转服务器的轻量转发控制台。沿用 [NexusGate-Sub](https://github.com/a2899882/NexusGate-Sub) 的浅色侧栏与蓝色视觉风格，使用 [Realm](https://github.com/zhboner/realm) 执行 TCP / UDP 端口转发。控制端基于 Python 标准库和 SQLite；节点主动拉取规则，无需在节点开放管理端口。

> 当前版本是 **端口转发版**。不包含独立订阅、用户售卖、隧道账号、流量计费、流量配额、限速、加密隧道或故障自动切换。Realm 本身不提供本项目可直接使用的逐账号流量计量接口，因此不会把这些限制伪装成已生效。

## 已实现

- 多服务器：名称、分组、展示地址、心跳、运行错误、节点密钥轮换。
- 转发规则：TCP、UDP 或两者；启停、编辑、删除；监听端口冲突检查；可添加最多 8 个附加出口并选择轮询或 IP 哈希分配。
- 管理体验：搜索、分组筛选、服务器筛选、每页 30 条；保存后自动刷新，状态每 10 秒更新。
- 连通诊断：从**面板服务器**到目标的 TCP 建连耗时，130 ms 以下绿色，其余黄色，不可达红色；最多 6 个并发批量探测。UDP 不用 TCP 探测冒充联通结果。
- 安全：安装时设置初始密码（留空随机生成）、PBKDF2 哈希、HttpOnly 会话、同站请求校验、登录尝试限制、节点密钥仅展示一次且数据库只保存哈希。
- 运维：Caddy 自动 HTTPS 域名反代、systemd 服务、SSH 菜单更新/域名/密码/备份/恢复、操作记录保留天数设置。
- Realm 配置失败时节点回滚到上一个可运行的配置，并在面板显示错误。面板暂时失联时节点继续运行已有配置。

## 安装面板

支持 Debian 12 / Ubuntu 22.04+ 的 systemd 主机，需有 root、可访问 GitHub 和能解析到该服务器的域名。开放 80/443 供 Caddy 申请证书；中转端口另在云防火墙中放行。

```bash
curl -fsSL https://raw.githubusercontent.com/a2899882/Nexus-panel/main/scripts/install.sh -o /root/nexus-panel-install.sh
sudo bash /root/nexus-panel-install.sh
```

安装器询问域名、管理员账号、密码；留空密码将随机生成并在终端显示一次。应用只监听 `127.0.0.1:8765`。Caddy 使用独立的 `/etc/caddy/nexus-panel.caddy`，并在已有 Caddyfile 加一行 `import`，不覆盖其他站点。无内置默认密码。

## 添加节点与规则

1. 登录面板，在“服务器”添加服务器。复制弹窗中的一次性节点密钥。
2. 在**该服务器**的 SSH 终端运行：

   ```bash
   curl -fsSL https://raw.githubusercontent.com/a2899882/Nexus-panel/main/scripts/install-agent.sh -o /root/nexus-agent-install.sh
   sudo bash /root/nexus-agent-install.sh
   ```

3. 输入面板的 `https://` 地址和节点密钥。安装器从 Realm 官方 v2.9.6 发布页下载相应架构的二进制，并与官方发布 API 提供的 SHA-256 摘要核对。支持 x86_64 / aarch64。
4. 在“转发规则”选择服务器、监听 IP/端口、目标地址、协议并保存。节点约每 5 秒拉取一次。公网防火墙需要放行规则的 TCP/UDP 端口。

节点服务以专用 `nexus-agent` 用户运行；其 Realm 子进程只获得绑定低端口所需的 `CAP_NET_BIND_SERVICE`。控制端以独立 `nexus-panel` 用户运行。

## SSH 菜单与迁移

在面板主机运行 `sudo nexus-panel`。菜单提供状态、重启、更新、域名修改、密码重置、备份、恢复和日志。备份以压缩包写到 `/root/nexus-panel-backup-日期.tar.gz`。迁移时在新主机先执行一键安装、输入新域名，然后将压缩包放到 `/root` 并在菜单选择“恢复备份”。恢复数据库后原节点仍能通过原密钥认证，但每个节点的 `NEXUS_PANEL_URL` 需改成新域名，再重启 `nexus-agent`。节点的加密密钥无法从哈希逆推出；丢失明文时可在面板轮换该节点的密钥。旧代理在失去认证后仍会运行最后一次成功下发的规则，需在节点更新密钥或停用服务。删除服务器前，面板要求先删除所有规则并等待节点确认空配置。

```bash
sudo systemctl status nexus-panel caddy
sudo journalctl -u nexus-panel -n 50 --no-pager
sudo journalctl -u nexus-agent -n 50 --no-pager
```

## 设计边界

- **Realm 与 GOST**：没有适用于所有网络的性能排名。Realm 适合此版本的直接端口转发；GOST 的协议与链式代理功能更广。真实性能应在相同线路、并发和协议下实测。
- **状态**：在线表示节点最近 20 秒有心跳；运行中表示配置已应用。连通诊断测的是面板到目标的 TCP 建连，不是用户端的实际延迟。
- **配置发布**：每台代理维护一份 Realm JSON 配置，变化时短暂重启 Realm，因此正在转发的连接可能中断。失败会尝试回滚。较大规模或零中断切换需要进一步设计。
- **存储**：SQLite 存服务器/规则/账号哈希/密钥哈希/操作记录，不记录转发内容。事件按设置天数自动删除，默认 30 天。systemd journal 的大小由主机 journald 配置控制。备份包含数据库，应私密保存。
- **版本范围**：面板和安装器支持 Debian/Ubuntu + systemd；节点要求能访问面板 HTTPS 地址。这个版本没有做流量配额、计费或多用户权限。

## 开发与验证

```bash
NEXUS_DB=./data/panel.db NEXUS_ADMIN_USER=admin NEXUS_ADMIN_PASSWORD='a-long-random-password' python3 panel.py bootstrap
NEXUS_DB=./data/panel.db python3 panel.py serve
python3 -m unittest discover -s tests -v
node --check web/app.js
bash -n scripts/*.sh
```

项目自身代码按 MIT 许可证发布。UI 风格参考 MIT 许可的 NexusGate-Sub；借鉴了 [realm-xwPF](https://github.com/zywe03/realm-xwPF) 的规则管理方向和 [flux-panel](https://github.com/bqlpfy/flux-panel) 的多服务器转发管理思路，未复制其业务代码。
