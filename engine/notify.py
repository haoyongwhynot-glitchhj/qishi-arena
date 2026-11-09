"""每日计划生成后推送提醒（可选）。在 /etc/qishi.env 里设置 QISHI_NOTIFY_URL：
- 企业微信群机器人：https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
- Server酱（推到个人微信）：https://sctapi.ftqq.com/<SendKey>.send
- 钉钉群机器人：https://oapi.dingtalk.com/robot/send?access_token=xxx
未设置则什么都不做。可选 QISHI_SITE_URL 用于消息里的链接。
"""
import json
import os
import urllib.parse
import urllib.request

import lab


def message():
    lines, date = [], ""
    for s in "ABCDE":
        L = json.load(open(os.path.join(lab.ROOT, "studios", s, "ledger.json")))
        g = lab.load_genome(s); date = (L.get("review") or {}).get("plan_date") or L.get("as_of", "")
        sell = "、".join(o["name"] for o in L.get("pending_sell", [])) or "无"
        buy = " > ".join(c["name"] for c in L.get("pending_buy", [])[:3]) or "不买"
        lines.append(f"{s} {g['name']}：净值{L['nav']:,.0f}｜卖：{sell}｜买：{buy}")
    site = os.environ.get("QISHI_SITE_URL", "")
    title = f"擂台 {date} 计划已生成，待复核"
    body = "\n".join(lines) + "\n\n回复 Kiro“复核”，14:25 前写回；未复核按公式执行。" + (f"\n{site}" if site else "")
    return title, body


def send():
    url = os.environ.get("QISHI_NOTIFY_URL", "")
    if not url:
        return "未配置 QISHI_NOTIFY_URL，跳过"
    title, body = message()
    if "sctapi.ftqq.com" in url:
        data = urllib.parse.urlencode(dict(title=title, desp=body.replace("\n", "\n\n"))).encode(); ct = "application/x-www-form-urlencoded"
    elif "dingtalk" in url:
        data = json.dumps(dict(msgtype="text", text=dict(content=f"{title}\n{body}"))).encode(); ct = "application/json"
    else:  # 企业微信或兼容格式
        data = json.dumps(dict(msgtype="text", text=dict(content=f"{title}\n{body}"))).encode(); ct = "application/json"
    req = urllib.request.Request(url, data=data, headers={"Content-Type": ct})
    with urllib.request.urlopen(req, timeout=15) as r:
        return f"已推送：{r.status}"


if __name__ == "__main__":
    print(send())
