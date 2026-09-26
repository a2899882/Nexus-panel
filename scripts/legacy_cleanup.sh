#!/usr/bin/env bash
# Migrate only the archived Python/SQLite panel. Never operate on unknown installs.
set -euo pipefail
APP_DIR=/opt/nexus-panel
DATA_DIR=/var/lib/nexus-panel
SERVICE=/etc/systemd/system/nexus-panel.service
SITE=/etc/caddy/nexus-panel.caddy
DOMAIN_FILE=/etc/nexus-panel-domain
CADDYFILE=/etc/caddy/Caddyfile
OLD_MENU=/usr/local/bin/nexus-panel

[[ $EUID -eq 0 ]] || { echo '请以 root 运行。' >&2; exit 1; }
[[ -r /dev/tty ]] || { echo '旧版迁移需要交互式 SSH 终端。' >&2; exit 1; }
if [[ -L $APP_DIR || ( -e $APP_DIR && ! -f $APP_DIR/panel.py ) ]]; then
  echo "无法识别 $APP_DIR，未进行清理。" >&2
  exit 1
fi
if [[ -e $DATA_DIR && ! -d $DATA_DIR || -L $DATA_DIR ]]; then
  echo "无法识别 $DATA_DIR，未进行清理。" >&2
  exit 1
fi
if [[ -f $SERVICE ]] && ! grep -Fq 'ExecStart=/usr/bin/python3 /opt/nexus-panel/panel.py serve' "$SERVICE"; then
  echo "服务文件 $SERVICE 不是旧版面板，未进行清理。" >&2
  exit 1
fi
if [[ -f $SITE ]] && ! grep -Fq 'reverse_proxy 127.0.0.1:8765' "$SITE"; then
  echo "Caddy 站点 $SITE 不是旧版面板，未进行清理。" >&2
  exit 1
fi
if [[ -e $OLD_MENU || -L $OLD_MENU ]] && { [[ ! -L $OLD_MENU ]] || [[ $(readlink "$OLD_MENU") != "$APP_DIR/scripts/menu.sh" ]]; }; then
  echo "命令 $OLD_MENU 不是旧版面板的链接，未进行清理。" >&2
  exit 1
fi
if [[ -f $APP_DIR/compose.yml || -f $APP_DIR/.env ]]; then
  echo '检测到新版面板配置，旧版迁移不会触碰现有数据。' >&2
  exit 1
fi

set --
for rel in opt/nexus-panel var/lib/nexus-panel etc/systemd/system/nexus-panel.service \
           etc/caddy/nexus-panel.caddy etc/caddy/Caddyfile etc/nexus-panel-domain \
           usr/local/bin/nexus-panel; do
  if [[ -e /$rel || -L /$rel ]]; then set -- "$@" "$rel"; fi
done
if [[ $# -eq 0 ]]; then
  echo '未发现旧版面板文件。'
  exit 0
fi
echo '识别到旧版 Python/SQLite 面板。将先保存源码、数据库和相关系统配置到 /root，再清理旧面板。'
printf '备份项目：%s\n' "$@"
read -r -p '确认迁移旧版并继续安装？输入 MIGRATE: ' confirm </dev/tty
[[ $confirm == MIGRATE ]] || { echo '已取消，旧项目保持原状。'; exit 1; }

was_active=0
if systemctl is-active --quiet nexus-panel 2>/dev/null; then
  was_active=1
  systemctl stop nexus-panel
fi
umask 077
archive="/root/nexus-panel-legacy-$(date +%Y%m%d-%H%M%S)-$$.tar.gz"
if ! tar -C / -czf "$archive" "$@" || ! tar -tzf "$archive" >/dev/null; then
  rm -f "$archive"
  if [[ $was_active -eq 1 ]]; then systemctl start nexus-panel || true; fi
  echo '旧版备份失败，已尝试恢复旧面板服务，未删除任何文件。' >&2
  exit 1
fi
chmod 0600 "$archive"
if [[ -f $SERVICE ]]; then
  systemctl disable --now nexus-panel >/dev/null 2>&1 || true
  rm -f "$SERVICE"
  systemctl daemon-reload
fi
if [[ -f $CADDYFILE ]]; then
  sed -i '\@^import /etc/caddy/nexus-panel.caddy$@d' "$CADDYFILE"
fi
rm -f "$SITE" "$DOMAIN_FILE"
if [[ -L $OLD_MENU ]]; then rm -f "$OLD_MENU"; fi
if [[ -d $APP_DIR ]]; then rm -r -- "$APP_DIR"; fi
if [[ -d $DATA_DIR ]]; then rm -r -- "$DATA_DIR"; fi
if id nexus-panel >/dev/null 2>&1; then userdel nexus-panel || echo '旧版系统用户未能移除，请稍后检查。' >&2; fi
if command -v caddy >/dev/null && [[ -f $CADDYFILE ]] && systemctl is-active --quiet caddy; then
  if caddy validate --config "$CADDYFILE"; then systemctl reload caddy; else
    echo 'Caddy 其他站点配置校验失败，请检查配置；旧版备份已保留。' >&2
  fi
fi
echo "旧版备份：$archive"
echo '旧版面板文件已清理。远程节点及其服务未受影响。'
