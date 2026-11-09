"""行情数据：腾讯行情（前复权日线、分时、市值快照），本地 CSV 缓存。

v3.3 起全部改用腾讯行情。东方财富接口在境外服务器不可达，已移除，不再需要任何“守卫”补丁。

日线字段：date,open,close,high,low,volume(手),amount(元),pct(%),turnover(%)
- 指标（均线、涨幅）用前复权序列；
- 本项目是日线模拟，成交价用日线/分时价格代理（见 RULES.md 第三节）。
"""
import csv
import json
import os
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAR_DIR = os.path.join(ROOT, "data", "bars")
# 个股：newfqkline 每行 [日期, 开, 收, 高, 低, 成交量(手), {}, 换手率%, 成交额(万元), ...]
STOCK_KLINE = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param={sym},day,,,{lmt},qfq"
# 指数：fqkline 每行 [日期, 开, 收, 高, 低, 成交量]
INDEX_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{lmt},qfq"
MINUTE = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={sym}"
QUOTE = "https://qt.gtimg.cn/q={syms}"
HEADER = ["date", "open", "close", "high", "low", "volume", "amount", "pct", "turnover"]
HS300 = "000300"  # 沪深300指数，基准
SOURCE = "腾讯行情"


def tx_symbol(code, index=False):
    if index:
        return ("sh" if code.startswith("000") else "sz") + code
    if code.startswith("92"):
        return "bj" + code
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def _get(url, retries=3, raw=False):
    for k in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                body = r.read()
            return body if raw else json.loads(body)
        except Exception:  # 网络抖动重试
            if k == retries - 1:
                raise
            time.sleep(1 + k)


def _num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def fetch_bars(code, index=False, lmt=500):
    sym = tx_symbol(code, index)
    url = (INDEX_KLINE if index else STOCK_KLINE).format(sym=sym, lmt=lmt)
    node = (_get(url).get("data") or {}).get(sym) or {}
    raw = node.get("qfqday") or node.get("day") or []
    rows, prev = [], None
    for r in raw:
        o, c, h, l, v = (_num(x) for x in r[1:6])
        if not index and code.startswith(("688", "689")):
            v /= 100  # 腾讯科创板成交量单位是股，统一为手（与其他板块一致）
        amount = _num(r[8]) * 1e4 if not index and len(r) > 8 else 0.0
        turnover = _num(r[7]) if not index and len(r) > 7 else 0.0
        pct = round((c / prev - 1) * 100, 2) if prev else 0.0
        rows.append([r[0], o, c, h, l, v, round(amount, 2), pct, turnover])
        prev = c
    if not rows:
        return 0
    os.makedirs(BAR_DIR, exist_ok=True)
    with open(os.path.join(BAR_DIR, f"{code}{'_idx' if index else ''}.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    return len(rows)


def minute_price(code, date, hhmm="1430"):
    """当日分时价（腾讯分时只提供当天）。拿不到返回 None，由调用方改用收盘代理。"""
    sym = tx_symbol(code)
    try:
        d = _get(MINUTE.format(sym=sym))["data"][sym]["data"]
    except Exception:
        return None
    if d.get("date") != date.replace("-", ""):
        return None
    for row in d.get("data", []):
        t, px = row.split()[:2]
        if t >= hhmm:
            return _num(px, None)
    return None


def fetch_quotes(codes):
    """当前名称、价格、总市值（元）。字段46=总市值（亿元）。"""
    out = {}
    codes = list(codes)
    for i in range(0, len(codes), 60):
        chunk = codes[i:i + 60]
        text = _get(QUOTE.format(syms=",".join(tx_symbol(c) for c in chunk)), raw=True).decode("gbk", errors="replace")
        for line in text.split(";"):
            f = line.split("~")
            if len(f) > 46 and f[2]:
                cap = _num(f[45], None)
                out[f[2]] = dict(name=f[1], price=_num(f[3]), mcap=cap * 1e8 if cap else None)
        time.sleep(0.2)
    return out


def load_bars(code, index=False):
    path = os.path.join(BAR_DIR, f"{code}{'_idx' if index else ''}.csv")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [{k: (v if k == "date" else float(v)) for k, v in r.items()} for r in csv.DictReader(f)]


def update_all(codes, sleep=0.15):
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
    if quotes:
        with open(os.path.join(ROOT, "data", "quotes.json"), "w") as f:
            json.dump(quotes, f, ensure_ascii=False, indent=0)
    return failed


def load_quotes():
    path = os.path.join(ROOT, "data", "quotes.json")
    return json.load(open(path)) if os.path.exists(path) else {}
