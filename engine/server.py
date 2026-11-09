"""看板服务：静态页面 + 两个写入接口（提交股票池、董事长批注）。只用标准库。

启动：QISHI_TOKEN=你的口令 python3 server.py --host 127.0.0.1 --port 8080
建议放在 nginx 后面并启用 HTTPS（见 deploy/）。
接口：
  POST /api/pool  {"token","studio":"A-E","batch":"可空","codes":"每行一只：代码 [名称]"}
  POST /api/note  {"token","text"}
"""
import argparse
import csv
import datetime
import hmac
import json
import os
import re
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SITE = os.path.join(ROOT, "site")
TOKEN = os.environ.get("QISHI_TOKEN", "")
LOCK = threading.Lock()
CN = datetime.timezone(datetime.timedelta(hours=8))


def last_trading_date():
    with open(os.path.join(ROOT, "data", "bars", "000300_idx.csv")) as f:
        return list(csv.DictReader(f))[-1]["date"]


def rebuild(replan):
    """后台重算：拉新股行情 →（需要时）重算次日计划 → 重建看板。"""
    cmds = []
    if replan:
        cmds.append([sys.executable, "-c", "import data,lab,json,os\n"
                     "codes=set()\n"
                     "for s in 'ABCDE': codes|={r['code'] for r in lab.load_pool(s)}\n"
                     "miss=[c for c in codes if not os.path.exists(os.path.join(lab.ROOT,'data','bars',c+'.csv'))]\n"
                     "[data.fetch_bars(c) for c in miss]\n"
                     "q=data.load_quotes(); q.update(data.fetch_quotes(miss) if miss else {})\n"
                     "json.dump(q,open(os.path.join(lab.ROOT,'data','quotes.json'),'w'),ensure_ascii=False)"])
        cmds.append([sys.executable, "daily.py"])
    cmds.append([sys.executable, "site.py"])

    def work():
        with LOCK:
            for c in cmds:
                subprocess.run(c, cwd=HERE, timeout=1800)
    threading.Thread(target=work, daemon=True).start()


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=SITE, **k)

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 100_000:
            return self._json(413, {"error": "内容过大"})
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json(400, {"error": "格式错误"})
        if not TOKEN or not hmac.compare_digest(str(req.get("token", "")), TOKEN):
            return self._json(403, {"error": "口令错误"})
        if self.path == "/api/pool":
            return self.pool(req)
        if self.path == "/api/note":
            text = str(req.get("text", "")).strip()[:2000]
            if not text:
                return self._json(400, {"error": "批注为空"})
            p = os.path.join(ROOT, "data", "notes.json")
            with LOCK:
                notes = json.load(open(p)) if os.path.exists(p) else []
                notes.append(dict(time=datetime.datetime.now(CN).strftime("%Y-%m-%d %H:%M"), text=text))
                json.dump(notes, open(p, "w"), ensure_ascii=False, indent=1)
            rebuild(False)
            return self._json(200, {"message": "已保存，看板稍后刷新"})
        return self._json(404, {"error": "未知接口"})

    def pool(self, req):
        s = str(req.get("studio", ""))
        if s not in list("ABCDE"):
            return self._json(400, {"error": "组别须为 A-E"})
        items = []
        for line in str(req.get("codes", "")).splitlines():
            m = re.match(r"^\s*(\d{6})\s*([^\s,，]*)", line)
            if m:
                items.append((m.group(1), m.group(2)[:20]))
        if not items or len(items) > 200:
            return self._json(400, {"error": "没有识别到6位代码，或一次超过200只"})
        now = datetime.datetime.now(CN)
        trading_hours = now.weekday() < 5 and "09:15" <= now.strftime("%H:%M") < "15:30"
        # 盘中提交：计入今日批次，只用于今晚生成的计划；其余时间：计入最近交易日，立即重算次日计划
        entry = now.strftime("%Y-%m-%d") if trading_hours else last_trading_date()
        ed = entry.replace("-", "")
        batch = re.sub(r"[^\w-]", "", str(req.get("batch", "")))[:30] or f"{s}-{ed}-W{now.strftime('%H%M')}"
        path = os.path.join(ROOT, "pools", f"{s}.csv")
        with LOCK:
            rows = list(csv.DictReader(open(path)))
            have = {r["code"]: r for r in rows}
            added, renewed = 0, 0
            for code, name in items:
                if code in have:
                    have[code].update(batch=batch, entry_date=ed); renewed += 1
                else:
                    rows.append(dict(code=code, name=name, batch=batch, entry_date=ed)); added += 1
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["code", "name", "batch", "entry_date"])
                w.writeheader(); w.writerows(rows)
            with open(os.path.join(ROOT, "pools", "submissions.csv"), "a", newline="") as f:
                csv.writer(f).writerow([now.strftime("%Y-%m-%d %H:%M"), s, batch, ed, " ".join(c for c, _ in items)])
        rebuild(not trading_hours)
        when = "今晚收盘计划" if trading_hours else "下一交易日计划（已开始重算）"
        return self._json(200, {"message": f"{s}组批次 {batch}：新增 {added} 只、重新入池 {renewed} 只，入池日 {entry}，用于{when}"})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("QISHI_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("QISHI_PORT", "8080")))
    a = ap.parse_args()
    if not TOKEN:
        print("警告：未设置 QISHI_TOKEN，写入接口全部拒绝（只读模式）")
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()
