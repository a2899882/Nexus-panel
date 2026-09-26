#!/usr/bin/env bash
# Nexus-panel: install the Flux-derived panel only. Node installers remain upstream.
set -euo pipefail

REPO_URL=https://github.com/a2899882/Nexus-panel.git
APP_DIR=/opt/nexus-panel

if [[ $EUID -ne 0 ]]; then echo '请使用 root 执行安装脚本' >&2; exit 1; fi
if [[ ! -r /dev/tty ]]; then echo '安装需要交互式 SSH 终端' >&2; exit 1; fi
exec 3</dev/tty
if ! command -v apt-get >/dev/null; then echo '当前支持 Debian/Ubuntu + systemd' >&2; exit 1; fi
if [[ -d $APP_DIR && -n $(ls -A "$APP_DIR") ]]; then
  echo "检测到现有安装：$APP_DIR。请先备份旧项目；旧 SQLite 数据与 Flux 的 MySQL 数据不兼容，本安装器不会覆盖。" >&2
  exit 1
fi
if [[ -f /etc/caddy/nexus-panel.caddy || -f /etc/nexus-panel-domain ]]; then
  echo '发现旧版 Nexus-panel 的域名配置。请先备份旧安装并移除旧 Caddy 站点，再运行安装脚本。' >&2
  exit 1
fi
if systemctl is-active --quiet nexus-panel 2>/dev/null; then
  echo '检测到旧 Nexus-panel 服务正在运行。请先备份并停止旧服务，再安装新版。' >&2
  exit 1
fi

read -r -u 3 -p '面板域名（DNS 已指向本机，不含协议）: ' domain
[[ $domain =~ ^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$ ]] || { echo '域名无效' >&2; exit 1; }
read -r -u 3 -p '节点连接后端的公网 IP 或域名（不可套 CDN）: ' backend_host
[[ $backend_host =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ && $backend_host != *..* ]] || { echo '后端地址需为 IPv4 或域名' >&2; exit 1; }
read -r -u 3 -p '初始管理员账号 [nexus_admin]: ' admin_user
admin_user=${admin_user:-nexus_admin}
[[ $admin_user =~ ^[A-Za-z0-9_]{3,32}$ && $admin_user != admin_user ]] || { echo '账号格式无效或为原版默认账号' >&2; exit 1; }
read -r -s -u 3 -p '初始密码（至少 12 位，留空自动生成）: ' admin_password; echo
if [[ -z $admin_password ]]; then admin_password=$(openssl rand -base64 24); generated=1; else generated=0; fi
[[ ${#admin_password} -ge 12 ]] || { echo '密码至少 12 位' >&2; exit 1; }

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl ca-certificates openssl docker.io caddy python3
if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-plugin \
    || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-v2 \
    || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose
fi
systemctl enable --now docker
mkdir -p "$APP_DIR"
git clone --depth=1 "$REPO_URL" "$APP_DIR"
cd "$APP_DIR"
install -d -m 0700 runtime

db_name=nexus_panel
db_user=nexus_app
db_password=$(openssl rand -hex 24)
jwt_secret=$(openssl rand -hex 32)
umask 077
cat > .env <<EOF
DB_NAME=$db_name
DB_USER=$db_user
DB_PASSWORD=$db_password
JWT_SECRET=$jwt_secret
BACKEND_PORT=6365
FRONTEND_PORT=6366
EOF
NEXUS_ADMIN_USER="$admin_user" NEXUS_ADMIN_PASSWORD="$admin_password" \
NEXUS_BACKEND_ADDRESS="$backend_host:6365" python3 scripts/render_sql.py gost.sql runtime/init.sql

if docker compose version >/dev/null 2>&1; then
  docker compose -f compose.yml up -d --build
else
  docker-compose -f compose.yml up -d --build
fi

install -m 0755 scripts/mb.sh /usr/local/bin/mb
bash scripts/mb.sh domain "$domain"

echo "面板：https://$domain"
echo "节点后端：$backend_host:6365（请仅向节点放行此 TCP 端口）"
echo "初始账号：$admin_user"
if [[ $generated -eq 1 ]]; then echo "初始密码（仅显示一次）：$admin_password"; fi
echo 'SSH 管理菜单：sudo mb'
echo '安装完成后可在“网站配置”修改面板名称、后端节点地址，在账号菜单修改密码。'
