#!/usr/bin/env bash
# SSH management menu for the Flux-derived Nexus-panel.
set -euo pipefail
APP_DIR=/opt/nexus-panel
DOMAIN_FILE=/etc/nexus-panel-domain
CADDY_SITE=/etc/caddy/nexus-panel.caddy
[[ $EUID -eq 0 ]] || { echo '请使用 root 登录后执行：mb' >&2; exit 1; }
[[ -f "$APP_DIR/compose.yml" && -f "$APP_DIR/.env" ]] || { echo '未找到 Nexus-panel 安装配置' >&2; exit 1; }
cd "$APP_DIR"

compose() {
  if docker compose version >/dev/null 2>&1; then docker compose -f compose.yml "$@";
  else docker-compose -f compose.yml "$@"; fi
}
load_db_name() {
  local name
  name=$(sed -n 's/^DB_NAME=//p' .env | head -1)
  [[ $name =~ ^[A-Za-z0-9_]+$ ]] || { echo '数据库名格式无效' >&2; return 1; }
  DB_NAME=$name
}
domain() {
  local new=${1:-} old='' import='import /etc/caddy/nexus-panel.caddy'
  if [[ -z $new ]]; then read -r -p '新的面板域名（DNS 已指向本机）: ' new; fi
  [[ $new =~ ^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$ ]] || { echo '域名无效' >&2; return 1; }
  mkdir -p /etc/caddy
  [[ ! -f $CADDY_SITE ]] || old=$(cat "$CADDY_SITE")
  cat > "$CADDY_SITE" <<EOF
$new {
    encode zstd gzip
    reverse_proxy 127.0.0.1:6366
}
EOF
  touch /etc/caddy/Caddyfile
  if ! grep -Fqx "$import" /etc/caddy/Caddyfile; then printf '\n%s\n' "$import" >> /etc/caddy/Caddyfile; fi
  if ! caddy validate --config /etc/caddy/Caddyfile; then
    if [[ -n $old ]]; then printf '%s\n' "$old" > "$CADDY_SITE"; else rm -f "$CADDY_SITE"; fi
    echo 'Caddy 配置检查失败，已恢复原域名配置' >&2
    return 1
  fi
  printf '%s\n' "$new" > "$DOMAIN_FILE"
  chmod 0600 "$DOMAIN_FILE"
  systemctl enable --now caddy
  systemctl reload caddy
  echo "面板域名：https://$new"
}
backup() {
  local tmp target
  tmp=$(mktemp -d)
  target="/root/nexus-panel-backup-$(date +%Y%m%d-%H%M%S)-$$.tar.gz"
  if ! compose exec -T mysql sh -c 'exec mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction --routines --events --default-character-set=utf8mb4 "$MYSQL_DATABASE"' > "$tmp/panel.sql"; then
    rm -f "$tmp/panel.sql"
    rmdir "$tmp"
    echo '数据库导出失败，未生成备份' >&2
    return 1
  fi
  cp .env "$tmp/env.snapshot"
  cp "$DOMAIN_FILE" "$tmp/domain.txt"
  printf '%s\n' NEXUS_PANEL_BACKUP_V1 > "$tmp/manifest.txt"
  umask 077
  tar -czf "$target" -C "$tmp" panel.sql env.snapshot domain.txt manifest.txt
  chmod 0600 "$target"
  rm -f "$tmp/panel.sql" "$tmp/env.snapshot" "$tmp/domain.txt" "$tmp/manifest.txt"
  rmdir "$tmp"
  echo "$target"
}
import_sql() {
  local file=$1 statement
  load_db_name
  statement="DROP DATABASE IF EXISTS \`$DB_NAME\`; CREATE DATABASE \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
  compose exec -T mysql sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "$1"' sh "$statement"
  compose exec -T mysql sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' < "$file"
}
restore() {
  local archive=${1:-} tmp safety
  if [[ -z $archive ]]; then read -r -p '备份包路径（例如 /root/nexus-panel-backup-日期.tar.gz）: ' archive; fi
  [[ -f $archive ]] || { echo '备份包不存在' >&2; return 1; }
  tmp=$(mktemp -d)
  if ! python3 "$APP_DIR/scripts/validate_backup.py" "$archive" "$tmp"; then
    rm -f "$tmp/panel.sql" "$tmp/env.snapshot" "$tmp/domain.txt" "$tmp/manifest.txt"
    rmdir "$tmp"
    return 1
  fi
  safety=$(backup)
  echo "当前数据库已先备份至：$safety"
  compose stop backend frontend
  if ! import_sql "$tmp/panel.sql"; then
    echo '导入失败，正在恢复操作前的数据库快照' >&2
    local rollback
    rollback=$(mktemp -d)
    python3 "$APP_DIR/scripts/validate_backup.py" "$safety" "$rollback"
    import_sql "$rollback/panel.sql" || true
    compose start backend frontend || true
    rm -f "$rollback/panel.sql" "$rollback/env.snapshot" "$rollback/domain.txt" "$rollback/manifest.txt"
    rmdir "$rollback"
    rm -f "$tmp/panel.sql" "$tmp/env.snapshot" "$tmp/domain.txt" "$tmp/manifest.txt"
    rmdir "$tmp"
    echo "请检查快照：$safety" >&2
    return 1
  fi
  compose start backend frontend
  rm -f "$tmp/panel.sql" "$tmp/env.snapshot" "$tmp/domain.txt" "$tmp/manifest.txt"
  rmdir "$tmp"
  echo '数据库已恢复。当前服务器的域名和数据库凭证保持不变。'
  echo '若迁移到新服务器，请在网站配置中更新节点后端 IP，并重新下发原版节点安装命令。'
}
update_panel() {
  local snapshot
  snapshot=$(backup)
  echo "更新前备份：$snapshot"
  git pull --ff-only
  install -m 0755 scripts/mb.sh /usr/local/bin/mb
  compose up -d --build
  echo '程序已更新，数据卷保持不变。'
}
status() {
  compose ps
  systemctl --no-pager --full status caddy 2>/dev/null | head -18 || true
  echo "域名：$(cat "$DOMAIN_FILE" 2>/dev/null || echo 未设置)"
}
menu() {
  while true; do
    echo
    echo 'Nexus-panel | SSH 菜单（mb）'
    echo '1) 服务状态  2) 更新程序  3) 更换面板域名  4) 备份压缩包'
    echo '5) 恢复备份  6) 查看日志  7) 重启服务  8) 停用面板（保留数据）  0) 退出'
    read -r -p '选择: ' choice
    case "$choice" in
      1) status ;;
      2) update_panel ;;
      3) domain ;;
      4) backup ;;
      5) restore ;;
      6) compose logs --tail=80 backend frontend mysql ;;
      7) compose restart ;;
      8) compose down; echo '容器已停止，数据库卷与源代码已保留。' ;;
      0) return ;;
      *) echo '选项无效' ;;
    esac
  done
}
case ${1:-menu} in
  menu) menu ;;
  domain) domain "${2:-}" ;;
  backup) backup ;;
  restore) restore "${2:-}" ;;
  status) status ;;
  update) update_panel ;;
  *) echo '用法：mb [menu|domain|backup|restore|status|update]' >&2; exit 2 ;;
esac
