"""特征计算 + 5个工作室的评分规则（纯标准库）"""
import csv
from statistics import mean, median, pstdev

# ---------------- 数据与特征 ----------------
def load(code):
    with open(f"data/{code}.csv") as f:
        return [{k: (v if k == "date" else float(v)) for k, v in r.items()} for r in csv.DictReader(f)]

def limit_pct(code):
    return 20.0 if code.startswith(("300", "688")) else 10.0

def is_lu(bar, code):
    return bar["pct"] >= limit_pct(code) - 0.15

def lin(x, lo, hi):
    """把 x 线性映射到 0~1；lo>hi 时为反向。"""
    if hi == lo:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))

def peak(x, lo, best_lo, best_hi, hi):
    """区间型打分：落在 [best_lo,best_hi] 得 1，越过 lo/hi 得 0。"""
    if x <= lo or x >= hi:
        return 0.0
    if x < best_lo:
        return (x - lo) / (best_lo - lo)
    if x > best_hi:
        return (hi - x) / (hi - best_hi)
    return 1.0

def vol_ratio(b, i):
    prev = [x["volume"] for x in b[max(0, i - 5):i]]
    return b[i]["volume"] / mean(prev) if prev and mean(prev) > 0 else 1.0

def ma_at(b, k, n):
    return mean(x["close"] for x in b[k - n + 1:k + 1])

def first_breakout(code, b, k):
    """k日是否为“底部首次放量站上20日线”：大阳(>=7%)+量比>=1.8+站上MA20，且此前10天里至少7天在MA20下方"""
    if k < 40:
        return False
    below = sum(1 for j in range(k - 10, k) if b[j]["close"] < ma_at(b, j, 20))
    return (b[k]["pct"] >= 7 and vol_ratio(b, k) >= 1.8
            and b[k]["close"] > ma_at(b, k, 20) and below >= 7)

def second_launch(code, b, i):
    """突破后回踩不破、再次启动：3~15天前出现过首次突破，此后最低价不破突破日最低价，
    今日涨幅>=5%或涨停（一字板缩量也算），收盘高于突破日收盘。西部材料 11-09 就是这一形态。"""
    c = b[i]
    if not (c["pct"] >= 5 or is_lu(c, code)):
        return False
    for k in range(i - 15, i - 2):
        if k > 40 and first_breakout(code, b, k):
            if min(x["low"] for x in b[k + 1:i + 1]) >= b[k]["low"] and c["close"] > b[k]["close"]:
                return True
    return False

def feats(code, b, i):
    """只用 b[0..i] 的数据，保证回测无未来函数。"""
    if i < 70:
        return None
    c = b[i]; C = c["close"]
    w250 = b[max(0, i - 249):i + 1]; w120 = b[max(0, i - 119):i + 1]
    closes = lambda n, k=0: [x["close"] for x in b[i - k - n + 1:i - k + 1]]
    hhv250 = max(x["high"] for x in w250); llv250 = min(x["low"] for x in w250)
    llv120 = min(x["low"] for x in w120)
    boards, j = 0, i
    while j >= 0 and is_lu(b[j], code):
        boards += 1; j -= 1
    w20 = range(i - 19, i + 1)
    big20 = sum(1 for k in w20 if b[k]["pct"] >= 7 and vol_ratio(b, k) >= 1.8)
    prev_close = b[i - 1]["close"]
    rng = c["high"] - c["low"]
    return dict(
        code=code, close=C, pct=c["pct"], turnover=c["turnover"], amount=c["amount"] / 1e8,
        vr=vol_ratio(b, i), lu=is_lu(c, code), boards=boards,
        lu20=sum(1 for k in w20 if is_lu(b[k], code)), big20=big20,
        dd=hhv250 / C, lowpos=C / llv120, range250=hhv250 / llv250,
        ma20=mean(closes(20)), ma60=mean(closes(60)), ma60_5=mean(closes(60, 5)),
        ret5=C / b[i - 5]["close"] - 1, ret20=C / b[i - 20]["close"] - 1,
        close_pos=(C - c["low"]) / rng if rng > 0 else 1.0,
        upper=(c["high"] - C) / prev_close,
        vol20=pstdev([b[k]["pct"] for k in w20]),
        brk1=first_breakout(code, b, i), brk2=second_launch(code, b, i),
        yizi=c["open"] == c["high"] == C and is_lu(c, code),
        prev_lu=is_lu(b[i - 1], code), prev_boards=_boards(code, b, i - 1),
        prev_open=b[i - 1]["open"], prev_low=b[i - 1]["low"], low=c["low"],
    )

def _boards(code, b, i):
    n = 0
    while i >= 0 and is_lu(b[i], code):
        n += 1; i -= 1
    return n

def pe_score(pe):
    if pe is None:
        return 0.0
    return peak(pe, 0, 15, 60, 300)

# ---------------- 5个工作室 ----------------
# 每个函数返回 (是否通过门槛, 总分0~100, 分项)
# ctx: 当日股票池统计（用于相对强弱/补涨判断）

def studio_A(f, t, ctx, p):
    """A 老龙回头（西部材料型）：深跌后底部首次放量突破，或突破后回踩不破再启动，持有5天"""
    gate = (t["theme"] >= p["theme_min"] and f["dd"] >= p["dd_min"]
            and f["lowpos"] <= p["lowpos_max"] and (f["brk1"] or f["brk2"]))
    vol_ok = 1.0 if f["yizi"] else lin(f["vr"], 1.5, 4)  # 一字板缩量是强势，不扣分
    s = {
        "主线题材":   25 * t["theme"],
        "老龙特征":   10 * lin(f["dd"], 1.4, 2.5) + 5 * lin(f["range250"], 1.8, 4) + 5 * t["soe"],
        "量能质量":   12 * vol_ok + 8 * peak(f["turnover"], 1, 2, 6, 12),
        "业绩底线":   15 * pe_score(t["pe"]),
        "低位程度":   10 * lin(f["lowpos"], p["lowpos_max"], 1.1),
        "突破确认":   10 * (1.0 if f["brk2"] else 0.6),  # 回踩确认过的二次启动更可靠
    }
    return gate, sum(s.values()), s

def studio_B(f, t, ctx, p):
    """B 龙头接力：只做当日涨停且近期最强的情绪龙头，持有2天"""
    gate = f["lu"] and (f["boards"] >= p["min_boards"] or f["lu20"] >= p["min_lu20"])
    s = {
        "连板高度":   20 * lin(f["boards"], 1, 4) + 10 * lin(f["lu20"], 1, 5),
        "相对强度":   20 * lin(f["ret20"] - ctx["ret20_med"], 0, 0.6),
        "封板质量":   15 * (1 - lin(f["upper"], 0, 0.03)),
        "换手活跃":   15 * peak(f["turnover"], 2, 5, 18, 30),
        "主线题材":   15 * t["theme"],
        "流动性":     5 * lin(f["amount"], 3, 20),
    }
    return gate, sum(s.values()), s

def studio_C(f, t, ctx, p):
    """C 主线中军：大成交额、均线多头、不追涨停，持有10天"""
    gate = (f["close"] > f["ma20"] > f["ma60"] and f["ma60"] > f["ma60_5"]
            and f["amount"] >= p["amt_min"] and f["pct"] < 7
            and ctx["ret20_med"] > p["regime_min"])  # 股票池整体20日中位涨幅为正才做中线
    s = {
        "主线题材":   25 * t["theme"],
        "国资背景":   15 * t["soe"],
        "趋势健康":   20 * peak(f["close"] / f["ma60"], 1.0, 1.05, 1.25, 1.5),
        "波动可控":   15 * lin(f["vol20"], 6, 2),
        "成交额":     15 * lin(f["amount"], p["amt_min"], 30),
        "不追高":     10 * lin(f["pct"], 7, 1),
    }
    return gate, sum(s.values()), s

def studio_D(f, t, ctx, p):
    """D 分歧低吸：昨日首板/二板，今日分歧（不涨停、不大跌），且守住昨日涨停阳线的开盘价，持有3天
    （前两版“主线内落后补涨”“强势回踩20日线”回测超额为负，已淘汰）"""
    gate = (t["theme"] >= p["theme_min"] and f["prev_lu"] and 1 <= f["prev_boards"] <= p["max_boards"]
            and not f["lu"] and p["pct_lo"] <= f["pct"] <= 5
            and f["close"] >= f["prev_open"])
    s = {
        "承接强度":   25 * lin(f["close"] / f["prev_open"], 1.0, 1.10),
        "分歧换手":   20 * peak(f["turnover"], 3, 6, 15, 25),
        "下影承接":   15 * lin(f["close_pos"], 0.3, 1.0),
        "低位":       15 * lin(f["lowpos"], 2.0, 1.2),
        "主线题材":   15 * t["theme"],
        "业绩底线":   10 * pe_score(t["pe"]),
    }
    return gate, sum(s.values()), s

def studio_E(f, t, ctx, p):
    """E 量价资金：不看题材和基本面，只看资金行为（对照组），持有3天"""
    gate = f["vr"] >= p["vr_min"] and f["pct"] > 0 and f["close_pos"] >= 0.7 and f["turnover"] <= p["turn_max"]
    s = {
        "量比":       30 * lin(f["vr"], p["vr_min"], 4),
        "换手区间":   20 * peak(f["turnover"], 1, 3, 8, p["turn_max"]),
        "收盘强度":   15 * f["close_pos"],
        "5日相对强弱": 15 * lin(f["ret5"] - ctx["ret5_med"], -0.05, 0.15),
        "成交额":     10 * lin(f["amount"], 2, 20),
        "当日涨幅":   10 * lin(f["pct"], 0, 7),
    }
    return gate, sum(s.values()), s

STUDIOS = {
    "A 老龙回头": dict(fn=studio_A, hold=5,  params=dict(theme_min=0.5, dd_min=1.4, lowpos_max=1.6),
                    grid=dict(theme_min=[0.5], dd_min=[1.25, 1.4, 1.6], lowpos_max=[1.4, 1.6, 1.8])),
    "B 龙头接力": dict(fn=studio_B, hold=2,  params=dict(min_boards=2, min_lu20=2),
                    grid=dict(min_boards=[1, 2, 3], min_lu20=[2, 3, 99])),
    "C 主线中军": dict(fn=studio_C, hold=10, params=dict(amt_min=8, regime_min=0.0),
                    grid=dict(amt_min=[3, 8, 15], regime_min=[-1.0, 0.0, 0.03])),
    "D 分歧低吸": dict(fn=studio_D, hold=3,  params=dict(theme_min=0.5, max_boards=2, pct_lo=-5),
                    grid=dict(theme_min=[0.5], max_boards=[1, 2, 3], pct_lo=[-5, -2])),
    "E 量价资金": dict(fn=studio_E, hold=3,  params=dict(vr_min=1.8, turn_max=15),
                    grid=dict(vr_min=[1.5, 1.8, 2.5], turn_max=[10, 15, 25])),
}

def day_ctx(fs, tags):
    r20 = [f["ret20"] for f in fs]; r5 = [f["ret5"] for f in fs]
    theme_r20 = [f["ret20"] for f in fs if tags[f["code"]]["theme"] >= 0.5] or r20
    return dict(ret20_med=median(r20), ret5_med=median(r5), ret20_theme_med=median(theme_r20))
