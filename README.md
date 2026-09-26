# Nexus-panel

轻量自托管转发控制台。UI 延续 [NexusGate-Sub](https://github.com/a2899882/NexusGate-Sub) 的浅色侧栏与蓝色风格；[Realm](https://github.com/zhboner/realm) 执行直连 TCP/UDP 规则，内置 TLS TCP 隧道执行带计量的三节点转发。面板使用 Python 标准库和 SQLite；节点主动向面板拉取配置，不开放管理 API。

## 功能

- 服务器：一次性安装码与一条 SSH 命令安装、心跳、进程状态、错误日志、凭证重置。
- 直连规则：Realm TCP/UDP、启停、分组与搜索、最多 8 个附加目标、轮询和 IP 哈希。
- 三节点隧道与转发：客户端 → 前置入口 → TLS 中转 → TLS 落地 → 目标。一条隧道可承载多条转发，每条转发有独立入口端口和一个或多个目标；多目标按连接轮询。逐跳验证证书 SHA-256 指纹及隧道密钥。此模式仅支持 TCP。
- 隧道账号：创建隧道时可自动生成账号；每个账号绑定一条隧道，多条转发共用其 GB 配额、计费倍率和 Mbps 限速。入口按上行、下行或双向统计应用数据字节，可配置参考单价（¥/GB）并展示累计金额；用量落地 SQLite，面板按累计高水位去重。
- 运维：Caddy 自动 HTTPS 域名反代、systemd、SSH 菜单更新/域名/密码/备份/恢复/日志、管理员后台改账号密码。

不包含独立订阅、支付结算、UDP 三节点隧道或自动故障切换。Realm 直连规则不参与隧道账号计量。

## 一键安装面板

支持 Debian 12 / Ubuntu 22.04+、systemd、root、域名解析到面板及可访问 GitHub。面板主机开放 80/443；各转发端口需在系统和云防火墙单独放行。

```bash
curl -fsSL https://raw.githubusercontent.com/a2899882/Nexus-panel/main/scripts/install.sh -o /root/nexus-panel-install.sh
sudo bash /root/nexus-panel-install.sh
```

安装器询问域名、管理员账号与密码；密码留空时随机生成并显示一次。面板仅监听 `127.0.0.1:8765`，Caddy 管理 HTTPS 反代且不覆盖其他站点。没有固定默认密码。

## 节点与转发

1. 在“节点监控”添加三台节点，填写节点间可互通的 IP 或域名。点击“安装/升级”，复制弹窗中的**整条**命令，在各节点 SSH 终端运行。命令自动包含面板地址和 30 分钟有效的一次性安装码。
2. 安装器拉取 Realm 官方 v2.9.6 的 glibc 2.28 兼容构建并校验发布 API 的 SHA-256 摘要；在领取节点凭证前先执行二进制兼容检查。节点以专用用户运行。约 5–10 秒后面板显示实际进程状态；旧节点也应重新执行“安装/升级”命令，以替换之前不兼容的 Realm 二进制。
3. 在“隧道管理”选择前置、中转、落地三台节点，账号可选择“自动创建”。在“转发管理”选择隧道，输入落地目标，入口端口可留空自动分配；同一隧道可继续添加其他转发。在“账号与配额”按需修改配额、倍率、参考单价和共享限速。单机直连 TCP/UDP 则在“Realm 直连”配置。

客户端连接**前置节点地址:入口端口**；前置连接中转、中转连接落地、落地连接目标。节点间地址必须从上一节点可达。转发列表显示每条规则的入口、中转和落地 TCP 端口，分别在系统及云防火墙放行；建议中转仅放行前置来源、落地仅放行中转来源。端口自动分配仅排除面板已管理的冲突，若被其他进程占用，节点会报告启动错误。

配额按入口实际转发的应用数据字节计，不含 TLS/TCP/IP 开销。倍率乘以选定方向的用量；参考金额由计费用量乘以单价计算，不代表已收款。`0` GB 和 `0` Mbps 分别表示不限配额与不限速。限速对同一账号的入口连接共享。账号名称是管理标签，不是客户登录凭证。

## SSH 菜单、备份与迁移

在面板服务器运行 `sudo nexus-panel`，可查看服务、更新面板、修改域名、重置密码、备份/恢复、查看面板与本机节点日志。备份保存在 `/root/nexus-panel-backup-日期.tar.gz`，包括数据库、域名和 `master.key` 隧道主密钥；妥善保管。

迁移时先在新服务器安装面板，再恢复备份；节点需要把 `/var/lib/nexus-agent/agent.env` 的 `NEXUS_PANEL_URL` 改为新域名并重启服务。缺少 `master.key` 会改变既有隧道的认证密钥。面板升级后，已有节点从服务器列表的“安装”按钮重新生成命令，在原节点 SSH 执行即可升级代理；领取安装码会轮换该节点凭证。

```bash
sudo systemctl status nexus-panel caddy nexus-agent
sudo journalctl -u nexus-agent -n 80 --no-pager
```

## 实现边界

- Realm 与 GOST 没有脱离线路和负载的通用性能排名；要在相同链路实测吞吐与延迟。三节点隧道使用独立的 TLS TCP 转发进程，不把 Realm 的直连能力误当成逐账号计费。
- 配置变化可能重启对应进程并中断既有连接。Realm 配置失败会回滚并定期重试；隧道启动错误显示在节点状态，持续重试。面板的“运行中”依赖节点心跳和进程报告，不等于公网防火墙及目标服务可达。
- 在线是最近 20 秒节点有心跳；连通诊断是面板到目标的 TCP 建连时间，不代表完整三跳延迟。
- 旧代理丢失认证后仍可能运行最后配置；轮换凭证时必须在节点重新运行安装命令或停用服务。删除节点前先删除关联规则和隧道。
- SQLite 只记录累计流量，不记录转发内容。操作记录默认保留 30 天。没有客户角色、金额账单或支付接口。

## 开发验证

```bash
NEXUS_DB=./data/panel.db NEXUS_ADMIN_USER=admin NEXUS_ADMIN_PASSWORD='a-long-random-password' python3 panel.py bootstrap
NEXUS_DB=./data/panel.db python3 panel.py serve
python3 -m unittest discover -s tests -v
node --check web/app.js
bash -n scripts/*.sh
```

代码按 MIT 许可证发布。UI 与功能方向参考 [NexusGate-Sub](https://github.com/a2899882/NexusGate-Sub)、[realm-xwPF](https://github.com/zywe03/realm-xwPF) 和 [flux-panel](https://github.com/bqlpfy/flux-panel)，未复制其业务代码。
