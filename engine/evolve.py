"""每周五收盘后运行：影子测试 → 自动晋级/回滚 → 错题本 → 写进化日志。

用法：python3 evolve.py [--dry]
规则（EVOLUTION.md）：
- 参数级挑战者：训练段、验证段都跑赢生产，回撤不扩大超过1个百分点，且样本≥20笔 → 自动晋级，一次只晋级一个（取两段收益合计最高者）。
- 授权级变量（must_open、池子、mcap_min）：只出提案，须董事长批准，不自动晋级。
- 回滚：晋级后若下次评估中旧参数（previous）在验证段反超 → 自动回滚。
"""
import csv
import datetime
import json
import os
import sys

import lab
import shadow

MANDATE = {"must_open", "mcap_min", "new_batch_first"}


def lessons(studio):
    """从真实成交里归纳错题：按退出原因聚类，同类亏损≥3笔才入错题本（上限10条）。"""
    path = os.path.join(lab.ROOT, "data", "real_trades.csv")
    rows = [r for r in csv.DictReader(open(path)) if r["studio"] == studio]
    groups = {}
    for r in rows:
        key = r["exit_reason"].split("(")[0]
        groups.setdefault(key, []).append(float(r["pnl"]))
    out = []
    for k, v in sorted(groups.items(), key=lambda kv: sum(kv[1])):
        losses = [x for x in v if x < 0]
        if len(losses) >= 3:
            out.append(f"退出原因[{k}]：{len(v)}笔，亏损{len(losses)}笔，合计{sum(v):+.0f}元")
    return out[:10]


def review_stats(hold=5):
    """个股复核是否加分：只统计“复核后实际买的 ≠ 公式本来会买的”那些日子，比较两只此后 hold 日的收益。"""
    import data
    path = os.path.join(lab.ROOT, "data", "review_log.csv")
    if not os.path.exists(path):
        return ["- 暂无复核记录"]
    rows = [r for r in csv.DictReader(open(path)) if r["reviewed"] == "1"]
    changed, out = [r for r in rows if r["mech_code"] != r["pick_code"]], []

    def fwd(code, d, px):
        if not code or not px:
            return 0.0  # 当天不买 = 收益 0
        bs = [b for b in data.load_bars(code) if b["date"] >= d]
        if len(bs) < 2:
            return None
        return bs[min(hold, len(bs) - 1)]["close"] / float(px) - 1

    diffs = []
    for r in changed:
        a, b = fwd(r["pick_code"], r["date"], r["pick_px"]), fwd(r["mech_code"], r["date"], r["mech_px"])
        if a is not None and b is not None:
            diffs.append(a - b)
            out.append(f"  - {r['date']} {r['studio']}：复核买 {r['pick_name'] or '不买'} {a:+.2%}，公式会买 {r['mech_name'] or '无'} {b:+.2%}")
    head = [f"- 已复核 {len(rows)} 次；改变了买入结果 {len(changed)} 次；可评估 {len(diffs)} 次"]
    if diffs:
        win = sum(d > 0 for d in diffs)
        head.append(f"- 复核相对公式的平均{hold}日收益差 {sum(diffs) / len(diffs):+.2%}，复核更好 {win}/{len(diffs)} 次")
        head.append("- 判定：" + ("样本不足20次，继续观察" if len(diffs) < 20 else
                                 ("复核有效，保留" if sum(diffs) > 0 else "复核未跑赢公式，建议取消复核")))
    return head + out[-15:]


def run(dry=False):
    today = datetime.date.today().isoformat()
    report = [f"# 进化周报 {today}（程序生成）", "", "## 个股复核效果（方案A）", ""] + review_stats() + [""]
    for s in "ABCDE":
        gpath = os.path.join(lab.ROOT, "studios", s, "genome.json")
        g = json.load(open(gpath))
        rows = shadow.evaluate(s)
        base = rows["生产"]
        report += [f"## {s} {g['name']}（{g['version']}）", "",
                   "| 方案 | 训练段收益 | 验证段收益 | 实盘段收益 | 验证段回撤 | 笔数(训/验) | 晋级资格 |", "|---|---|---|---|---|---|---|"]
        for name, r in rows.items():
            report.append(f"| {name} | {r['train']['ret']:+.2%} | {r['valid']['ret']:+.2%} | {r['real']['ret']:+.2%} | "
                          f"{r['valid']['mdd']:.2%} | {r['train']['n']}/{r['valid']['n']} | {'是' if r.get('晋级资格') else ''} |")
        ok = [(n, r) for n, r in rows.items() if r.get("晋级资格")]
        param_ok = [(n, r) for n, r in ok if not (set(r["delta"]) & MANDATE)]
        mandate_ok = [n for n, r in ok if set(r["delta"]) & MANDATE]
        action = "维持生产参数"
        # 回滚检查
        if g.get("previous"):
            prev_p = g["previous"]["params"]
            _, m_prev = lab.run(s, prev_p, *shadow.VALID, "extended")
            if m_prev["ret"] > base["valid"]["ret"] + 0.005:
                action = f"回滚到 {g['previous']['version']}（旧参数验证段 {m_prev['ret']:+.2%} 反超）"
                if not dry:
                    g = dict(g, params=prev_p, version=g["previous"]["version"] + "-rb", previous=None)
        if action == "维持生产参数" and param_ok:
            name, r = max(param_ok, key=lambda x: x[1]["train"]["ret"] + x[1]["valid"]["ret"])
            major, minor = g["version"].lstrip("v").split("-")[0].split(".")[:2]
            newv = f"v{major}.{int(minor) + 1}"
            action = f"晋级 {name} → {newv}"
            if not dry:
                g["previous"] = dict(version=g["version"], params=dict(g["params"]))
                g["params"] = dict(g["params"], **r["delta"])
                g["version"] = newv
        g["negative_memory_bank"] = lessons(s)
        g["hypothesis_in_testing"] = [n for n, r in rows.items() if n != "生产" and not r.get("晋级资格")][:5]
        g["updated"] = today
        if mandate_ok:
            report.append(f"\n- 授权级提案（须董事长批准）：{'、'.join(mandate_ok)}")
        report.append(f"- 本周动作：**{action}**\n")
        if not dry:
            json.dump(g, open(gpath, "w"), ensure_ascii=False, indent=1)
            with open(os.path.join(lab.ROOT, "studios", s, "evolution_log.md"), "a") as f:
                f.write(f"\n## {today}\n- {action}\n- 生产：训练{base['train']['ret']:+.2%} / 验证{base['valid']['ret']:+.2%}"
                        f" / 实盘段{base['real']['ret']:+.2%}\n" + (f"- 待批授权级提案：{'、'.join(mandate_ok)}\n" if mandate_ok else ""))
    path = os.path.join(lab.ROOT, "reports", f"evolution_{today}.md")
    with open(path, "w") as f:
        f.write("\n".join(report))
    return path


if __name__ == "__main__":
    print(run(dry="--dry" in sys.argv))
