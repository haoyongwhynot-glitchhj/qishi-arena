# 部署到你的服务器

## 前提
- 一台 Linux 服务器（Ubuntu 20.04+ 或 Debian 11+），有 root 权限。
- 服务器能访问腾讯行情（qt.gtimg.cn、web.ifzq.gtimg.cn、proxy.finance.qq.com）。境内外服务器都可以。
- 域名已添加 A 记录，指向这台服务器的 IP；防火墙或安全组已放行 80 和 443 端口。
- 如果服务器在中国大陆，域名需要先完成 ICP 备案，否则 80/443 端口会被拦截。

## 一键安装
```bash
# 1. 把 qishi-arena-v3.zip 上传到服务器，然后：
unzip qishi-arena-v3.zip && cd qishi-arena
sudo bash deploy/install.sh 你的域名 chairman
# 安装过程中会提示设置看板登录密码（用户名为 chairman）
```

安装完成后：
- 打开 `https://你的域名`。浏览器先弹出登录框，用户名 chairman，密码是安装时设的。
- “录入”页提交股票池和批注时要填口令，口令在服务器上查看：`sudo cat /etc/qishi.env`。

## 自动运行的内容
| 时间（北京时间） | 内容 |
|---|---|
| 周一至周五 15:45 | 结算当日成交（开盘卖出、14:30 分时买入）→ 清理股票池 → 退出检查 → 全池评分 → 生成次日计划 → 重建看板 |
| 周五 15:45 之后 | 在上一项基础上，再运行影子测试 → 自动晋级或回滚 → 更新错题本 |
| 每天 08:40、15:25 | 检查 GitHub 仓库有没有新代码，有就自动更新程序（只更新 engine/、deploy/ 和规则文档，不动股票池、台账、行情） |
| 随时 | 看板“录入”页提交股票池后自动重算次日计划；提交批注后刷新看板 |

节假日休市时，程序发现没有新行情，会自动跳过结算。

## 常用命令
```bash
systemctl status qishi-web               # 看板服务状态
systemctl list-timers qishi-daily.timer  # 下次定时运行时间
sudo systemctl start qishi-daily         # 立即手动跑一次
tail -f /opt/qishi-arena/reports/job_$(date +%F).log
```

## 安全
- 整站有 HTTP 基本认证，外人看不到内容。
- 写入接口需要口令，口令保存在 `/etc/qishi.env`，只有 root 可读。
- 程序以独立的 qishi 用户运行，只监听 127.0.0.1，由 nginx 对外提供服务。

## 备份
所有状态都是文本文件，存放在 `/opt/qishi-arena`（pools、studios、data、reports 目录）。建议每天备份一次，或者把这个目录推送到你的 GitHub 私有仓库。

## 程序更新
- 自动：服务器每天 08:40 和 15:25 检查仓库 main 分支，有新版本就更新程序并重启看板，记录写在 `reports/update.log`。
- 手动立即更新：`sudo bash /opt/qishi-arena/deploy/update.sh`
- 回滚到上一版：`sudo bash /opt/qishi-arena/deploy/update.sh --rollback`
- 更新前会做语法检查，不通过就放弃；每次更新前备份旧代码到 `backup/`（保留 10 份）。
- 安全提醒：能往这个仓库 main 分支推代码的人，就能在你的服务器上运行代码。只给可信的人推送权限，令牌用完即删。
