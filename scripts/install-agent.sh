#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo '请使用 root 运行'; exit 1; fi
if ! command -v apt-get >/dev/null; then echo '节点安装器支持 Debian/Ubuntu (systemd + apt)'; exit 1; fi
panel_url=''; ticket=''; agent_token=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --panel) panel_url="${2:?缺少面板地址}"; shift 2 ;;
    --ticket) ticket="${2:?缺少安装码}"; shift 2 ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
done
if [[ -z "$panel_url" ]]; then read -rp '面板地址（https://域名）: ' panel_url; fi
panel_url="${panel_url%/}"
[[ "$panel_url" =~ ^https://[a-zA-Z0-9.-]+(:[0-9]{1,5})?$ ]] || { echo '必须使用有效 HTTPS 地址'; exit 1; }
if [[ -z "$ticket" ]]; then read -rsp '节点安装码（面板生成的一次性命令中提供）: ' ticket; echo; fi
[[ "$ticket" =~ ^[a-zA-Z0-9_-]{30,100}$ ]] || { echo '安装码无效'; exit 1; }
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 curl ca-certificates openssl
arch=$(uname -m)
case "$arch" in
  x86_64) asset='realm-x86_64-unknown-linux-gnu-glibc2.28.tar.gz' ;;
  aarch64) asset='realm-aarch64-unknown-linux-gnu-glibc2.28.tar.gz' ;;
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
chmod 0755 "$tmp/realm"
if ! "$tmp/realm" --version > "$tmp/realm-version.log" 2>&1; then
  cat "$tmp/realm-version.log" >&2
  echo 'Realm 与本机系统不兼容，安装未修改现有节点凭证或服务。' >&2
  exit 1
fi
curl -fL --retry 3 'https://raw.githubusercontent.com/a2899882/Nexus-panel/main/agent.py' -o "$tmp/agent.py"
curl -fL --retry 3 'https://raw.githubusercontent.com/a2899882/Nexus-panel/main/tunnel.py' -o "$tmp/tunnel.py"
agent_token=$(python3 - "$panel_url" "$ticket" <<'PY'
import json,sys,urllib.request
url,ticket=sys.argv[1:]
request=urllib.request.Request(url+'/api/agent/enroll',json.dumps({'ticket':ticket}).encode(),{'Content-Type':'application/json'},method='POST')
with urllib.request.urlopen(request,timeout=15) as response: print(json.load(response)['token'])
PY
)
[[ ${#agent_token} -ge 40 ]] || { echo '领取节点凭证失败'; exit 1; }
systemctl stop nexus-agent 2>/dev/null || true
install -m 0755 "$tmp/realm" /usr/local/bin/realm
id nexus-agent >/dev/null 2>&1 || useradd --system --home-dir /var/lib/nexus-agent --shell /usr/sbin/nologin nexus-agent
install -d -m 0700 -o nexus-agent -g nexus-agent /var/lib/nexus-agent
install -d -m 0755 /opt/nexus-agent
install -m 0644 "$tmp/agent.py" /opt/nexus-agent/agent.py
install -m 0644 "$tmp/tunnel.py" /opt/nexus-agent/tunnel.py
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
