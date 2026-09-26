#!/usr/bin/env bash
set -euo pipefail
APP_DIR=/opt/nexus-panel
DATA_DIR=/var/lib/nexus-panel
service_status() { systemctl is-active nexus-panel 2>/dev/null || true; }
password_reset() {
  read -rp '账号名 [admin]: ' name; name=${name:-admin}
  read -rsp '新密码（至少 12 位）: ' pass; echo
  NEXUS_DB="$DATA_DIR/panel.db" NEXUS_ADMIN_PASSWORD="$pass" python3 "$APP_DIR/panel.py" reset-password --username "$name"
}
backup() (
  local target="/root/nexus-panel-backup-$(date +%Y%m%d-%H%M%S).tar.gz" temp
  temp=$(mktemp -d); trap 'rm -rf "$temp"' EXIT
  NEXUS_DB="$DATA_DIR/panel.db" python3 - "$temp/panel.db" <<'PY'
import os,sqlite3,sys
src=sqlite3.connect(os.environ['NEXUS_DB']); dst=sqlite3.connect(sys.argv[1]); src.backup(dst); dst.close(); src.close()
PY
  cp /etc/nexus-panel-domain "$temp/domain"
  cp "$DATA_DIR/master.key" "$temp/master.key"
  tar -czf "$target" -C "$temp" panel.db domain master.key
  chmod 0600 "$target"
  echo "备份完成: $target（包含账号散列及节点密钥散列，请妥善保管）"
)
restore() (
  read -rp '备份包完整路径（例如 /root/nexus-panel-backup.tar.gz）: ' package
  [[ -f "$package" ]] || { echo '文件不存在'; return 1; }
  local temp
  temp=$(mktemp -d); trap 'rm -rf "$temp"' EXIT
  python3 - "$package" "$temp" <<'PY'
import os,tarfile,sys
p,d=sys.argv[1:]
with tarfile.open(p,'r:gz') as t:
    names=set(t.getnames())
    if names not in ({'panel.db','domain'},{'panel.db','domain','master.key'}) or any(not x.isfile() or x.size>100_000_000 for x in t): sys.exit('备份包格式无效')
    for x in t:
        with open(os.path.join(d,x.name),'wb') as f: f.write(t.extractfile(x).read())
PY
  python3 - "$temp/panel.db" <<'PY'
import sqlite3,sys
c=sqlite3.connect(sys.argv[1]); result=c.execute('PRAGMA integrity_check').fetchone()[0]
if result!='ok' or not c.execute("SELECT 1 FROM sqlite_master WHERE name='users'").fetchone(): sys.exit('数据库校验失败')
PY
  echo '将覆盖当前面板数据库。输入 RESTORE 继续：'
  read -r confirm
  [[ "$confirm" == 'RESTORE' ]] || return 1
  systemctl stop nexus-panel
  cp -p "$DATA_DIR/panel.db" "$DATA_DIR/panel.db.pre-restore" 2>/dev/null || true
  install -m 0600 -o nexus-panel -g nexus-panel "$temp/panel.db" "$DATA_DIR/panel.db"
  if [[ -f "$temp/master.key" ]]; then install -m 0600 -o nexus-panel -g nexus-panel "$temp/master.key" "$DATA_DIR/master.key"; fi
  systemctl start nexus-panel
  echo '已恢复数据库。原节点密钥不会从散列中还原；原代理仍可用。新域名请在菜单中设置。'
)
change_domain() {
  read -rp '新域名（DNS 已解析到本机）: ' domain
  [[ "$domain" =~ ^[a-zA-Z0-9.-]+$ && "$domain" == *.* ]] || { echo '域名无效'; return 1; }
  cat > /etc/caddy/nexus-panel.caddy <<EOF
$domain {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8765
}
EOF
  caddy validate --config /etc/caddy/Caddyfile
  echo "$domain" > /etc/nexus-panel-domain
  systemctl reload caddy
  echo "面板地址: https://$domain"
}
[[ $EUID -eq 0 ]] || { echo '请使用 root 运行'; exit 1; }
while :; do
  echo
  echo "Nexus-panel | 域名: $(cat /etc/nexus-panel-domain 2>/dev/null || echo 未设置) | 状态: $(service_status)"
  echo '1) 状态与诊断   2) 重启面板   3) 更新程序   4) 修改域名'
  echo '5) 重置管理员密码   6) 备份   7) 恢复备份   8) 查看日志'
  echo '9) 安装本机节点代理   0) 退出'
  read -rp '选择: ' choice
  case "$choice" in
    1) systemctl status nexus-panel --no-pager || true; systemctl status caddy --no-pager || true ;;
    2) systemctl restart nexus-panel ;;
    3) git -C "$APP_DIR" pull --ff-only; systemctl restart nexus-panel ;;
    4) change_domain ;;
    5) password_reset ;;
    6) backup ;;
    7) restore ;;
    8) journalctl -u nexus-panel -n 60 --no-pager; journalctl -u nexus-agent -n 60 --no-pager 2>/dev/null || true ;;
    9) bash "$APP_DIR/scripts/install-agent.sh" ;;
    0) exit 0 ;;
    *) echo '无效选项' ;;
  esac
done
