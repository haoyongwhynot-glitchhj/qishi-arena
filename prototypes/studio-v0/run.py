"""1) 用今日数据给5个工作室出 Top3；2) 回测 + 参数优化（前60%训练，后40%验证）"""
import itertools, sys
from statistics import mean
from engine import STUDIOS, load, feats, day_ctx, is_lu
from tags import TAGS, SAMPLE, NEUTRAL

COST = 0.002  # 双边手续费+印花税+滑点
BARS = {c: load(c) for c in list(TAGS) + list(SAMPLE)}
IDX = {c: {b["date"]: i for i, b in enumerate(bs)} for c, bs in BARS.items()}
CAL = [b["date"] for b in BARS["000026"]]

FEAT = {}  # (code, date) -> feats，预计算
for c in TAGS:
    for d, i in IDX[c].items():
        FEAT[(c, d)] = feats(c, BARS[c], i)

def rank_day(date, name, params, tags):
    fs = [FEAT[(c, date)] for c in TAGS if FEAT.get((c, date))]
    if len(fs) < 8:
        return []
    ctx = day_ctx(fs, tags)
    fn = STUDIOS[name]["fn"]
    out = []
    for f in fs:
        ok, sc, parts = fn(f, tags[f["code"]], ctx, params)
        if ok:
            out.append((sc, f["code"], parts))
    return sorted(out, reverse=True)[:3]

def fwd(code, date, hold):
    """次日开盘买入，持有 hold 天后收盘卖出；次日一字涨停买不进返回 None"""
    b, i = BARS[code], IDX[code].get(date)
    if i is None or i + hold >= len(b):
        return None
    e = b[i + 1]
    if e["open"] == e["high"] and is_lu(e, code):
        return None
    return b[i + hold]["close"] / e["open"] - 1 - COST

def backtest(name, params, dates):
    hold = STUDIOS[name]["hold"]
    rets, excess, benches = [], [], []
    for d in dates:
        picks = rank_day(d, name, params, NEUTRAL_TAGS)
        if not picks:
            continue
        pool = [r for c in TAGS if (r := fwd(c, d, hold)) is not None]
        if not pool:
            continue
        bench = mean(pool)
        for _, c, _ in picks:
            r = fwd(c, d, hold)
            if r is not None:
                rets.append(r); excess.append(r - bench); benches.append(bench)
    if not rets:
        return dict(n=0, ret=0, win=0, exc=0, bench=0)
    return dict(n=len(rets), ret=mean(rets), win=sum(r > 0 for r in rets) / len(rets), exc=mean(excess), bench=mean(benches))

NEUTRAL_TAGS = {c: dict(NEUTRAL) for c in TAGS}

def fmt(r):
    return f"n={r['n']:>3}  均收益={r['ret']*100:+6.2f}%  胜率={r['win']*100:5.1f}%  超额={r['exc']*100:+6.2f}%  同期池均={r['bench']*100:+6.2f}%"

def today():
    d = CAL[-1]
    print(f"=== 今日选股 {d}（使用人工标签）===")
    picks_all = {}
    for name in STUDIOS:
        picks = rank_day(d, name, STUDIOS[name]["params"], TAGS)
        picks_all[name] = [c for _, c, _ in picks]
        print(f"\n[{name}]  持有{STUDIOS[name]['hold']}天")
        if not picks:
            print("  今日无标的通过门槛 → 空仓")
        for sc, c, parts in picks:
            detail = " ".join(f"{k}{v:.0f}" for k, v in parts.items())
            print(f"  {c} {TAGS[c]['name']:<6} {sc:5.1f}分 | {detail}")
    # 样板校验：西部材料在 A 工作室的得分
    fs = [FEAT[(c, d)] for c in TAGS if FEAT.get((c, d))]
    ctx = day_ctx(fs, TAGS)
    bs = BARS["002149"]
    print("\n[样板校验] 西部材料近30日在A工作室触发的日期：")
    for i in range(len(bs) - 30, len(bs)):
        f = feats("002149", bs, i)
        ok, sc, _ = STUDIOS["A 老龙回头"]["fn"](f, SAMPLE["002149"], ctx, STUDIOS["A 老龙回头"]["params"])
        if ok:
            kind = "首次突破" if f["brk1"] else "回踩后二次启动"
            r = fwd("002149", bs[i]["date"], 5)
            r5 = "次日一字买不进或数据不足" if r is None else f"次日开盘买入持有5天 {r*100:+.2f}%"
            print(f"  {bs[i]['date']} {kind} 得分={sc:.1f} 收盘={bs[i]['close']}  {r5}")
    # 工作室之间的重合度
    names = list(picks_all)
    print("\n[重合检查] 两两共同入选数：")
    for a, b2 in itertools.combinations(names, 2):
        common = set(picks_all[a]) & set(picks_all[b2])
        if common:
            print(f"  {a} × {b2}: {', '.join(TAGS[c]['name'] for c in common)}")

def optimize():
    dates = [d for d in CAL[250:] if d in CAL]
    cut = int(len(dates) * 0.6)
    train, test = dates[:cut], dates[cut:]
    print(f"\n=== 回测（标签中性化，只检验量价规则）训练 {train[0]}~{train[-1]}  验证 {test[0]}~{test[-1]} ===")
    for name, cfg in STUDIOS.items():
        keys = list(cfg["grid"])
        best, best_p = None, cfg["params"]
        for combo in itertools.product(*cfg["grid"].values()):
            p = dict(zip(keys, combo))
            r = backtest(name, p, train)
            if r["n"] >= 10 and (best is None or r["exc"] > best["exc"]):
                best, best_p = r, p
        print(f"\n[{name}]")
        print(f"  默认参数 {cfg['params']}")
        print(f"    训练: {fmt(backtest(name, cfg['params'], train))}")
        print(f"    验证: {fmt(backtest(name, cfg['params'], test))}")
        print(f"  训练最优 {best_p}")
        print(f"    训练: {fmt(best) if best else '样本不足'}")
        print(f"    验证: {fmt(backtest(name, best_p, test))}")

if __name__ == "__main__":
    today()
    if "--today" not in sys.argv:
        optimize()
