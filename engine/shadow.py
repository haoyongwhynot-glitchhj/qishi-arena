"""影子测试：生产参数 vs 单变量挑战者，在扩展历史上分训练段/验证段比较。

用法：python3 shadow.py [studio...]  → 写 reports/shadow_<studio>.json
晋级规则（EVOLUTION.md）：挑战者须在训练段和验证段都优于生产（收益更高且最大回撤不扩大超过1个百分点），
并且两段合计交易≥20笔，才允许提交晋级。
"""
import json
import os
import sys

import lab

def _windows():
    """滚动窗口：验证段 = 最近 90 个交易日，训练段 = 之前的全部历史，实盘段 = 10-12 至今。
    （v3.0 初版写死了 11-09 截止日，之后的新数据不会进入评估，已修正。）"""
    import data
    cal = sorted(b["date"] for b in data.load_bars(data.HS300, index=True))
    v0 = cal[-90]
    return (cal[260], cal[cal.index(v0) - 1]), (v0, cal[-1]), ("2026-10-12", cal[-1])


TRAIN, VALID, REAL = _windows()


def challengers(studio, prod):
    """每个挑战者只改一个变量。"""
    ch = {}
    for ex in ["ma5", "ma5_confirm", "ma10"]:
        if prod.get("exit") != ex:
            ch[f"exit={ex}"] = {"exit": ex}
    ch["hold_max=5"] = {"hold_max": 5}
    ch["take_profit=10%"] = {"take_profit": 0.10}
    ch["不买信号日涨停股"] = {"skip_signal_lu": True}
    ch["只做自主开仓(取消必开)"] = {"must_open": False}
    if studio == "C":
        ch["exit=low60"] = {"exit": "low60"}
        ch["低位放宽(0.5/1.25/0.25)"] = {"lowpos_max": 0.5, "near_low": 1.25, "amp_max": 0.25}
    if studio == "D":
        ch["分歧低吸模式"] = {"mode": "divergence"}
    if studio == "A":
        ch["取消量比硬门"] = {"v5_20_min": 0}
    if studio == "B":
        ch["红柱收紧(5日涨幅≤10%,量比≥1.5)"] = {"ret5_max": 0.10, "vr_min": 1.5}
    if studio == "E":
        ch["过热降序取消"] = {"hot_mult": 1.0}
    return ch


def summarize(m):
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}


def evaluate(studio):
    prod = lab.load_genome(studio)["params"]
    rows = {}
    for name, delta in [("生产", {})] + list(challengers(studio, prod).items()):
        p = dict(prod, **delta)
        out = {}
        for seg, (a, b), mode in [("train", TRAIN, "extended"), ("valid", VALID, "extended"), ("real", REAL, "real")]:
            res, m = lab.run(studio, p, a, b, mode)
            out[seg] = summarize(m)
        rows[name] = dict(delta=delta, **out)
        print(studio, name, out["train"].get("ret"), out["valid"].get("ret"), out["real"].get("ret"), flush=True)
    base = rows["生产"]
    for name, r in rows.items():
        if name == "生产":
            continue
        ok = all(r[s]["ret"] > base[s]["ret"] and r[s]["mdd"] <= base[s]["mdd"] + 0.01 for s in ("train", "valid"))
        r["晋级资格"] = bool(ok and r["train"]["n"] + r["valid"]["n"] >= 20)
    os.makedirs(os.path.join(lab.ROOT, "reports"), exist_ok=True)
    with open(os.path.join(lab.ROOT, "reports", f"shadow_{studio}.json"), "w") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    return rows


if __name__ == "__main__":
    for s in sys.argv[1:] or list("ABCDE"):
        evaluate(s)
