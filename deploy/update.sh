#!/bin/bash
# 从 GitHub 拉取最新程序代码并更新服务器。
# 只更新：engine/、deploy/、RULES.md、EVOLUTION.md、README.md、AUDIT-*.md
# 不动：pools/（股票池）、studios/（参数、台账、进化记录）、data/（行情、成交）、reports/（报告）
#
# 用法（root）：
#   bash /opt/qishi-arena/deploy/update.sh            # 有新版本才更新
#   bash /opt/qishi-arena/deploy/update.sh --force    # 强制重装当前最新版本
#   bash /opt/qishi-arena/deploy/update.sh --rollback # 退回上一次更新前的代码
# 首次安装自动更新（一条命令）：
#   curl -fsSL https://raw.githubusercontent.com/haoyongwhynot-glitchhj/qishi-arena/main/deploy/update.sh | bash
set -euo pipefail
REPO=haoyongwhynot-glitchhj/qishi-arena
BRANCH=main
APP=/opt/qishi-arena
LOG=$APP/reports/update.log
[ "$(id -u)" = 0 ] || { echo "请用 root 运行"; exit 1; }
mkdir -p "$APP/reports" "$APP/backup"
exec > >(tee -a "$LOG") 2>&1
echo "== $(TZ=Asia/Shanghai date '+%F %T') update $*"

if [ "${1:-}" = "--rollback" ]; then
  LAST=$(ls -1d "$APP"/backup/* 2>/dev/null | tail -1)
  [ -n "$LAST" ] || { echo "没有可回滚的备份"; exit 1; }
  rm -rf "$APP/engine" "$APP/deploy"
  cp -r "$LAST/engine" "$LAST/deploy" "$APP/"
  cp "$LAST"/*.md "$APP/" 2>/dev/null || true
  cat "$LAST/.version" > "$APP/.version" 2>/dev/null || true
  chown -R qishi:qishi "$APP"
  systemctl restart qishi-web
  echo "已回滚到 $LAST"; exit 0
fi

SHA=$(curl -fsSL -m 30 "https://api.github.com/repos/$REPO/commits/$BRANCH" | python3 -c 'import json,sys;print(json.load(sys.stdin)["sha"])')
CUR=$(cat "$APP/.version" 2>/dev/null || echo none)
if [ "$SHA" = "$CUR" ] && [ "${1:-}" != "--force" ]; then echo "已是最新版本 ${SHA:0:7}"; exit 0; fi

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
curl -fsSL -m 120 "https://codeload.github.com/$REPO/tar.gz/$SHA" | tar xz -C "$TMP"
SRC=$(ls -1d "$TMP"/*/ | head -1)
[ -f "$SRC/engine/run_day.py" ] || { echo "下载内容不完整，放弃更新"; exit 1; }
# 语法检查不通过就不更新
python3 -m py_compile "$SRC"/engine/*.py || { echo "代码语法检查失败，放弃更新"; exit 1; }

# 备份当前代码（保留最近 10 份）
BK="$APP/backup/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BK"
cp -r "$APP/engine" "$APP/deploy" "$BK/" 2>/dev/null || true
cp "$APP"/*.md "$BK/" 2>/dev/null || true
cp "$APP/.version" "$BK/" 2>/dev/null || true
ls -1d "$APP"/backup/* | head -n -10 | xargs -r rm -rf

# 替换代码
rm -rf "$APP/engine" "$APP/deploy"
cp -r "$SRC/engine" "$SRC/deploy" "$APP/"
for f in RULES.md EVOLUTION.md README.md "$SRC"/AUDIT-*.md; do cp "$SRC/$(basename "$f")" "$APP/" 2>/dev/null || true; done
find "$APP/engine" -name '__pycache__' -prune -exec rm -rf {} +
chmod +x "$APP"/deploy/*.sh

# 同步定时任务（含本自动更新任务）
cp "$APP"/deploy/qishi-web.service "$APP"/deploy/qishi-daily.service "$APP"/deploy/qishi-daily.timer \
   "$APP"/deploy/qishi-update.service "$APP"/deploy/qishi-update.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now qishi-daily.timer qishi-update.timer >/dev/null

chown -R qishi:qishi "$APP"
echo "$SHA" > "$APP/.version"
systemctl restart qishi-web
sudo -u qishi bash -c "cd $APP/engine && python3 site.py" >/dev/null
echo "已更新：${CUR:0:7} → ${SHA:0:7}"
