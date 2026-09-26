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
  local new=${1:-} old='' import='import /etc/caddy/nexus-panel.caddy' added_import=0
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
  if ! grep -Fqx "$import" /etc/caddy/Caddyfile; then
    printf '\n%s\n' "$import" >> /etc/caddy/Caddyfile
    added_import=1
  fi
  if ! caddy validate --config /etc/caddy/Caddyfile; then
    if [[ -n $old ]]; then printf '%s\n' "$old" > "$CADDY_SITE"; else rm -f "$CADDY_SITE"; fi
    if [[ $added_import -eq 1 ]]; then sed -i '\@^import /etc/caddy/nexus-panel.caddy$@d' /etc/caddy/Caddyfile; fi
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
  local tmp target attempt ready=0
  [[ -s $DOMAIN_FILE ]] || { echo '面板域名尚未配置，请先运行 mb domain。' >&2; return 1; }
  compose up -d mysql >/dev/null
  for attempt in {1..30}; do
    if compose exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -N -e "SELECT COUNT(*) FROM user"' >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 2
  done
  [[ $ready -eq 1 ]] || { echo '数据库尚未就绪或缺少管理员表，未生成备份。' >&2; return 1; }
  tmp=$(mktemp -d)
  target="/root/nexus-panel-backup-$(date +%Y%m%d-%H%M%S)-$.tar.gz"
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
  if ! tar -czf "$target" -C "$tmp" panel.sql env.snapshot domain.txt manifest.txt ||
     ! tar -tzf "$target" >/dev/null; then
    rm -f "$target" "$tmp/panel.sql" "$tmp/env.snapshot" "$tmp/domain.txt" "$tmp/manifest.txt"
    rmdir "$tmp"
    echo '备份压缩包未通过校验，已停止操作。' >&2
    return 1
  fi
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
install_panel() {
  local revision tmp base
  revision=$(git rev-parse HEAD)
  if [[ -f runtime/image-sha && $(cat runtime/image-sha) == "$revision" ]] &&
     docker image inspect nexus-panel/backend:local nexus-panel/frontend:local >/dev/null 2>&1; then
    compose up -d --no-build
    echo '当前版本镜像已就绪，面板已启动。'
    return
  fi
  tmp=$(mktemp -d)
  base="https://github.com/a2899882/Nexus-panel/releases/download/build-$revision"
  echo '正在获取已经构建好的 Nexus-panel 镜像（首次下载受网络速度影响）...'
  if curl -fsSL --retry 2 --connect-timeout 15 --max-time 900 "$base/panel-images.sha256" -o "$tmp/panel-images.sha256" &&
     curl -fsSL --retry 2 --connect-timeout 15 --max-time 600 "$base/panel-images.tar.gz" -o "$tmp/panel-images.tar.gz" &&
     (cd "$tmp" && sha256sum -c panel-images.sha256) &&
     { compose stop frontend backend mysql >/dev/null 2>&1 || true; docker load -i "$tmp/panel-images.tar.gz"; }; then
    rm -rf -- "$tmp"
    printf '%s\n' "$revision" > runtime/image-sha
    compose up -d --no-build
    echo '已使用预构建镜像启动面板，数据库卷保持不变。'
    return
  fi
  rm -rf -- "$tmp"
  echo '该提交的预构建镜像不可用，改为在本机依次构建后端和前端。' >&2
  # Only the source fallback runs Maven and Node on the panel server.
  compose stop frontend backend mysql >/dev/null 2>&1 || true
  if ! compose build backend || ! compose build frontend; then
    compose start mysql backend frontend || true
    echo '镜像构建失败，已尝试恢复旧容器；查看上方构建日志后重新执行 mb install。' >&2
    return 1
  fi
  compose up -d --no-build
  printf '%s\n' "$revision" > runtime/image-sha
  echo '面板已安装/重建，数据库卷保持不变。'
}
reset_admin() {
  local password confirm hash attempt ready=0 result
  [[ -r /dev/tty ]] || { echo '重设管理员密码需要交互式 SSH 终端。' >&2; return 1; }
  read -r -s -p '新的管理员密码（至少 12 位）: ' password </dev/tty; echo
  read -r -s -p '再次输入密码: ' confirm </dev/tty; echo
  [[ ${#password} -ge 12 && $password == "$confirm" ]] || { echo '密码长度不足或两次不一致。' >&2; return 1; }
  compose up -d mysql >/dev/null
  for attempt in {1..30}; do
    if compose exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -N -e "SELECT id FROM user WHERE id=1 AND role_id=0"' >/dev/null 2>&1; then
      ready=1; break
    fi
    sleep 2
  done
  [[ $ready -eq 1 ]] || { echo '管理员表尚未就绪。' >&2; return 1; }
  hash=$(printf '%s' "$password" | md5sum | cut -d' ' -f1)
  result=$(compose exec -T mysql sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -N -e "$1"' sh "UPDATE user SET pwd='$hash' WHERE id=1 AND role_id=0; SELECT ROW_COUNT();")
  [[ $result == 1 ]] || { echo '未找到初始管理员账号，密码没有更改。' >&2; return 1; }
  echo '管理员密码已更新。'
}
start_panel() {
  compose up -d
  echo '面板已启动。'
}
uninstall_panel() {
  local confirm snapshot
  if [[ -f $CADDY_SITE ]] && ! grep -Fq 'reverse_proxy 127.0.0.1:6366' "$CADDY_SITE"; then
    echo 'Caddy 站点不是当前面板生成的配置，已停止卸载。' >&2
    return 1
  fi
  [[ -r /dev/tty ]] || { echo '卸载需要交互式 SSH 终端。' >&2; return 1; }
  echo '将备份数据库和配置到 /root，然后移除本项目的容器、数据卷、镜像、域名站点、源码及 mb 命令。'
  echo 'Docker、Caddy、其他站点和系统 swap 会保留。'
  read -r -p '输入 UNINSTALL 确认：' confirm </dev/tty
  [[ $confirm == UNINSTALL ]] || { echo '已取消。'; return 1; }
  snapshot=$(backup)
  [[ -s $snapshot ]] || { echo '备份文件无效，已停止卸载。' >&2; return 1; }
  echo "卸载前备份：$snapshot"
  compose down --volumes
  if [[ -f /etc/caddy/Caddyfile ]]; then
    sed -i '\@^import /etc/caddy/nexus-panel.caddy$@d' /etc/caddy/Caddyfile
  fi
  rm -f "$CADDY_SITE" "$DOMAIN_FILE"
  if command -v caddy >/dev/null && [[ -f /etc/caddy/Caddyfile ]] && systemctl is-active --quiet caddy; then
    if caddy validate --config /etc/caddy/Caddyfile; then systemctl reload caddy; else
      echo 'Caddy 其他站点配置检查失败，请手动检查后重载。' >&2
    fi
  fi
  cd /
  rm -r -- "$APP_DIR"
  docker image rm nexus-panel/backend:local nexus-panel/frontend:local >/dev/null 2>&1 || true
  rm -f /usr/local/bin/mb
  echo "Nexus-panel 已卸载，备份保存在 $snapshot。需要重新安装时使用 README 的 root 安装命令。"
}
update_panel() {
  local snapshot
  snapshot=$(backup)
  echo "更新前备份：$snapshot"
  git pull --ff-only
  install -m 0755 scripts/mb.sh /usr/local/bin/mb
  install_panel
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
    echo '========================================'
    echo '       Nexus-panel 面板管理菜单'
    echo '========================================'
    echo ' 1. 查看状态'
    echo ' 2. 更新面板'
    echo ' 3. 更换面板域名'
    echo ' 4. 导出备份'
    echo ' 5. 恢复备份'
    echo ' 6. 查看日志'
    echo ' 7. 重启服务'
    echo ' 8. 停止服务（保留数据）'
    echo ' 9. 启动 / 重装面板'
    echo '10. 卸载面板'
    echo '11. 重设管理员密码'
    echo ' 0. 退出'
    echo '========================================'
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
      9) install_panel ;;
      10) uninstall_panel; return ;;
      11) reset_admin ;;
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
  update|upgrade) update_panel ;;
  install|reinstall) install_panel ;;
  start) start_panel ;;
  restart) compose restart ;;
  stop) compose down ;;
  uninstall) uninstall_panel ;;
  reset-admin) reset_admin ;;
  *) echo '用法：mb [menu|domain|backup|restore|status|update|install|start|restart|stop|uninstall|reset-admin]' >&2; exit 2 ;;
esac
