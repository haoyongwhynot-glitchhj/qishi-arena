"""成交结算：按 RULES.md 第三节执行上一交易日签发的指令。

- 卖出：当日开盘价（09:30 分时首价），一字跌停卖不出 → 顺延
- 买入：当日 14:30 分时价（腾讯分时，只能当天取；当天没取到——例如结算延误或接口失败——就用当日收盘价代理，
  并在持仓的 fill_src 字段、台账和看板上标注“收盘代理”）；沿顺延链只买 1 只
- 收盘：盯市、记净值（含沪深300基准，基准起点 2026-10-09 收盘）
用法：python3 settle.py [YYYY-MM-DD]
"""
import csv
import json
import os
import sys

import data
import lab
from features import limit_pct
from sim import fees

BASE_DATE = "2026-10-09"


def minute_price(code, date):
    """14:30 分时价（腾讯分时，仅当天可取）。取不到返回 None → 按收盘价代理成交并在台账/看板标注。"""
    return data.minute_price(code, date, "1430")


def bar_on(code, date):
    bs = data.load_bars(code)
    for k, b in enumerate(bs):
        if b["date"] == date:
            return b, (bs[k - 1] if k else None)
    return None, None


def settle_studio(s, date, idx):
    path = os.path.join(lab.ROOT, "studios", s, "ledger.json")
    L = json.load(open(path))
    if L.get("as_of", "") >= date:
        return f"{s}: 已结算到 {L['as_of']}，跳过"
    p = lab.load_genome(s)["params"]
    log, keep = [], []
    L.setdefault("trades", [])
    # 1) 开盘卖出
    for order in L.get("pending_sell", []):
        h = next((x for x in L["positions"] if x["code"] == order["code"]), None)
        b, _ = bar_on(order["code"], date)
        if not h or not b:
            keep.append(order); continue
        if b["open"] == b["high"] == b["low"] and b["pct"] < 0:
            keep.append(order); log.append(f"{h['name']} 一字跌停卖不出，顺延"); continue
        px = b["open"]; amt = px * h["qty"]; net = amt - fees(amt, True)
        pnl = round(net - h["cost_total"], 2)
        L["cash"] += net
        L["positions"].remove(h)
        t = dict(studio=s, code=h["code"], name=h["name"], buy_date=h["date"], buy=h["cost"], qty=h["qty"],
                 sell_date=date, sell=px, pnl=pnl, exit_reason=order["reason"], entry_type=h.get("type", ""),
                 fill_src=h.get("fill_src", "v3前成交"))
        L["trades"].append(t)
        with open(os.path.join(lab.ROOT, "data", "real_trades.csv"), "a", newline="") as f:
            csv.writer(f).writerow([t[k] for k in ["studio", "code", "name", "buy_date", "buy", "qty", "sell_date",
                                                   "sell", "pnl", "exit_reason", "entry_type"]])
        log.append(f"卖出 {h['code']} {h['name']} {h['qty']}股 @{px}（{order['reason']}），盈亏 {pnl:+.2f}")
    L["pending_sell"] = keep
    # 2) 14:30 买入（顺延链只成交 1 只）
    nav_prev = L["nav"]
    held = {h["code"] for h in L["positions"]}

    def first_fill(chain, cash, notes):
        """沿顺延链找第一只可成交的；返回 (候选, 成交价, 是否收盘代理, 股数, 总成本) 或 None。"""
        for c in chain:
            if c["code"] in held:
                continue
            b, prev = bar_on(c["code"], date)
            if not b or not prev:
                notes.append(f"跳过 {c['name']}：无行情"); continue
            if b["open"] / c["signal_close"] - 1 > p.get("gap_skip", 0.05):
                notes.append(f"跳过 {c['name']}：开盘涨幅 {b['open'] / c['signal_close'] - 1:+.1%} > 5%"); continue
            px = minute_price(c["code"], date); proxy = px is None
            px = b["close"] if proxy else px
            up = round(c["signal_close"] * (1 + limit_pct(c["code"]) / 100), 2)
            if px > c["max_buy"]:
                notes.append(f"跳过 {c['name']}：14:30 价 {px} > 最高买价 {c['max_buy']}"); continue
            if px >= up - 0.005:
                notes.append(f"跳过 {c['name']}：封涨停不可买"); continue
            q = int(nav_prev * p.get("position", 0.10) / c["max_buy"] / 100) * 100
            amt = q * px; cost_total = amt + fees(amt, False)
            if q == 0 or cost_total > cash:
                notes.append(f"跳过 {c['name']}：现金不足"); continue
            return c, px, proxy, q, cost_total
        return None

    chain = L.get("pending_buy", [])
    rv = L.get("review") or {}
    reviewed = rv.get("status") == "已复核"
    vetoed = [c for c in chain if (c.get("review") or {}).get("decision") == "否决"]
    for c in vetoed:
        log.append(f"复核否决 {c['code']} {c['name']}：{c['review'].get('reason', '')}")
    live = [c for c in chain if c not in vetoed]
    pick = first_fill(live, L["cash"], log)
    if pick:
        c, px, proxy, q, cost_total = pick
        L["cash"] -= cost_total
        L["positions"].append(dict(code=c["code"], name=c["name"], date=date, qty=q, cost=px,
                                   cost_total=round(cost_total, 2), type=c["type"],
                                   review="已复核" if reviewed else "未复核",
                                   fill_src="收盘代理" if proxy else "14:30分时"))
        log.append(f"买入 {c['code']} {c['name']} {q}股 @{px}{'（收盘代理）' if proxy else '（14:30分时）'}"
                   f"【{c['type']}】【{c['layer']}】【{'已复核' if reviewed else '未复核，按公式执行'}】")
    elif chain:
        log.append("顺延链全部失效（或全部被复核否决）：当日不买，已记录")
    # 复核效果记账：公式本来会买的那只 vs 实际买的那只（evolve.py 每周统计后续收益）
    if chain:
        mech = first_fill(chain, L["cash"] + (pick[4] if pick else 0), [])
        with open(os.path.join(lab.ROOT, "data", "review_log.csv"), "a", newline="") as f:
            w = csv.writer(f)
            if f.tell() == 0:
                w.writerow(["date", "studio", "reviewed", "mech_code", "mech_name", "mech_px",
                            "pick_code", "pick_name", "pick_px", "vetoed"])
            w.writerow([date, s, int(reviewed), mech[0]["code"] if mech else "", mech[0]["name"] if mech else "",
                        mech[1] if mech else "", pick[0]["code"] if pick else "", pick[0]["name"] if pick else "",
                        pick[1] if pick else "", " ".join(c["code"] for c in vetoed)])
    L["pending_buy"] = []
    L["review"] = None
    # 3) 收盘盯市
    mv = 0.0
    for h in L["positions"]:
        b, _ = bar_on(h["code"], date)
        h["last"] = b["close"] if b else h.get("last", h["cost"])
        h.setdefault("cost_total", round(h["cost"] * h["qty"] + 5, 2))
        mv += h["last"] * h["qty"]
    L["nav"] = round(L["cash"] + mv, 2)
    L["as_of"] = date
    hs = (idx.get(date) / idx[BASE_DATE] - 1) if idx.get(date) and idx.get(BASE_DATE) else None
    L.setdefault("nav_history", []).append(dict(date=date, nav=L["nav"], ret=L["nav"] / 1e5 - 1, hs300=hs))
    with open(os.path.join(lab.ROOT, "studios", s, "nav_history.csv"), "a", newline="") as f:
        csv.writer(f).writerow([date, L["nav"], "" if hs is None else round(hs, 5)])
    json.dump(L, open(path, "w"), ensure_ascii=False, indent=1)
    write_md(s, L)
    return f"{s}: " + ("；".join(log) or "无成交") + f"；收盘总资产 {L['nav']:,.2f}"


def write_md(s, L):
    g = lab.load_genome(s)
    peak, mdd = 1e5, 0.0
    for x in L.get("nav_history", []):
        peak = max(peak, x["nav"]); mdd = max(mdd, 1 - x["nav"] / peak)
    md = [f"# {s} {g['name']} 台账（截至 {L['as_of']} 收盘，genome {g['version']}）", "",
          f"- 总资产 ¥{L['nav']:,.2f}，累计 {L['nav'] / 1e5 - 1:+.2%}；现金 ¥{L['cash']:,.2f}；本账本记录的最大回撤 {mdd:.2%}",
          "- 2026-11-09 之前的历史成交见 data/real_trades.csv", "", "## 持仓", "",
          "| 代码 | 名称 | 买入日 | 股数 | 成本 | 买入价来源 | 现价 | 浮盈亏 | 类型 |", "|---|---|---|---|---|---|---|---|---|"]
    for h in L["positions"]:
        last = h.get("last", h["cost"])
        md.append(f"| {h['code']} | {h['name']} | {h['date']} | {h['qty']} | {h['cost']} | {h.get('fill_src', 'v3前成交')} | {last} | "
                  f"{(last - h['cost']) * h['qty']:+.0f} | {h.get('type', '')} |")
    md += ["", "## 待执行（下一交易日）"]
    md += [f"- 开盘卖出 {o['code']} {o['name']}（{o['reason']}）" for o in L.get("pending_sell", [])]
    rv = L.get("review") or {}
    if L.get("pending_buy"):
        md.append(f"- 个股复核：{rv.get('status', '待复核')}" + (f"（{rv.get('time')}）{rv.get('summary', '')}" if rv.get("time") else ""))
    md += [f"- 14:30 买入顺延链 {k}. {c['code']} {c['name']} 最高买价 {c['max_buy']}【{c['type']}】【{c['layer']}】"
           + (f" 复核：{c['review']['decision']}（{c['review'].get('reason', '')}）" if c.get("review") else "")
           for k, c in enumerate(L.get("pending_buy", []), 1)] or []
    md += ["", "## v3 成交", "", "| 买入日 | 代码 | 名称 | 买价 | 卖出日 | 卖价 | 盈亏 | 原因 |", "|---|---|---|---|---|---|---|---|"]
    md += [f"| {t['buy_date']} | {t['code']} | {t['name']} | {t['buy']} | {t['sell_date']} | {t['sell']} | {t['pnl']:+.2f} | {t['exit_reason']} |"
           for t in L.get("trades", [])]
    md += ["", "## 每日净值", "", "| 日期 | 总资产 | 累计 | 沪深300(自10-09) | 超额 |", "|---|---|---|---|---|"]
    for x in L.get("nav_history", []):
        hs = x.get("hs300")
        hs_s = "" if hs is None else f"{hs:+.2%}"
        ex_s = "" if hs is None else f"{x['ret'] - hs:+.2%}"
        md.append(f"| {x['date']} | {x['nav']:,.2f} | {x['ret']:+.2%} | {hs_s} | {ex_s} |")
    open(os.path.join(lab.ROOT, "studios", s, "ledger.md"), "w").write("\n".join(md) + "\n")


def run(date=None):
    idx = {b["date"]: b["close"] for b in data.load_bars(data.HS300, index=True)}
    date = date or max(idx)
    return [settle_studio(s, date, idx) for s in "ABCDE"]


if __name__ == "__main__":
    print("\n".join(run(sys.argv[1] if len(sys.argv) > 1 else None)))
