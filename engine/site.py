"""生成看板：site/index.html（单文件，数据内嵌，无外部依赖）。
用法：python3 site.py
"""
import csv
import glob
import json
import os
from statistics import mean

import data
import lab
import md

ROOT = lab.ROOT
OUT = os.path.join(ROOT, "site")
BASE = "2026-10-09"


def read(path, default=""):
    return open(path, encoding="utf-8").read() if os.path.exists(path) else default


def csv_rows(path):
    return list(csv.DictReader(open(path, encoding="utf-8"))) if os.path.exists(path) else []


def closes(code):
    return {b["date"]: b["close"] for b in data.load_bars(code)}


def stats(trades):
    if not trades:
        return dict(n=0)
    p = [float(t["pnl"]) for t in trades]
    w = [x for x in p if x > 0]; l = [x for x in p if x <= 0]
    by = {}
    for t in trades:
        k = t["exit_reason"].split("(")[0]
        d = by.setdefault(k, dict(n=0, pnl=0.0, win=0)); d["n"] += 1; d["pnl"] += float(t["pnl"]); d["win"] += float(t["pnl"]) > 0
    return dict(n=len(p), win=len(w) / len(p), total=sum(p), avg_win=mean(w) if w else 0, avg_loss=mean(l) if l else 0,
                payoff=(mean(w) / -mean(l)) if w and l and mean(l) < 0 else None,
                best=max(p), worst=min(p), by_reason=by)


def studio_data(s, idx, all_trades, quotes):
    g = lab.load_genome(s)
    L = json.load(open(os.path.join(ROOT, "studios", s, "ledger.json")))
    nav = [dict(date=r["date"], nav=float(r["nav"]),
                hs300=(idx[r["date"]] / idx[BASE] - 1) if r["date"] in idx else None)
           for r in csv_rows(os.path.join(ROOT, "studios", s, "nav_history.csv"))]
    seen, navu = set(), []
    for x in nav:  # 去重：同一日期取最后一条
        if x["date"] in seen:
            navu = [y for y in navu if y["date"] != x["date"]]
        seen.add(x["date"]); navu.append(x)
    peak, mdd = 1e5, 0.0
    for x in navu:
        peak = max(peak, x["nav"]); x["dd"] = 1 - x["nav"] / peak; mdd = max(mdd, x["dd"])
    ret = L["nav"] / 1e5 - 1
    hs = (idx[L["as_of"]] / idx[BASE] - 1) if L["as_of"] in idx else None
    positions = []
    for h in L["positions"]:
        c = closes(h["code"]); last = c[max(c)] if c else h["cost"]
        positions.append(dict(h, last=last, pnl=round((last - h["cost"]) * h["qty"], 2),
                              pnl_pct=last / h["cost"] - 1, mv=round(last * h["qty"], 2),
                              weight=last * h["qty"] / L["nav"]))
    tr = [t for t in all_trades if t["studio"] == s]
    # 股票池 + 今日评分
    score_files = sorted(glob.glob(os.path.join(ROOT, "reports", "scores", f"*_{s}.csv")))
    scores = {r["code"]: r for r in csv_rows(score_files[-1])} if score_files else {}
    pool = []
    for r in lab.load_pool(s):
        c = closes(r["code"]); ed = f"{r['entry_date'][:4]}-{r['entry_date'][4:6]}-{r['entry_date'][6:]}"
        ks = [k for k in sorted(c) if k >= ed]
        ep = c[ks[0]] if ks else None; last = c[max(c)] if c else None
        sc = scores.get(r["code"], {})
        pool.append(dict(code=r["code"], name=r["name"], batch=r["batch"], entry_date=ed, entry_price=ep, last=last,
                         ret=(last / ep - 1) if ep and last else None,
                         mcap=round((quotes.get(r["code"], {}).get("mcap") or 0) / 1e8, 1),
                         rank=int(sc["rank"]) if sc else None, composite=float(sc["composite"]) if sc else None,
                         signal=float(sc["signal"]) if sc else None, verify=float(sc["verify"]) if sc else None,
                         auto=sc.get("autonomous") == "1", pct=float(sc["pct"]) if sc else None,
                         vr=float(sc["vr"]) if sc else None))
    shadow = json.load(open(os.path.join(ROOT, "reports", f"shadow_{s}.json"))) if os.path.exists(
        os.path.join(ROOT, "reports", f"shadow_{s}.json")) else {}
    S = None if hs is None else 0.60 * ret * 100 + 0.25 * (ret - hs) * 100 - 0.15 * mdd * 100
    return dict(id=s, name=g["name"], version=g["version"], updated=g.get("updated"), params=g["params"],
                previous=g.get("previous"), memory=g.get("negative_memory_bank", []),
                testing=g.get("hypothesis_in_testing", []), as_of=L["as_of"], cash=L["cash"], nav=L["nav"],
                ret=ret, hs300=hs, excess=None if hs is None else ret - hs, mdd=mdd, score=S,
                review=L.get("review"), positions=positions, pending_sell=L.get("pending_sell", []), pending_buy=L.get("pending_buy", []),
                nav_history=navu, trades=tr, stats=stats(tr), pool=pool, scores_date=os.path.basename(score_files[-1])[:10] if score_files else None,
                shadow=shadow,
                studio_md=md.render(read(os.path.join(ROOT, "studios", s, "STUDIO.md"))),
                evo_md=md.render(read(os.path.join(ROOT, "studios", s, "evolution_log.md"))))


def build():
    idx = {b["date"]: b["close"] for b in data.load_bars(data.HS300, index=True)}
    quotes = data.load_quotes()
    all_trades = csv_rows(os.path.join(ROOT, "data", "real_trades.csv"))
    studios = [studio_data(s, idx, all_trades, quotes) for s in "ABCDE"]
    plans = sorted(glob.glob(os.path.join(ROOT, "reports", "plan_*.md")))[-15:]
    evos = sorted(glob.glob(os.path.join(ROOT, "reports", "evolution_*.md")))[-10:]
    D = dict(
        as_of=max(s["as_of"] for s in studios), base=BASE,
        hs300_now=idx[max(idx)], hs300_ret=idx[max(idx)] / idx[BASE] - 1,
        hs300_series=[dict(date=d, r=idx[d] / idx[BASE] - 1) for d in sorted(idx) if d >= BASE],
        studios=studios,
        removed=csv_rows(os.path.join(ROOT, "pools", "removed.csv")),
        plans=[dict(name=os.path.basename(p)[5:15], html=md.render(read(p))) for p in reversed(plans)],
        evolutions=[dict(name=os.path.basename(p)[10:20], html=md.render(read(p))) for p in reversed(evos)],
        docs={k: md.render(read(os.path.join(ROOT, f))) for k, f in
              [("共用规则", "RULES.md"), ("进化协议", "EVOLUTION.md"), ("审计报告", "AUDIT-20261109.md"), ("说明", "README.md")]},
        notes=json.load(open(os.path.join(ROOT, "data", "notes.json"))) if os.path.exists(os.path.join(ROOT, "data", "notes.json")) else [],
        generated=__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    os.makedirs(OUT, exist_ok=True)
    tpl = read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html"))
    payload = json.dumps(D, ensure_ascii=False, default=str).replace("</", "<\\/")
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(tpl.replace("/*__DATA__*/null", payload))
    return os.path.join(OUT, "index.html")


if __name__ == "__main__":
    print(build())
