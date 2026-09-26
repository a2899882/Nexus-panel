# Nexus-panel

以 [flux-panel](https://github.com/bqlpfy/flux-panel) 的面板端源码为基底维护的自托管转发面板。保留原有 React 页面、Spring Boot API、登录、用户、隧道、转发、限速、流量统计和计费功能。新增 Nexus-panel 品牌、源码构建的一键安装、Caddy HTTPS 域名反代，以及含升级、备份、恢复和卸载的 `mb` SSH 管理菜单。上游代码基线和许可见 [NOTICE](NOTICE) 与 [LICENSE](LICENSE)。

## 转发引擎与节点

原版面板通过 GOST 节点 API 创建转发服务，并依赖节点的流量上报、隧道账号和限速插件。这一版**保留原版 GOST 转发引擎**及节点安装命令，不提供另一套节点程序。直接换成 Realm 将失去这些功能，不能作为只修改面板端的小改动。转发协议、隧道功能及节点安装步骤与 Flux 原版一致；节点页生成的命令仍使用上游 1.4.3 节点安装包。

已核对 [Realm 的原生配置](https://github.com/zhboner/realm/blob/master/readme.md) 与 [realm-xwPF 的管理方式](https://github.com/zywe03/realm-xwPF)：Realm 能执行 TCP/UDP 转发和部分传输、负载均衡配置，但 Flux 的面板会向 GOST 节点发送 `AddService`、`AddLimiters` 等指令，并接收节点的流量数据以执行账号配额与计费。realm-xwPF 的端口流量狗使用系统工具管理端口流量，不能直接替代 Flux 的按用户、隧道统计。若要完整替换，需要另做节点代理、规则同步、流量归属和限速执行；在“仅维护面板端、保留原功能”的范围内不切换转发引擎。

## 安装面板

支持 Debian 12 / Ubuntu 22.04+、systemd、root SSH 终端、x86_64。域名 DNS 指向服务器，开放 80/443 给 Caddy；节点到面板后端需要访问 **TCP 6365**，仅向可信节点放行。正式安装优先下载本仓库 CI 为当前提交构建的后端与前端镜像压缩包，校验 SHA-256 后加载，服务器不必运行 Maven 与 npm。首次安装仍需下载 Docker、Caddy、MySQL 镜像等，耗时取决于服务器网络。若当前提交的预构建包尚未发布或下载失败，安装器会回退到逐个源码构建并提示原因；此路径需要更多时间与可用内存，1 核 1 GB 机器可能无法完成。建议为 Docker 镜像和临时下载预留约 6 GB 磁盘空间。安装器不创建或调整 swap。

root 登录后复制下面**完整的一行**；下载成功才会执行脚本：

```bash
curl -fsSL https://raw.githubusercontent.com/a2899882/Nexus-panel/main/panel_install.sh -o /root/nexus-panel-install.sh && bash /root/nexus-panel-install.sh
```

安装器依次询问面板域名、节点连接后端的公网 IP/域名、管理员账号和密码。密码留空时随机生成并只显示一次；新安装没有共享默认密码。访问 `https://面板域名` 登录，方式与 Flux 原版一致。在“网站配置”可修改应用名称及节点后端地址；账号密码可在面板内修改。

如果安装在下载或构建时中断，重新运行**同一条命令**即可识别 `/opt/nexus-panel` 中的安装配置、拉取修复并继续；数据库配置不会重新生成。安装过程中已创建 `/usr/local/bin/mb`，也可用 `mb install` 重试。此前生成的随机初始密码丢失时，安装完成后执行 `mb reset-admin` 重设。

前端仅监听本机 `127.0.0.1:6366` 并由 Caddy 提供 HTTPS。Flux 节点协议仍要求连接独立的 `后端地址:6365`，不能把这个端口误配置成前端 HTTPS 地址，也不要给节点后端域名套 CDN。建议在云防火墙限制 6365 的来源。

节点由面板“节点监控”页面生成原版安装命令；本仓库只提供面板安装器，不重新开发节点端。

## SSH 管理菜单

在**面板服务器以 root 登录**后运行 `mb`：

| 选项 | 功能 |
| --- | --- |
| 1 / 6 / 7 | 查看状态、日志、重启服务 |
| 2 | 先备份，再拉取本仓库更新并重建容器（`mb update`） |
| 3 | 更换面板域名，校验并重载 Caddy 配置 |
| 4 | 生成 `/root/nexus-panel-backup-日期-编号.tar.gz` |
| 5 | 从备份压缩包恢复数据库；操作前自动再做一次安全备份 |
| 8 | 停止容器，保留数据卷和源码（`mb stop`） |
| 9 | 启动或按现有源码重装服务，不删除数据（`mb install`） |
| 10 | 先备份，确认后卸载本项目容器、数据卷、镜像、源码和域名配置（`mb uninstall`） |
| 11 | 重设初始管理员密码（`mb reset-admin`） |

备份包含 MySQL 数据、安装时的 `.env` 配置快照与域名记录。`mb uninstall` 在移除新版数据前生成这类备份；保留 Docker、Caddy 及主机 swap，以免影响其他服务，卸载后可再次运行上方 root 一行命令。恢复时使用**当前机器**的域名和数据库凭证，避免新服务器 MySQL 卷与旧密码不匹配；SQL 导入失败时尝试回滚到操作前的备份。新服务器迁移：先在新服务器按上面的命令安装，将压缩包上传到 `/root`，运行 `mb` 选 5，填入完整路径；随后在面板“网站配置”更新节点后端地址并重新安装/指向现有节点。

若旧安装器此前创建了 `/swapfile-nexus-panel`，新版安装器不会重复创建或调整它；`mb` 菜单不管理系统 swap。

## 与旧 Nexus-panel 的关系

旧的 Python/SQLite Nexus-panel 与 Flux/MySQL 表结构不同，**不能直接把旧备份恢复到新版**。旧代码保留在 [`archive/pre-flux-20260926`](https://github.com/a2899882/Nexus-panel/tree/archive/pre-flux-20260926) 分支。安装器能识别旧版 `panel.py`、`/var/lib/nexus-panel`、旧 systemd 服务与指向 8765 端口的 Caddy 站点：先列出识别到的文件，要求在 SSH 输入 `MIGRATE`，停止旧面板并将源码、SQLite 数据及相关配置备份为 `/root/nexus-panel-legacy-日期-编号.tar.gz`；验证压缩包可读取后仅清理这些旧版文件，继续安装新版。遇到不符合旧版特征的文件会停止，不会覆盖它们。迁移备份只用于恢复旧版，新的面板需要重新添加 Flux 节点和数据。远程旧节点不由面板安装器删除。

## 本地源码

- `vite-frontend/`：Flux 原版 React/Vite 前端，品牌改为 Nexus-panel。
- `springboot-backend/`：Flux 原版 Spring Boot API。
- `gost.sql`：数据库模板；安装时生成带随机初始凭证的 `runtime/init.sql`。
- `compose.yml`：运行 MySQL、后端与前端；优先加载与当前源码提交对应的预构建镜像，必要时逐个构建源码镜像。
- `panel_install.sh`、`scripts/mb.sh`、`scripts/legacy_cleanup.sh`：面板部署、旧版备份迁移、SSH 菜单、域名、备份与恢复。

```bash
bash -n panel_install.sh scripts/mb.sh scripts/legacy_cleanup.sh
python3 -m py_compile scripts/*.py
```

本项目的面板源码遵循上游 Apache-2.0 许可证。对上游的贡献与归属见 `NOTICE`；未声称 Realm 已替代 GOST。
