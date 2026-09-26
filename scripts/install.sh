#!/usr/bin/env bash
set -euo pipefail
REPO_URL="https://github.com/a2899882/Nexus-panel.git"
APP_DIR="/opt/nexus-panel"
DATA_DIR="/var/lib/nexus-panel"
if [[ $EUID -ne 0 ]]; then echo '请使用 root 运行'; exit 1; fi
if ! command -v apt-get >/dev/null; then echo '当前安装器支持 Debian/Ubuntu (systemd + apt)'; exit 1; fi
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 git curl caddy ca-certificates openssl
if [[ ! -d "$APP_DIR/.git" ]]; then git clone "$REPO_URL" "$APP_DIR"; else git -C "$APP_DIR" pull --ff-only; fi
id nexus-panel >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin nexus-panel
install -d -m 0700 -o nexus-panel -g nexus-panel "$DATA_DIR"
if [[ ! -f "$DATA_DIR/panel.db" ]]; then
  read -rp '面板域名（已解析到本机，不含 https://）: ' domain
  [[ "$domain" =~ ^[a-zA-Z0-9.-]+$ && "$domain" == *.* ]] || { echo '域名无效'; exit 1; }
  read -rp '初始管理员账号 [admin]: ' admin
  admin="${admin:-admin}"
  read -rsp '初始管理员密码（至少 12 位，留空则自动生成）: ' password; echo
  if [[ -z "$password" ]]; then password=$(openssl rand -base64 24); echo "初始密码: $password"; fi
  NEXUS_DB="$DATA_DIR/panel.db" NEXUS_ADMIN_USER="$admin" NEXUS_ADMIN_PASSWORD="$password" runuser -u nexus-panel -- python3 "$APP_DIR/panel.py" bootstrap
  echo "$domain" > /etc/nexus-panel-domain
  chmod 0600 /etc/nexus-panel-domain
else
  domain=$(cat /etc/nexus-panel-domain)
fi
cat > /etc/systemd/system/nexus-panel.service <<EOF
[Unit]
Description=Nexus-panel control plane
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=nexus-panel
Group=nexus-panel
WorkingDirectory=$APP_DIR
Environment=NEXUS_DB=$DATA_DIR/panel.db
Environment=NEXUS_COOKIE_SECURE=1
ExecStart=/usr/bin/python3 $APP_DIR/panel.py serve --host 127.0.0.1 --port 8765
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$DATA_DIR
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/caddy/nexus-panel.caddy <<EOF
$domain {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8765
}
EOF
touch /etc/caddy/Caddyfile
grep -Fqx 'import /etc/caddy/nexus-panel.caddy' /etc/caddy/Caddyfile || printf '\nimport /etc/caddy/nexus-panel.caddy\n' >> /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now nexus-panel
caddy validate --config /etc/caddy/Caddyfile
systemctl enable --now caddy
systemctl reload caddy
ln -sf "$APP_DIR/scripts/menu.sh" /usr/local/bin/nexus-panel
printf '\n面板地址: https://%s\n菜单命令: nexus-panel\n' "$domain"
