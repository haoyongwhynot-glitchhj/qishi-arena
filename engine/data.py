"""行情数据：东方财富公开接口（前复权日线 + 市值快照），本地 CSV 缓存。

日线字段：date,open,close,high,low,volume,amount,pct,turnover
- 指标（均线、涨幅）用前复权序列；
- 本项目是日线模拟，成交价用日线价格代理（见 RULES.md 第三节）。
"""
import csv
import json
import os
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAR_DIR = os.path.join(ROOT, "data", "bars")
KLINE_URL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
             "&fields1=f1,f2&fields2=f51,f52,f53,f54,f55,f56,f57,f59,f61"
             "&klt=101&fqt=1&end=20500101&lmt={lmt}")
QUOTE_URL = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids={secids}"
             "&fields=f12,f14,f2,f116")
HEADER = ["date", "open", "close", "high", "low", "volume", "amount", "pct", "turnover"]
HS300 = "000300"  # 沪深300指数，基准


def secid(code, index=False):
    if index:
        return ("1." if code.startswith("000") else "0.") + code
    if code.startswith(("6", "9")) and not code.startswith("92"):
        return "1." + code
    return "0." + code  # 深市与北交所(92xxxx)均用 0.


def _get(url, retries=3):
    for k in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.load(r)
        except Exception:  # 网络抖动重试
            if k == retries - 1:
                raise
            time.sleep(1 + k)


def fetch_bars(code, index=False, lmt=500):
    d = _get(KLINE_URL.format(secid=secid(code, index), lmt=lmt))["data"]
    rows = [r.split(",") for r in (d["klines"] if d else [])]
    os.makedirs(BAR_DIR, exist_ok=True)
    with open(os.path.join(BAR_DIR, f"{code}{'_idx' if index else ''}.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    return len(rows)


def _tx(code):
    if code.startswith("92"):
        return "bj" + code
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def fetch_quotes(codes):
    """当前总市值(元)与名称，用于 70 亿门槛和 ST 判断。来源：腾讯行情（字段46=总市值，单位亿元）。
    东方财富 ulist 接口的 f116 在本环境返回 0，已弃用。"""
    out = {}
    codes = list(codes)
    for i in range(0, len(codes), 60):
        chunk = codes[i:i + 60]
        url = "https://qt.gtimg.cn/q=" + ",".join(_tx(c) for c in chunk)
        with urllib.request.urlopen(url, timeout=20) as r:
            text = r.read().decode("gbk", errors="replace")
        for line in text.split(";"):
            f = line.split("~")
            if len(f) > 46 and f[2]:
                try:
                    cap = float(f[45]) * 1e8
                except ValueError:
                    cap = None
                out[f[2]] = dict(name=f[1], price=float(f[3] or 0), mcap=cap or None)
        time.sleep(0.2)
    return out


def load_bars(code, index=False):
    path = os.path.join(BAR_DIR, f"{code}{'_idx' if index else ''}.csv")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [{k: (v if k == "date" else float(v)) for k, v in r.items()} for r in csv.DictReader(f)]


def update_all(codes, sleep=0.25):
    """拉取全部池内股票 + 沪深300。返回失败清单（不中断）。"""
    failed = []
    for c in sorted(set(codes)):
        try:
            if fetch_bars(c) == 0:
                failed.append(c)
        except Exception as e:
            failed.append(f"{c}:{e.__class__.__name__}")
        time.sleep(sleep)
    fetch_bars(HS300, index=True)
    quotes = fetch_quotes(codes)
    with open(os.path.join(ROOT, "data", "quotes.json"), "w") as f:
        json.dump(quotes, f, ensure_ascii=False, indent=0)
    return failed


def load_quotes():
    path = os.path.join(ROOT, "data", "quotes.json")
    return json.load(open(path)) if os.path.exists(path) else {}
