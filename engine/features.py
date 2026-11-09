"""逐日特征（只用截至当日的数据，无未来函数）。"""
from statistics import mean


def limit_pct(code, name=""):
    if "ST" in (name or ""):
        return 5.0
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("92", "8", "43")):
        return 30.0
    return 10.0


def _ema(xs, n):
    k, out, e = 2 / (n + 1), [], None
    for x in xs:
        e = x if e is None else e + k * (x - e)
        out.append(e)
    return out


def build(code, bars, name="", mcap_now=None, idx_close=None):
    """返回与 bars 等长的特征列表（前 60 根为 None）。
    idx_close: {date: 沪深300收盘}，用于相对强弱。"""
    n = len(bars)
    if n < 70:
        return [None] * n
    lim = limit_pct(code, name)
    C = [b["close"] for b in bars]; O = [b["open"] for b in bars]
    H = [b["high"] for b in bars]; L = [b["low"] for b in bars]
    V = [b["volume"] for b in bars]
    dif = [a - b for a, b in zip(_ema(C, 12), _ema(C, 26))]
    dea = _ema(dif, 9)
    hist = [a - b for a, b in zip(dif, dea)]
    lu = [b["pct"] >= lim - 0.15 for b in bars]
    ld = [b["pct"] <= -lim + 0.15 for b in bars]
    last_close = C[-1]
    out = [None] * n
    for i in range(65, n):
        ma = lambda k, s=0: mean(C[i - s - k + 1:i - s + 1])
        v5 = mean(V[i - 5:i]) or 1
        hp = 0
        while hp < 30 and hist[i - hp] > 0:
            hp += 1
        w60l = min(L[i - 59:i + 1]); w60h = max(H[i - 59:i + 1])
        idx_r20 = None
        if idx_close:
            a, b = idx_close.get(bars[i]["date"]), idx_close.get(bars[i - 20]["date"])
            idx_r20 = a / b - 1 if a and b else None
        r20 = C[i] / C[i - 20] - 1
        out[i] = dict(
            date=bars[i]["date"], code=code, open=O[i], close=C[i], high=H[i], low=L[i],
            pct=bars[i]["pct"], turnover=bars[i]["turnover"], amount=bars[i]["amount"] / 1e8,
            prev_close=C[i - 1], prev_pct=bars[i - 1]["pct"], prev_open=O[i - 1],
            ma5=ma(5), ma10=ma(10), ma20=ma(20), ma60=ma(60), ma60_5=ma(60, 5),
            vr=V[i] / v5, v5_20=mean(V[i - 4:i + 1]) / (mean(V[i - 19:i + 1]) or 1),
            ret5=C[i] / C[i - 5] - 1, ret20=r20,
            rs20=None if idx_r20 is None else r20 - idx_r20,
            hhv20_prev=max(C[i - 20:i]), high250=max(H[max(0, i - 249):i + 1]),
            low250=min(L[max(0, i - 249):i + 1]), low120=min(L[max(0, i - 119):i + 1]),
            lowpos60=(C[i] - w60l) / (w60h - w60l) if w60h > w60l else 0.5,
            llv60=w60l, new_low5=min(L[i - 4:i + 1]) < min(L[i - 59:i - 4]),
            amp10=(max(H[i - 9:i + 1]) - min(L[i - 9:i + 1])) / min(L[i - 9:i + 1]),
            vol10=mean(V[i - 9:i + 1]), vol3=mean(V[i - 2:i + 1]), vol_prev20=mean(V[i - 29:i - 9]),
            hhv10=max(H[i - 10:i]),
            hist=hist[i], hist_prev=hist[i - 1], hist_pos_days=hp,
            lu=lu[i], ld=ld[i], prev_lu=lu[i - 1], lu10=sum(lu[i - 9:i + 1]),
            boards=_run(lu, i), prev_boards=_run(lu, i - 1),
            yizi_up=lu[i] and O[i] == H[i] == L[i], yizi_down=ld[i] and O[i] == H[i] == L[i],
            sealed=lu[i] and C[i] == H[i],
            lim=lim, st="ST" in (name or ""),
            mcap=None if not mcap_now else mcap_now * C[i] / last_close / 1e8,  # 亿元，按当前股本近似
        )
    return out


def _run(flags, i):
    k = 0
    while i >= 0 and flags[i]:
        k += 1; i -= 1
    return k
