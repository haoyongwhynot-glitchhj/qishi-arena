#!/bin/bash
# 在 Ubuntu/Debian 服务器上以 root 运行：sudo bash deploy/install.sh 你的域名 看板登录用户名
set -e
DOMAIN=${1:?用法: install.sh 域名 用户名}; USER_NAME=${2:-chairman}
APP=/opt/qishi-arena
apt-get update && apt-get install -y python3 nginx apache2-utils certbot python3-certbot-nginx
id qishi >/dev/null 2>&1 || useradd -r -m -d /home/qishi -s /usr/sbin/nologin qishi
SRC="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p $APP && [ "$SRC" != "$APP" ] && cp -r "$SRC/." $APP/
chown -R qishi:qishi $APP
# 写入接口口令
if [ ! -f /etc/qishi.env ]; then
  echo "QISHI_TOKEN=$(head -c 18 /dev/urandom | base64 | tr -d '/+=')" > /etc/qishi.env
  echo "QISHI_PORT=8711" >> /etc/qishi.env; chmod 600 /etc/qishi.env
fi
cp $APP/deploy/qishi-web.service $APP/deploy/qishi-daily.service $APP/deploy/qishi-daily.timer $APP/deploy/qishi-update.service $APP/deploy/qishi-update.timer /etc/systemd/system/
sed "s/__DOMAIN__/$DOMAIN/g" $APP/deploy/nginx.conf > /etc/nginx/sites-available/qishi
ln -sf /etc/nginx/sites-available/qishi /etc/nginx/sites-enabled/qishi
[ -f /etc/nginx/qishi.htpasswd ] || htpasswd -c /etc/nginx/qishi.htpasswd "$USER_NAME"
sudo -u qishi bash -c "cd $APP/engine && python3 bootstrap.py && python3 site.py"
systemctl daemon-reload && systemctl enable --now qishi-web qishi-daily.timer qishi-update.timer
nginx -t && systemctl reload nginx
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@$DOMAIN" --redirect || echo "证书申请失败：确认域名已解析到本机、80端口已放行后重跑 certbot --nginx -d $DOMAIN"
echo "完成：https://$DOMAIN  录入口令见 /etc/qishi.env"
