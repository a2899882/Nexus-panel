#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo '请使用 root 运行'; exit 1; fi
if ! command -v apt-get >/dev/null; then echo '节点安装器支持 Debian/Ubuntu (systemd + apt)'; exit 1; fi
read -rp '面板地址（https://域名）: ' panel_url
[[ "$panel_url" == https://* ]] || { echo '必须使用 HTTPS'; exit 1; }
read -rsp '节点密钥（在面板添加服务器后显示一次）: ' agent_token; echo
[[ ${#agent_token} -ge 40 ]] || { echo '节点密钥无效'; exit 1; }
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 curl ca-certificates
arch=$(uname -m)
case "$arch" in
  x86_64) asset='realm-x86_64-unknown-linux-gnu.tar.gz' ;;
  aarch64) asset='realm-aarch64-unknown-linux-gnu.tar.gz' ;;
  *) echo '当前支持 x86_64 / aarch64'; exit 1 ;;
esac
version='v2.9.6'
url="https://github.com/zhboner/realm/releases/download/$version/$asset"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
curl -fL --retry 3 "$url" -o "$tmp/realm.tar.gz"
# Verify the upstream release asset digest from GitHub's release API.
python3 - "$asset" "$tmp/realm.tar.gz" <<'PY'
import hashlib,json,sys,urllib.request
asset,path=sys.argv[1:]
req=urllib.request.Request('https://api.github.com/repos/zhboner/realm/releases/tags/v2.9.6',headers={'User-Agent':'nexus-panel-installer'})
with urllib.request.urlopen(req,timeout=20) as r: release=json.load(r)
match=next((x for x in release['assets'] if x['name']==asset),None)
if not match or not match.get('digest','').startswith('sha256:'): sys.exit('无法校验 Realm 压缩包')
if hashlib.sha256(open(path,'rb').read()).hexdigest()!=match['digest'][7:]: sys.exit('Realm SHA-256 校验失败')
PY
tar --no-same-owner -xzf "$tmp/realm.tar.gz" -C "$tmp" realm
install -m 0755 "$tmp/realm" /usr/local/bin/realm
id nexus-agent >/dev/null 2>&1 || useradd --system --home-dir /var/lib/nexus-agent --shell /usr/sbin/nologin nexus-agent
install -d -m 0700 -o nexus-agent -g nexus-agent /var/lib/nexus-agent
install -d -m 0755 /opt/nexus-agent
curl -fL --retry 3 'https://raw.githubusercontent.com/a2899882/Nexus-panel/main/agent.py' -o /opt/nexus-agent/agent.py
printf 'NEXUS_PANEL_URL=%s\nNEXUS_AGENT_TOKEN=%s\n' "$panel_url" "$agent_token" > /var/lib/nexus-agent/agent.env
chown nexus-agent:nexus-agent /var/lib/nexus-agent/agent.env
chmod 0600 /var/lib/nexus-agent/agent.env
cat > /etc/systemd/system/nexus-agent.service <<'EOF'
[Unit]
Description=Nexus-panel Realm node agent
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=nexus-agent
Group=nexus-agent
EnvironmentFile=/var/lib/nexus-agent/agent.env
ExecStart=/usr/bin/python3 /opt/nexus-agent/agent.py
Restart=always
RestartSec=3
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/nexus-agent
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now nexus-agent
printf '节点代理已安装；请在面板等待约 10 秒查看在线状态。\n'
