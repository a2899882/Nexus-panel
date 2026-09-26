#!/usr/bin/env bash
# Nexus-panel: install the Flux-derived panel only. Node installers remain upstream.
set -euo pipefail

REPO_URL=https://github.com/a2899882/Nexus-panel.git
APP_DIR=/opt/nexus-panel

if [[ $EUID -ne 0 ]]; then echo '请使用 root 执行安装脚本' >&2; exit 1; fi
if [[ ! -r /dev/tty ]]; then echo '安装需要交互式 SSH 终端' >&2; exit 1; fi
exec 3</dev/tty
if ! command -v apt-get >/dev/null; then echo '当前支持 Debian/Ubuntu + systemd' >&2; exit 1; fi
resume=0
if [[ -f "$APP_DIR/compose.yml" && -f "$APP_DIR/.env" ]]; then
  if [[ ! -d "$APP_DIR/.git" || ! -s "$APP_DIR/runtime/init.sql" ]] ||
     [[ $(git -C "$APP_DIR" remote get-url origin 2>/dev/null) != "$REPO_URL" ]]; then
    echo '现有目录缺少本项目的 Git 信息或初始化配置，请检查 /opt/nexus-panel；安装器不会覆盖。' >&2
    exit 1
  fi
  resume=1
  echo '检测到未完成或已有的 Nexus-panel 安装，将保留现有数据并继续安装。'
fi
read -r -u 3 -p '面板域名（DNS 已指向本机，不含协议）: ' domain
[[ $domain =~ ^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$ ]] || { echo '域名无效' >&2; exit 1; }
if [[ $resume -eq 0 ]]; then
read -r -u 3 -p '节点连接后端的公网 IP 或域名（不可套 CDN）: ' backend_host
[[ $backend_host =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ && $backend_host != *..* ]] || { echo '后端地址需为 IPv4 或域名' >&2; exit 1; }
read -r -u 3 -p '初始管理员账号 [nexus_admin]: ' admin_user
admin_user=${admin_user:-nexus_admin}
[[ $admin_user =~ ^[A-Za-z0-9_]{3,32}$ && $admin_user != admin_user ]] || { echo '账号格式无效或为原版默认账号' >&2; exit 1; }
read -r -s -u 3 -p '初始密码（至少 12 位，留空自动生成）: ' admin_password; echo
if [[ -z $admin_password ]]; then
  if ! command -v openssl >/dev/null 2>&1; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssl
  fi
  admin_password=$(openssl rand -base64 24); generated=1
else
  generated=0
fi
[[ ${#admin_password} -ge 12 ]] || { echo '密码至少 12 位' >&2; exit 1; }
fi

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl ca-certificates openssl docker.io caddy python3
if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null; then
  if apt-cache show docker-compose-plugin >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-plugin
  elif apt-cache show docker-compose-v2 >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-v2
  else
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose
  fi
fi
if [[ $resume -eq 0 ]] &&
   [[ -e $APP_DIR || -L $APP_DIR || -e /var/lib/nexus-panel || -f /etc/systemd/system/nexus-panel.service ||
      -f /etc/caddy/nexus-panel.caddy || -f /etc/nexus-panel-domain || -L /usr/local/bin/nexus-panel ]]; then
  curl -fsSL https://raw.githubusercontent.com/a2899882/Nexus-panel/main/scripts/legacy_cleanup.sh \
    -o /root/nexus-panel-legacy-cleanup.sh
  bash /root/nexus-panel-legacy-cleanup.sh
  rm -f /root/nexus-panel-legacy-cleanup.sh
fi
if [[ $resume -eq 0 ]] && { [[ -d $APP_DIR && -n $(ls -A "$APP_DIR") ]] ||
   [[ -f /etc/caddy/nexus-panel.caddy || -f /etc/nexus-panel-domain ]] ||
   systemctl is-active --quiet nexus-panel 2>/dev/null; }; then
  echo '检测到尚未清理的旧面板或其他安装，已停止，未覆盖任何数据。' >&2
  exit 1
fi

systemctl enable --now docker
if [[ $resume -eq 1 ]]; then
  git -C "$APP_DIR" pull --ff-only
  install -m 0755 "$APP_DIR/scripts/mb.sh" /usr/local/bin/mb
  mb install
  mb domain "$domain"
  echo '已继续安装，原数据库和凭证保留。如忘记初始密码，可执行 mb reset-admin。'
  exit 0
fi
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

install -m 0755 scripts/mb.sh /usr/local/bin/mb
echo "初始账号：$admin_user"
if [[ $generated -eq 1 ]]; then echo "初始密码（仅显示一次，请保存）：$admin_password"; fi
mb install
bash scripts/mb.sh domain "$domain"

echo "面板：https://$domain"
echo "节点后端：$backend_host:6365（请仅向节点放行此 TCP 端口）"
echo 'SSH 管理菜单：mb'
echo '安装完成后可在“网站配置”修改面板名称、后端节点地址，在账号菜单修改密码。'
