"""每日收盘后运行：池子删除检查 → 持仓退出检查 → 全池评分 → 次日买入顺延链。

用法：python3 daily.py [--fetch]
输出：reports/plan_<日期>.md（五组计划）、reports/scores/<日期>_<组>.csv（全池评分）、pools/<组>.csv（删除后）
"""
import csv
import json
import os
import sys

import data
import lab
import sim
from studios import rank_composite

ROOT = lab.ROOT


def load_ledger(s):
    with open(os.path.join(ROOT, "studios", s, "ledger.json")) as f:
        return json.load(f)


def pool_cleanup(s, u, date, p):
    """按 RULES.md 第二节删除规则全量检查；超过50只按入池以来收益从低到高淘汰。"""
    idx = {b["date"]: b["close"] for b in data.load_bars(data.HS300, index=True)}
    keep, removed = [], []
    for r in lab.load_pool(s):
        c = r["code"]; fs = [x for x in u.f.get(c, []) if x]
        ed = r["entry_date"]; ed = f"{ed[:4]}-{ed[4:6]}-{ed[6:]}"
        since = [x for x in fs if x["date"] >= ed]
        if not since:
            keep.append((r, 0.0)); continue
        entry = since[0]["close"]; last = since[-1]
        why = None
        if last["st"]:
            why = "⑤ST"
        elif sum(1 for x in since[-3:] if x["mcap"] is not None and x["mcap"] < 70) == 3 and len(since) >= 3 \
                and (since[0]["mcap"] or 999) >= 70:
            why = "①市值连续3日<70亿"
        elif last["close"] < entry * 0.85:
            why = "②跌破入池价-15%"
        elif len(since) >= 6 and all(x["close"] < entry for x in since[-5:]):
            a, b = idx.get(since[-6]["date"]), idx.get(last["date"])
            if a and b and last["close"] / since[-6]["close"] < b / a:
                why = "③连续5日低于入池价且跑输沪深300"
        if not why and len(since) >= 11 and last["ret5"] < 0:
            st = sim.STUDIOS[s]
            if not any(st["auto"](x, p) for x in since):
                why = "④满10日无信号且5日涨幅<0"
        if why:
            removed.append((r, why))
        else:
            keep.append((r, last["close"] / entry - 1))
    if len(keep) > 50:
        # 授权期内（入池10个交易日内）的新批次不参与超限淘汰，只淘汰老池里表现最差的
        recent = set(u.cal[-10:])
        fresh = lambda r: f"{r['entry_date'][:4]}-{r['entry_date'][4:6]}-{r['entry_date'][6:]}" in recent
        old = sorted([x for x in keep if not fresh(x[0])], key=lambda x: x[1])
        cut = min(len(keep) - 50, len(old))
        for r, ret in old[:cut]:
            removed.append((r, f"超限淘汰(入池以来{ret:+.1%})"))
        drop = {id(x[0]) for x in old[:cut]}
        keep = [x for x in keep if id(x[0]) not in drop]
    rows = sorted((r for r, _ in keep), key=lambda r: (r["entry_date"], r["code"]))
    with open(os.path.join(ROOT, "pools", f"{s}.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["code", "name", "batch", "entry_date"])
        w.writeheader(); w.writerows(rows)
    with open(os.path.join(ROOT, "pools", "removed.csv"), "a", newline="") as f:
        w = csv.writer(f)
        for r, why in removed:
            w.writerow([date, s, r["code"], r["name"], r["batch"], why])
    return rows, removed


def exit_checks(s, u, date, p, ledger):
    out = []
    for h in ledger["positions"]:
        f = u.get(h["code"], date)
        if not f:
            out.append((h, None, "无行情，标NA")); continue
        pos = dict(cost=h["cost"], days=sum(1 for d in u.cal if h["date"] <= d <= date))
        if p.get("exit") == "ma5_confirm":
            i = u.idx[h["code"]][date]; prev = u.f[h["code"]][i - 1]
            pos["below_ma5"] = prev["close"] < prev["ma5"] and prev["date"] > h["date"]
        out.append((h, f, sim.exit_signal(pos, f, p)))
    return out


def run(fetch=False):
    if fetch:
        codes = set()
        for s in "ABCDE":
            codes |= {r["code"] for r in lab.load_pool(s)}
        data.update_all(codes)
    os.makedirs(os.path.join(ROOT, "reports", "scores"), exist_ok=True)
    lines = []
    date = None
    for s in "ABCDE":
        g = lab.load_genome(s); p = g["params"]; ledger = load_ledger(s)
        held_codes = [h["code"] for h in ledger["positions"]]
        u = lab.universe(s); date = u.cal[-1]
        pool, removed = pool_cleanup(s, u, date, p)
        u_all = lab.universe(s, extra=held_codes)
        u = sim.Universe(u_all.f, pool, u.cal)
        held = {h["code"] for h in ledger["positions"]}
        exits = exit_checks(s, u, date, p, ledger)
        cands = sim.candidates(u, s, p, date, held, "real")
        ranked = rank_composite(cands, s, p)
        with open(os.path.join(ROOT, "reports", "scores", f"{date}_{s}.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["rank", "code", "name", "composite", "signal", "verify", "autonomous", "close", "pct", "vr"])
            names = {r["code"]: r["name"] for r in pool}
            for k, (sc, x, sig, ver, auto) in enumerate(ranked, 1):
                w.writerow([k, x["code"], names.get(x["code"], ""), round(sc, 1), round(sig, 1), round(ver, 1),
                            int(auto), x["close"], x["pct"], round(x["vr"], 2)])
        chain = sim.plan_buys(u, s, p, date, held, "real")[:5]
        if p.get("risk_pause"):
            chain = []
        # 写回台账：明日开盘卖单 + 14:30 顺延链（由 settle.py 执行）
        mb0 = p.get("max_buy", 1.03)
        ledger["pending_sell"] = [dict(code=h["code"], name=h["name"], reason=why)
                                  for h, f, why in exits if f is not None and why]
        ledger["pending_buy"] = [dict(code=x["code"], name=names.get(x["code"], ""), signal_close=x["close"],
                                      max_buy=round(x["close"] * (p.get("max_buy_prev_lu", mb0) if x["lu"] else mb0), 2),
                                      score=round(sc, 1), type="自主" if auto else "强制",
                                      layer="新批" if layer == 0 else "老池")
                                 for sc, x, sig, ver, auto, layer in chain]
        ledger.pop("pending_exits", None)
        # 个股复核（方案A）：计划生成后为“待复核”；复核写入后为“已复核”；到执行时仍未复核则按公式执行
        ledger["review"] = dict(status="待复核" if ledger["pending_buy"] else "无需复核", plan_date=date)
        json.dump(ledger, open(os.path.join(ROOT, "studios", s, "ledger.json"), "w"), ensure_ascii=False, indent=1)
        lines += [f"## {s} {g['name']}（genome {g['version']}）", "",
                  f"- 池子：{len(pool)} 只；本次删除 {len(removed)} 只" + (
                      "（" + "、".join(f"{r['name']}:{why}" for r, why in removed[:8]) + ("…" if len(removed) > 8 else "") + "）" if removed else ""),
                  "- 持仓退出检查（明日开盘执行）："]
        for h, f, why in exits:
            if f is None:
                lines.append(f"  - {h['code']} {h['name']}：{why}")
            else:
                lines.append(f"  - {h['code']} {h['name']} 收{f['close']} MA5 {f['ma5']:.3f} 成本{h['cost']} → "
                             + (f"**EXIT（{why}）**" if why else "持有"))
        mb = p.get("max_buy", 1.03)
        lines.append(f"- 明日 14:30 买入顺延链（最高买价 = 昨收×{mb}，前日涨停股×{p.get('max_buy_prev_lu', mb)}）：")
        if not chain:
            lines.append("  - 无过硬门标的：必开暂停，上报董事长")
        for k, (sc, x, sig, ver, auto, layer) in enumerate(chain, 1):
            mult = p.get("max_buy_prev_lu", mb) if x["lu"] else mb
            lines.append(f"  {k}. {x['code']} {names.get(x['code'], '')} 综合{sc:.0f} 【{'新批' if layer == 0 else '老池'}】"
                         f"【{'自主' if auto else '强制'}】 收{x['close']} 涨{x['pct']}% 量比{x['vr']:.2f} 最高买价{x['close'] * mult:.2f}")
        lines.append("")
    path = os.path.join(ROOT, "reports", f"plan_{date}.md")
    with open(path, "w") as f:
        f.write(f"# {date} 收盘检查与次日计划（程序生成）\n\n" + "\n".join(lines))
    return path


if __name__ == "__main__":
    print(run(fetch="--fetch" in sys.argv))
