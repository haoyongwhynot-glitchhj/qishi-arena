"""五个工作室的选股规则。所有参数来自各自的 genome.json，进化只改 genome，不改代码。

每个工作室提供：
- gate(f, p)       硬门（市值、ST 等公共硬门在 sim 里统一做）
- signal(f, p)     信号研究员评分（0~100）
- verify(f, p)     核验研究员评分（0~100）
- autonomous(f, p) 是否“全部信号通过”（自主开仓）；不通过但过硬门的只能做强制开仓
两名研究员的分数按平均排名法合成（RULES.md 第四节）。
"""


def lin(x, lo, hi):
    if x is None or hi == lo:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def band(x, lo, a, b, hi):
    """落在 [a,b] 得 1，越过 lo/hi 得 0，中间线性。"""
    if x is None or x <= lo or x >= hi:
        return 0.0
    if x < a:
        return (x - lo) / (a - lo)
    if x > b:
        return (hi - x) / (hi - b)
    return 1.0


# ---------------- A 董事长池：涨停质量 + 老龙回头 ----------------
def a_gate(f, p):
    return f["close"] > f["ma20"] and (not p.get("v5_20_min") or f["v5_20"] >= p["v5_20_min"])


def a_signal(f, p):  # A01 量价择时
    return (20 * lin(f["close"] / f["ma20"] - 1, -0.0, 0.05) + 15 * lin(f["rs20"] or 0, -0.1, 0.0)
            + 20 * lin(f["v5_20"], 1.0, 2.0) + 15 * band(f["turnover"], 1, 3, 8, 15)
            + 15 * lin(f["high250"] / f["close"], 1.0, 1.8)          # 离52周高点越远越高
            + 15 * (0.0 if f["ret5"] > p.get("hot_ret5", 0.30) else 1.0))  # 近期暴涨视作假突破风险


def a_verify(f, p):  # A02 涨停结构（日线可得的部分）
    if f["lu"]:
        q = 12 * (not f["yizi_up"]) + 12 * f["sealed"] + 13 * band(f["turnover"], 1, 3, 10, 25) + 13
    else:
        q = 25
    return q + 15 + 10 * (f["high250"] / f["close"] > 1.4) + 10 * (f["close"] / f["low120"] < 1.6)


def a_auto(f, p):
    """西部材料型：距250日高点跌幅≥dd_min，且离120日低点不远（低位首次突破/回踩后再启动）。"""
    return (f["high250"] / f["close"] >= p.get("dd_min", 1.4)
            and f["close"] / f["low120"] <= p.get("lowpos_max", 1.6)
            and f["pct"] >= 5 and f["vr"] >= 1.5)


# ---------------- B 克劳德主力红柱 ----------------
def b_gate(f, p):
    return f["close"] > f["ma20"]


def b_auto(f, p):
    return (f["hist"] > 0 and f["hist"] > f["hist_prev"] and f["hist_pos_days"] >= 2
            and f["close"] > f["open"] and f["vr"] >= p.get("vr_min", 1.2)
            and f["pct"] < p.get("pct_max", 9.8)
            and f["ret5"] <= p.get("ret5_max", 9.99))


def b_signal(f, p):  # B01
    grow = f["hist"] / f["hist_prev"] if f["hist_prev"] > 0 else (2.0 if f["hist"] > 0 else 0)
    return (40 * lin(grow, 1.0, 1.2) + 15 * lin(f["vr"], 1.0, 2.0) + 15 * lin(f["close"] / f["open"] - 1, 0, 0.04)
            + 15 * band(f["close"] / f["ma20"], 1.0, 1.02, 1.08, 1.2) + 15 * lin(f["rs20"] or 0, -0.05, 0.1))


def b_verify(f, p):  # B02
    return (40 * band(f["turnover"], 1, 3, 8, 15) + 30 * (not f["yizi_up"])
            + 30 * (1 - lin(f["ret5"], 0.10, 0.25)))


# ---------------- C 克劳德低位主力吸 ----------------
def c_gate(f, p):
    return True  # MA20 硬门豁免（低位股常在 MA20 下方）


def c_auto(f, p):
    return (f["lowpos60"] <= p.get("lowpos_max", 0.33) and f["close"] <= f["llv60"] * p.get("near_low", 1.15)
            and f["amp10"] < p.get("amp_max", 0.15) and f["vol10"] < f["vol_prev20"]
            and f["vol3"] > f["vol10"] * 1.1 and f["close"] <= f["hhv10"]
            and not f["new_low5"])


def c_signal(f, p):  # C01
    return (40 * lin(f["lowpos60"], 0.6, 0.0) + 30 * lin(f["amp10"], 0.30, 0.08)
            + 30 * lin(f["vol3"] / (f["vol10"] or 1), 0.9, 1.5))


def c_verify(f, p):  # C02
    return (40 * lin(f["amount"], 0.5, 5) + 30 * band(f["turnover"], 0.2, 0.8, 3, 8)
            + 30 * (1 - lin(f["ret5"], 0.05, 0.15)))


# ---------------- D 主力红箭头5日（短线起爆） ----------------
def d_gate(f, p):
    if p.get("mode") == "divergence":  # 分歧低吸：只看昨日首/二板、今日分歧且守住涨停阳线开盘价
        return (f["prev_lu"] and 1 <= f["prev_boards"] <= p.get("max_boards", 2) and not f["lu"]
                and p.get("pct_lo", -5) <= f["pct"] <= 5 and f["close"] >= f["prev_open"])
    return True


def d_signal(f, p):  # S01 起爆评分（原公式，按排名使用，无需归一）
    brk = 15 if f["close"] >= f["hhv20_prev"] else (8 if f["close"] >= f["hhv20_prev"] * 0.97 else 0)
    return 10 * f["lu10"] + brk + 5 * (f["vr"] - 1)


def d_verify(f, p):  # S02
    return (25 * lin(f["close"] / f["prev_open"], 1.0, 1.10) + 20 * band(f["turnover"], 3, 6, 15, 25)
            + 15 * f["close"] / f["high"] + 15 * lin(f["close"] / f["low120"], 2.0, 1.2) + 25)


def d_auto(f, p):
    return d_gate(f, p) if p.get("mode") == "divergence" else d_signal(f, p) >= p.get("auto_min", 20)


# ---------------- E 起爆六六六红（每日必开动量） ----------------
def e_gate(f, p):
    return True


def e_signal(f, p):  # Q01 起爆评分 + 过热降序
    s = f["pct"] + f["ret5"] * 100 * 0.5 + (f["vr"] - 1) * 5
    if f["ret5"] > p.get("hot_ret5", 0.20):
        s *= p.get("hot_mult", 0.8)
    return s


def e_verify(f, p):  # Q02 结构与反证
    return (40 * (0.5 if not f["lu"] else (f["sealed"] and not f["yizi_up"])) + 30
            + 30 * (1 - lin(f["ret5"], 0.15, 0.35)))


def e_auto(f, p):
    return f["pct"] > p.get("pct_min", 2) and f["vr"] > p.get("vr_min", 1.2)


STUDIOS = {
    "A": dict(name="董事长池", gate=a_gate, signal=a_signal, verify=a_verify, auto=a_auto),
    "B": dict(name="克劳德主力红柱", gate=b_gate, signal=b_signal, verify=b_verify, auto=b_auto),
    "C": dict(name="克劳德低位主力吸", gate=c_gate, signal=c_signal, verify=c_verify, auto=c_auto),
    "D": dict(name="主力红箭头5日", gate=d_gate, signal=d_signal, verify=d_verify, auto=d_auto),
    "E": dict(name="起爆六六六红", gate=e_gate, signal=e_signal, verify=e_verify, auto=e_auto),
}


def rank_composite(cands, studio, p):
    """cands: 过硬门的特征列表。返回按综合排名排序的 [(composite_score, f, sig, ver, auto)]。"""
    st = STUDIOS[studio]
    rows = [(f, st["signal"](f, p), st["verify"](f, p), bool(st["auto"](f, p))) for f in cands]
    n = len(rows)
    if n == 0:
        return []

    def ranks(vals):
        order = sorted(range(n), key=lambda k: -vals[k])
        r = [0.0] * n
        k = 0
        while k < n:  # 并列取平均名次
            j = k
            while j + 1 < n and vals[order[j + 1]] == vals[order[k]]:
                j += 1
            for m in range(k, j + 1):
                r[order[m]] = (k + j) / 2 + 1
            k = j + 1
        return r

    rs, rv = ranks([r[1] for r in rows]), ranks([r[2] for r in rows])
    comp = [(rs[k] + rv[k]) / 2 for k in range(n)]
    out = []
    for k in range(n):
        score = 100.0 if n == 1 else 100 * (n - comp[k]) / (n - 1)
        out.append((score, rs[k], rows[k][0], rows[k][1], rows[k][2], rows[k][3]))
    # 综合分降序；并列时信号研究员排名靠前者优先；再按代码升序
    out.sort(key=lambda x: (-x[0], x[1], x[2]["code"]))
    return [(s, f, sig, ver, auto) for s, _, f, sig, ver, auto in out]
