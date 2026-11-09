"""组合模拟器：复刻擂台的成交口径（RULES.md 第三节）。

时序（信号日 T）：
- T 收盘：检查持仓退出 → 次日 T+1 开盘卖出（一字跌停卖不出，顺延）
- T 收盘：对池内股票评分排序，生成顺延链
- T+1 14:30–15:00 买入：用 T+1 收盘价代理；开盘涨幅>gap_skip 或价格>最高买价或封涨停 → 顺延下一只
仓位：每笔 = 总资产×position（向下取整到100股）；现金不足则跳过。
"""
import math
from statistics import mean

from studios import STUDIOS, rank_composite


def fees(amount, sell):
    return max(amount * 0.00025, 5.0) + amount * 0.00001 + (amount * 0.0005 if sell else 0.0)


class Universe:
    """把各股的特征按日期索引，便于横截面取数。"""

    def __init__(self, feats_by_code, pool_rows, calendar):
        self.f = feats_by_code                       # code -> [feat|None]
        self.idx = {c: {x["date"]: i for i, x in enumerate(fs) if x} for c, fs in feats_by_code.items()}
        self.pool = {r["code"]: r for r in pool_rows}
        self.cal = calendar

    def get(self, code, date):
        i = self.idx.get(code, {}).get(date)
        return None if i is None else self.f[code][i]

    def next_bar(self, code, date):
        i = self.idx.get(code, {}).get(date)
        if i is None or i + 1 >= len(self.f[code]):
            return None
        return self.f[code][i + 1]


def eligible(u, code, date, mode):
    """real：入池日之后才可交易；extended：全期可交易，但剔除入池前20个交易日（选股偏差区）。"""
    ed = u.pool[code]["entry_date"]
    d = date.replace("-", "")
    if mode == "real":
        return d >= ed
    if d >= ed:
        return True
    try:
        k = u.cal.index(date)
        ahead = u.cal[k:k + 21]
        return not any(x.replace("-", "") >= ed for x in ahead)
    except ValueError:
        return False


def candidates(u, studio, p, date, held, mode):
    st = STUDIOS[studio]
    out = []
    for code in u.pool:
        if code in held or not eligible(u, code, date, mode):
            continue
        f = u.get(code, date)
        if not f or f["st"] or (f["mcap"] is not None and f["mcap"] < p.get("mcap_min", 70)):
            continue
        if p.get("skip_signal_lu") and f["lu"]:
            continue
        if st["gate"](f, p):
            out.append(f)
    return out


def plan_buys(u, studio, p, date, held, mode):
    """返回顺延链：[(score, f, sig, ver, auto, layer)]。新批优先 → 自主优先 → 综合分。"""
    ranked = rank_composite(candidates(u, studio, p, date, held, mode), studio, p)
    if not p.get("must_open", True):
        ranked = [r for r in ranked if r[4]]
    d = date.replace("-", "")
    layer = lambda r: 0 if (mode == "real" and u.pool[r[1]["code"]]["entry_date"] == d) else 1
    if not p.get("new_batch_first", True):
        layer = lambda r: 1
    ranked = [r + (layer(r),) for r in ranked]
    ranked.sort(key=lambda r: (r[5], not r[4]))  # 稳定排序：层级、自主在前，层内保持综合分顺序
    return ranked


def exit_signal(pos, f, p):
    """收盘检查。返回退出原因或 None。"""
    if f["close"] < pos["cost"] * p.get("stop", 0.95):
        return "硬止损"
    tp = p.get("take_profit")
    if tp and f["close"] >= pos["cost"] * (1 + tp):
        return "止盈"
    mode = p.get("exit", "ma5")
    if mode == "ma5" and f["close"] < f["ma5"]:
        return "MA5"
    if mode == "ma5_confirm":
        below = f["close"] < f["ma5"]
        if below and pos.get("below_ma5"):
            return "MA5确认"
        pos["below_ma5"] = below
    if mode == "ma10" and f["close"] < f["ma10"]:
        return "MA10"
    if mode == "low60" and f["close"] < f["llv60"] * 1.0001:
        return "创60日新低"
    hn = p.get("hold_max")
    if hn and pos["days"] >= hn:
        return f"持有满{hn}日"
    return None


def run(u, studio, p, start, end, mode="real", cash=100000.0, positions=None):
    cal = [d for d in u.cal if start <= d <= end]
    pos = {k: dict(v) for k, v in (positions or {}).items()}
    trades, nav_hist, pending_sell, pending_buy = [], [], {}, None
    for d in cal:
        # 1) 开盘卖出
        for code, reason in list(pending_sell.items()):
            f = u.get(code, d)
            if not f:
                continue
            if f["yizi_down"]:
                continue  # 一字跌停卖不出，次日再试
            px = f["open"]; q = pos[code]["qty"]; amt = px * q
            cash += amt - fees(amt, True)
            pnl = amt - fees(amt, True) - pos[code]["cost_total"]
            trades.append(dict(studio=studio, code=code, buy_date=pos[code]["date"], buy=pos[code]["cost"],
                               sell_date=d, sell=px, qty=q, pnl=round(pnl, 2), reason=reason,
                               auto=pos[code]["auto"], score=pos[code]["score"],
                               sig_lu=pos[code]["sig_lu"], days=pos[code]["days"]))
            del pos[code]; del pending_sell[code]
        # 2) 14:30 后买入（用当日收盘代理）
        nav_open = cash + sum(pv["qty"] * (u.get(c, d) or {"close": pv["last"]})["close"] for c, pv in pos.items())
        if pending_buy:
            for score, f0, sig, ver, auto, layer in pending_buy:
                f = u.get(f0["code"], d)
                if not f:
                    continue
                mult = p.get("max_buy", 1.03)
                if f0["lu"] and p.get("max_buy_prev_lu"):
                    mult = p["max_buy_prev_lu"]
                if f["open"] / f0["close"] - 1 > p.get("gap_skip", 0.05):
                    continue
                if f["close"] > f0["close"] * mult or (f["lu"] and f["sealed"]):
                    continue
                if p.get("a_pullback") and f["low"] > f0["close"]:
                    continue  # A 组回踩条件单：盘中需回到昨收以下
                budget = nav_open * p.get("position", 0.10)
                q = int(budget / (f0["close"] * mult) / 100) * 100
                amt = q * f["close"]
                if q == 0 or amt + fees(amt, False) > cash:
                    continue
                cash -= amt + fees(amt, False)
                pos[f["code"]] = dict(qty=q, cost=f["close"], cost_total=amt + fees(amt, False), date=d,
                                      auto=auto, score=round(score, 1), sig_lu=f0["lu"], days=0, last=f["close"])
                break
        pending_buy = None
        # 3) 收盘：盯市 + 退出检查
        for code, pv in pos.items():
            f = u.get(code, d)
            if not f:
                continue
            pv["last"] = f["close"]
            pv["days"] += 1
            if code not in pending_sell:
                r = exit_signal(pv, f, p)
                if r:
                    pending_sell[code] = r
        nav = cash + sum(pv["qty"] * pv["last"] for pv in pos.values())
        nav_hist.append((d, nav))
        # 4) 次日买入计划
        pending_buy = plan_buys(u, studio, p, d, set(pos), mode)
    return dict(trades=trades, nav=nav_hist, cash=cash, positions=pos, pending_sell=pending_sell,
                pending_buy=pending_buy)


def metrics(res, start_nav=100000.0):
    t = res["trades"]; nav = [v for _, v in res["nav"]]
    if not nav:
        return {}
    peak, mdd = nav[0], 0.0
    for v in nav:
        peak = max(peak, v); mdd = max(mdd, 1 - v / peak)
    wins = [x["pnl"] for x in t if x["pnl"] > 0]; loss = [x["pnl"] for x in t if x["pnl"] <= 0]
    return dict(ret=nav[-1] / start_nav - 1, mdd=mdd, n=len(t),
                win=len(wins) / len(t) if t else 0.0,
                avg_win=mean(wins) if wins else 0.0, avg_loss=mean(loss) if loss else 0.0,
                payoff=(mean(wins) / -mean(loss)) if wins and loss and mean(loss) < 0 else math.nan)
