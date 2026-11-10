"""A股短线策略日线回测

策略A：底部横盘 → 长上影试盘 → 1~3日内阳线反包（对应通达信 V3 选股公式）
策略B：龙头首次分歧 → 次日竞价高开 + 突破分歧日最高（或回封排板）确认买入

数据：先运行 fetch_data.py 生成 data/daily.pkl（baostock 前复权日线）
用法：python backtest.py
输出：控制台打印分组统计；output/ 下生成交易明细 CSV 和统计 CSV

所有参数集中在 CFG 中修改。
"""
import os
import pickle
import warnings
from multiprocessing import Pool

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
np.seterr(all="ignore")

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
OUT_DIR = os.path.join(BASE, "output")

CFG = {
    # ---- 回测区间（信号日）----
    "BT_START": "2024-01-01",
    "BT_END": "2026-12-31",
    "COST": 0.002,          # 单笔往返成本：佣金+印花税+滑点，约0.2%
    # ---- 策略A：底部横盘反包 ----
    "M": 60, "N": 180, "BOX_MAX": 28,
    "WIN": 3,               # 上影后几日内反包
    "FB_MODE": 1,           # 1=收盘站上上影日最高；2=突破上影高点且收盘吞没上影70%
    "A_GAP_MAX": 0.03,      # 次日高开超过3%不追
    "A_HOLD": 5,            # 持有交易日数（含买入日），到期收盘卖出
    "A_STOP": "fanbao_low", # 止损位：fanbao_low=反包日最低；shadow_low=上影日最低
    # ---- 策略B：龙头首次分歧 ----
    "B_HOLD": 3,
    "B_T2_OPEN_STOP": -0.03,  # 买入次日开盘低于买入价3%直接卖
    "B_GAP_MAIN": (0.02, 0.06),
    "B_GAP_20CM": (0.03, 0.10),
    # ---- 市值、流动性（作为分组字段统计，不在信号层过滤）----
    "MIN_AMT5": 3e7,
}
LTSZ_BINS = [0, 30, 50, 80, 150, 300, 1e9]
LTSZ_LABELS = ["<30亿", "30-50亿", "50-80亿", "80-150亿", "150-300亿", ">300亿"]


# ======================== 通达信函数的 numpy 实现 ========================
def REF(x, k):
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:len(x) - k]
    return out


def REFV(x, k):
    """周期为序列的 REF，k 可为 NaN（返回 NaN）"""
    n = len(x)
    k = np.asarray(k, dtype=float)
    idx = np.arange(n) - np.nan_to_num(k, nan=-10**9).astype(np.int64)
    ok = (~np.isnan(k)) & (k >= 0) & (idx >= 0) & (idx < n)
    out = np.full(n, np.nan)
    out[ok] = np.asarray(x, dtype=float)[idx[ok]]
    return out


def _roll(x, w, fn):
    s = pd.Series(np.asarray(x, dtype=float))
    return getattr(s.rolling(w, min_periods=w), fn)().to_numpy()


def MA(x, w): return _roll(x, w, "mean")
def HHV(x, w): return _roll(x, w, "max")
def LLV(x, w): return _roll(x, w, "min")
def SUM(x, w): return _roll(x, w, "sum")
def COUNT(c, w): return _roll(np.asarray(c, dtype=float), w, "sum")


def BARSLAST(cond):
    n = len(cond)
    i = np.arange(n)
    last = np.where(cond, i, -1)
    last = np.maximum.accumulate(last)
    return np.where(last >= 0, i - last, np.nan).astype(float)


def B(x):
    """NaN 视为 False 的布尔化"""
    return np.nan_to_num(np.asarray(x, dtype=float), nan=0) > 0


# ======================== 数据加载 ========================
def load_data():
    path = os.path.join(DATA_DIR, "daily.pkl")
    if os.path.exists(path):
        with open(path, "rb") as f:
            obj = pickle.load(f)
        return obj["daily"], obj["names"], obj["index"]
    # 未合并时直接读分片（无指数数据）
    part_dir = os.path.join(DATA_DIR, "parts")
    data = pd.concat([pd.read_pickle(os.path.join(part_dir, f))
                      for f in sorted(os.listdir(part_dir))], ignore_index=True)
    num = ["open", "high", "low", "close", "volume", "amount", "turn", "pctChg"]
    data[num] = data[num].apply(pd.to_numeric, errors="coerce")
    for col in ["isST", "tradestatus"]:
        data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0).astype(int)
    data["date"] = pd.to_datetime(data["date"])
    names_path = os.path.join(DATA_DIR, "names.pkl")
    names = pd.read_pickle(names_path) if os.path.exists(names_path) else None
    return data, names, {}


def index_flags(index):
    """大盘在20日线上方的标记：{指数代码: Series(date->bool)}"""
    out = {}
    for code, df in index.items():
        s = df.set_index("date")["close"].sort_index()
        out[code] = s >= s.rolling(20).mean()
    return out


# ======================== 单只股票计算 ========================
def calc_stock(args):
    code, df, dp_flag = args
    cfg = CFG
    df = df[(df["tradestatus"] == 1) & (df["volume"] > 0)].sort_values("date").reset_index(drop=True)
    n = len(df)
    if n < cfg["N"] + 30:
        return [], []

    O, H, L, C = (df[c].to_numpy(float) for c in ["open", "high", "low", "close"])
    V, AMT, TURN, PCT = (df[c].to_numpy(float) for c in ["volume", "amount", "turn", "pctChg"])
    ST = df["isST"].to_numpy() == 1
    dates = df["date"].to_numpy()
    idx_all = np.arange(n)

    is20 = code.startswith("sz.30") or code.startswith("sh.68")
    ZF = 0.2 if is20 else 0.1
    K = 1.6 if is20 else 1.0
    KS = 1.5 if is20 else 1.0
    LIM = ZF * 100

    # 流通市值（亿）= 成交额 / 换手率，取5日中位数降低噪声
    ltsz_raw = np.where(TURN > 0, AMT / (TURN / 100) / 1e8, np.nan)
    LTSZ = pd.Series(ltsz_raw).rolling(5, min_periods=1).median().to_numpy()
    AMT20 = MA(AMT, 20)
    DP = dp_flag.reindex(pd.to_datetime(dates)).to_numpy() if dp_flag is not None else np.full(n, np.nan)

    RC1 = REF(C, 1)
    JC = (~ST) & (idx_all + 1 >= cfg["N"] + 10) & B(MA(AMT, 5) > cfg["MIN_AMT5"]) & (V > 0)

    # 涨跌停近似判定（用交易所涨跌幅 pctChg）
    ZT = (PCT >= LIM - 0.25) & (C >= H * 0.998)
    YZ_UP = ZT & (H == L)                     # 一字涨停
    YZ_DN = (PCT <= -LIM + 0.25) & (H == L)   # 一字跌停

    trades_a = calc_strategy_a(cfg, code, n, O, H, L, C, V, RC1, JC, K, KS,
                               dates, LTSZ, AMT20, DP, YZ_UP, YZ_DN, is20)
    trades_b = calc_strategy_b(cfg, code, n, O, H, L, C, V, TURN, PCT, RC1, ST, ZT,
                               ZF, LIM, dates, LTSZ, AMT20, DP, YZ_UP, YZ_DN, is20)
    return trades_a, trades_b


def simulate_exit(i_entry, entry, stop, hold, O, H, L, C, YZ_DN, n, t2_open_stop=None):
    """从买入日次日开始检查止损，到期收盘卖出。返回(卖出价, 卖出索引, 原因)"""
    last = min(i_entry + hold - 1, n - 1)
    if last <= i_entry:
        return np.nan, -1, "数据不足"
    j = i_entry + 1
    while j <= last:
        reason, px = None, None
        if t2_open_stop is not None and j == i_entry + 1 and O[j] <= entry * (1 + t2_open_stop):
            reason, px = "次日低开卖出", O[j]
        elif O[j] <= stop:
            reason, px = "跳空止损", O[j]
        elif L[j] <= stop:
            reason, px = "止损", stop
        elif j == last:
            reason, px = "到期", C[j]
        if reason:
            # 一字跌停卖不掉，顺延到下一个能卖的交易日开盘
            while YZ_DN[j] and j + 1 < n:
                j += 1
                px = O[j]
                reason += "(跌停顺延)"
            return px, j, reason
        j += 1
    return C[last], last, "到期"


def path_stats(i_entry, entry, O, H, L, C, n):
    out = {}
    for k in (1, 3, 5):
        j = i_entry + k
        out[f"ret_{k}d_close"] = C[j] / entry - 1 if j < n else np.nan
    j2 = min(i_entry + 5, n - 1)
    out["max_up_5d"] = H[i_entry + 1:j2 + 1].max() / entry - 1 if j2 > i_entry else np.nan
    out["max_dn_5d"] = L[i_entry + 1:j2 + 1].min() / entry - 1 if j2 > i_entry else np.nan
    return out


# ---------------- 策略A：底部横盘·长上影·反包 ----------------
def calc_strategy_a(cfg, code, n, O, H, L, C, V, RC1, JC, K, KS,
                    dates, LTSZ, AMT20, DP, YZ_UP, YZ_DN, is20):
    M, N, WIN = cfg["M"], cfg["N"], cfg["WIN"]
    TR = np.fmax(np.fmax(H - L, np.abs(H - RC1)), np.abs(L - RC1))

    VB = np.fmax(REF(MA(V, 40), 5), 1)
    ATRP = REF(MA(TR, 20), 1) / np.fmax(RC1, 0.01) * 100
    UPS = H - np.fmax(O, C)
    SD = (B(UPS / np.fmax(RC1, 0.01) * 100 >= np.fmax(2.0, ATRP * 0.5))
          & B(UPS / np.fmax(H - L, 0.01) >= 0.40)
          & B(C >= RC1 * (1 - 0.03 * K))
          & B(V >= VB * 1.1) & B(V <= VB * 6))
    YD_A = B(V >= VB * 1.6) & (C > O) & B(C / RC1 >= 1 + 0.025 * K) & B((C - L) / np.fmax(H - L, 0.01) >= 0.55)
    YD_B = B(V >= VB * 1.5) & B(H / RC1 >= 1 + 0.03 * K) & B(C >= RC1 * 0.99)
    LJYD = YD_A | YD_B
    CH_BAD = B(C < RC1 * (1 - 0.05 * K)) & B(V >= VB * 1.5)

    D = REF(BARSLAST(SD), 1) + 1
    SD_OK = B((D >= 1) & (D <= WIN))
    D = np.where(SD_OK, D, np.nan)
    Bk = D + 1
    SH = REFV(H, D)
    SL = REFV(L, D)
    SH_TOP = REFV(np.fmax(O, C), D)
    SH_UP = SH - SH_TOP

    # 变周期 LLV(L,D) 与 REF(HHV(C,D),1)
    llv_d = np.full(n, np.nan)
    hhv_c_d = np.full(n, np.nan)
    for d in range(1, WIN + 1):
        m = D == d
        llv_d[m] = LLV(L, d)[m]
        hhv_c_d[m] = REF(HHV(C, d), 1)[m]
    NOBREAK = B(llv_d >= SL * 0.99)
    FIRST = B(hhv_c_d < SH)

    HH = REFV(HHV(H, N), Bk)
    LL = REFV(LLV(L, N), Bk)
    RG = np.fmax(HH - LL, 0.01)
    CB = REFV(C, Bk)
    POS_B = (CB - LL) / RG * 100
    POS_T = (C - LL) / RG * 100
    IS_BOT = B(POS_B <= 50) & B(POS_T <= 60) & B(HH / np.fmax(LL, 0.01) >= 1.15)

    H55 = REFV(HHV(C, M - 5), Bk + 3)
    L55 = REFV(LLV(C, M - 5), Bk + 3)
    AMP_BOX = (H55 / np.fmax(L55, 0.01) - 1) * 100
    BOX_LIM = cfg["BOX_MAX"] * (1.25 if K > 1 else 1)
    BOX_OK = (B(AMP_BOX <= BOX_LIM)
              & B((CB / np.fmax(REFV(LLV(L, M), Bk), 0.01) - 1) * 100 <= BOX_LIM)
              & B(CB <= H55 * 1.08))
    MA60 = MA(C, 60)
    FLAT60 = B(np.abs(REFV(MA60, Bk) / np.fmax(REFV(MA60, Bk + 20), 0.01) - 1) * 100 <= 10)

    V_BASE = np.fmax(REFV(MA(V, 40), Bk + 3), 1)
    upv = np.where((C > O) | ((C == O) & B(C >= RC1)), V, 0)
    dnv = np.where((C < O) | ((C == O) & B(C < RC1)), V, 0)
    YY_RATIO = REFV(SUM(upv, 10), Bk) / np.fmax(REFV(SUM(dnv, 10), Bk), 1)
    DUI_V = REFV(MA(V, 5), Bk) / V_BASE
    NYD = REFV(COUNT(LJYD, 20), Bk)
    NBAD = REFV(COUNT(CH_BAD, 10), Bk)

    body = (C - O) / np.fmax(H - L, 0.01)
    T_YANG = ((C > O) & B(C > RC1) & ~((O == H) & (H == L))
              & B(body >= 0.40) & B((H - C) / C * 100 <= 2.5 * KS) & B(V >= V_BASE * 1.1))
    FB_1 = B(C >= SH)
    FB_2 = B(H >= SH) & B(C >= SH_TOP + SH_UP * 0.70)
    T_FANBAO = FB_1 if cfg["FB_MODE"] == 1 else FB_2
    NO_CHASE = B(C / REF(C, 5) <= 1 + 0.2 * K) & B(C > MA(C, 20))

    S1 = np.where(BOX_OK, 20, np.where(B(AMP_BOX <= BOX_LIM * 1.3), 10, 0))
    S2 = np.where(FLAT60, 10, 0)
    S3 = np.where(B(DUI_V >= 1.2) | B(NYD >= 2), 15, np.where(B(DUI_V >= 1.05) | B(NYD >= 1), 8, 0))
    S4 = np.where(B(YY_RATIO >= 1.3), 10, np.where(B(YY_RATIO >= 1.1), 5, 0))
    S5 = np.where(B(NBAD == 0), 5, 0)
    S6 = np.where(B(SH >= H55 * 0.97), 10, 0)
    S7 = np.where(B(C > H55), 10, 0)
    S8 = np.where(B(V >= V_BASE * 1.6), 10, np.where(B(V >= V_BASE * 1.2), 5, 0))
    S9 = np.where(B(body >= 0.6) & B((H - C) / C * 100 <= 1.5 * KS), 10, 0)
    SCORE = S1 + S2 + S3 + S4 + S5 + S6 + S7 + S8 + S9

    HARD = JC & SD_OK & IS_BOT & NOBREAK & FIRST & T_YANG & T_FANBAO & NO_CHASE

    t0, t1 = np.datetime64(cfg["BT_START"]), np.datetime64(cfg["BT_END"])
    trades = []
    for i in np.where(HARD)[0]:
        if not (t0 <= dates[i] <= t1) or i + 1 >= n:
            continue
        e = i + 1
        rec = {"code": code, "signal_date": pd.Timestamp(dates[i]), "score": int(SCORE[i]),
               "D": int(D[i]), "ltsz": LTSZ[i], "amt20": AMT20[i], "dp_ok": DP[i],
               "board": "20cm" if is20 else "主板", "box_ok": bool(BOX_OK[i]),
               "touch_box": bool(S6[i] > 0), "break_box": bool(S7[i] > 0),
               "pos_b": POS_B[i], "gap": O[e] / C[i] - 1}
        if YZ_UP[e]:
            rec.update(status="一字买不进")
        elif O[e] > C[i] * (1 + cfg["A_GAP_MAX"]):
            rec.update(status="高开过多放弃")
        else:
            entry = O[e]
            stop = L[i] if cfg["A_STOP"] == "fanbao_low" else SL[i]
            px, j, why = simulate_exit(e, entry, stop, cfg["A_HOLD"], O, H, L, C, YZ_DN, n)
            if j < 0:
                continue
            rec.update(status="成交", entry=entry, exit=px, exit_reason=why,
                       ret=px / entry - 1 - cfg["COST"], hold_days=j - e + 1)
            rec.update(path_stats(e, entry, O, H, L, C, n))
        trades.append(rec)
    return trades


# ---------------- 策略B：龙头首次分歧转一致 ----------------
def calc_strategy_b(cfg, code, n, O, H, L, C, V, TURN, PCT, RC1, ST, ZT,
                    ZF, LIM, dates, LTSZ, AMT20, DP, YZ_UP, YZ_DN, is20):
    ZFU = (H - L) / np.fmax(RC1, 0.01)
    YZ = ZT & B(ZFU <= ZF * 0.4)
    FQ_ZT = ZT & (B(ZFU >= ZF * 0.6) | B(TURN >= REF(TURN, 1) * 1.8))
    FQ_DB = (~ZT) & B(C >= RC1 * (1 - 0.3 * ZF)) & B(ZFU >= ZF * 0.6) & B((C - L) / np.fmax(H - L, 0.01) >= 0.4)
    FQ = FQ_ZT | FQ_DB

    # 分歧日前的连板数，且这些板必须全部是一致板
    streak = np.zeros(n, dtype=int)
    all_yz = np.zeros(n, dtype=bool)
    s, ok = 0, True
    for i in range(n):
        streak[i], all_yz[i] = s, ok and s > 0   # 截至 i-1 的连续涨停
        if ZT[i]:
            ok = ok and YZ[i] if s > 0 else YZ[i]
            s += 1
        else:
            s, ok = 0, True

    HS_MAX = 45 if is20 else 30
    SIG = ((~ST) & (np.arange(n) >= 60) & (streak >= 2) & all_yz & FQ
           & B(TURN <= HS_MAX) & ~YZ_UP)

    gap_lo, gap_hi = cfg["B_GAP_20CM"] if is20 else cfg["B_GAP_MAIN"]
    t0, t1 = np.datetime64(cfg["BT_START"]), np.datetime64(cfg["BT_END"])
    trades = []
    for i in np.where(SIG)[0]:
        if not (t0 <= dates[i] <= t1) or i + 1 >= n:
            continue
        e = i + 1
        gap = O[e] / C[i] - 1
        base = {"code": code, "signal_date": pd.Timestamp(dates[i]), "streak": int(streak[i]),
                "fq_type": "换手板/烂板" if FQ_ZT[i] else "断板承接", "turn": TURN[i],
                "ltsz": LTSZ[i], "amt20": AMT20[i], "dp_ok": DP[i],
                "board": "20cm" if is20 else "主板", "gap": gap}
        if YZ_UP[e]:
            trades.append({**base, "mode": "-", "status": "一字买不进"})
            continue
        if not (gap_lo <= gap <= gap_hi):
            trades.append({**base, "mode": "-", "status": "竞价不符放弃"})
            continue
        hit_zt = PCT[e] >= LIM - 0.25 or H[e] >= C[i] * (1 + ZF) * 0.997
        modes = {
            # 突破分歧日最高价买入
            "突破买": max(O[e], H[i]) if H[e] >= H[i] else None,
            # 盘中触及涨停时排板买入（假设能成交，结果偏乐观）
            "排板买": H[e] if hit_zt else None,
        }
        for mode, entry in modes.items():
            rec = {**base, "mode": mode}
            if entry is None:
                rec["status"] = "未触发"
            else:
                px, j, why = simulate_exit(e, entry, L[i], cfg["B_HOLD"], O, H, L, C, YZ_DN, n,
                                           t2_open_stop=cfg["B_T2_OPEN_STOP"])
                if j < 0:
                    continue
                rec.update(status="成交", entry=entry, exit=px, exit_reason=why,
                           ret=px / entry - 1 - cfg["COST"], hold_days=j - e + 1,
                           sealed_e=bool(C[e] >= H[e] * 0.998 and hit_zt))
                rec.update(path_stats(e, entry, O, H, L, C, n))
            trades.append(rec)
    return trades


# ======================== 统计 ========================
def stats(df):
    r = df["ret"].dropna()
    if len(r) == 0:
        return pd.Series({"笔数": 0})
    win, loss = r[r > 0], r[r <= 0]
    pf = win.sum() / -loss.sum() if loss.sum() < 0 else np.inf
    return pd.Series({
        "笔数": len(r),
        "胜率%": round(len(win) / len(r) * 100, 1),
        "平均收益%": round(r.mean() * 100, 2),
        "中位收益%": round(r.median() * 100, 2),
        "平均盈利%": round(win.mean() * 100, 2) if len(win) else 0,
        "平均亏损%": round(loss.mean() * 100, 2) if len(loss) else 0,
        "盈亏比": round(-win.mean() / loss.mean(), 2) if len(win) and len(loss) and loss.mean() < 0 else np.nan,
        "利润因子": round(pf, 2),
        "最大单亏%": round(r.min() * 100, 2),
        "5日内最高涨≥5%占比": round((df.loc[r.index, "max_up_5d"] >= 0.05).mean() * 100, 1),
    })


def group_report(df, by, title, out_rows):
    g = df.groupby(by, observed=True).apply(stats)
    print(f"\n--- {title} ---")
    print(g.to_string())
    g = g.reset_index()
    g.insert(0, "分组", title)
    out_rows.append(g)


def report_a(ta, out_rows):
    print("\n" + "=" * 20 + " 策略A：底部横盘·长上影·反包 " + "=" * 20)
    print("信号状态：", ta["status"].value_counts().to_dict())
    t = ta[ta["status"] == "成交"].copy()
    if t.empty:
        print("没有成交记录")
        return
    t["市值段"] = pd.cut(t["ltsz"], LTSZ_BINS, labels=LTSZ_LABELS)
    t["年份"] = t["signal_date"].dt.year
    t["反包窗口"] = np.where(t["D"] == 1, "次日反包(D=1)", "2~3日反包")
    t["大盘20日线上"] = t["dp_ok"].map({1.0: "是", 0.0: "否", True: "是", False: "否"}).fillna("无数据")
    t["流动性"] = np.where(t["amt20"] >= 1.5e8, "20日均额≥1.5亿", "<1.5亿")

    rows = []
    for th in (0, 45, 55, 65, 75):
        s = stats(t[t["score"] >= th])
        s.name = f"评分≥{th}"
        rows.append(s)
    g = pd.DataFrame(rows)
    print("\n--- 评分门槛 ---")
    print(g.to_string())
    g = g.reset_index().rename(columns={"index": "评分门槛"})
    g.insert(0, "分组", "A-评分门槛")
    out_rows.append(g)

    base = t[t["score"] >= 55]
    print(f"\n以下分组均基于 评分≥55（{len(base)} 笔）")
    group_report(base, "市值段", "A-流通市值", out_rows)
    group_report(base, "流动性", "A-流动性", out_rows)
    group_report(base, "反包窗口", "A-反包窗口", out_rows)
    group_report(base, "board", "A-板块", out_rows)
    group_report(base, "大盘20日线上", "A-大盘过滤", out_rows)
    group_report(base, "touch_box", "A-上影触及箱顶", out_rows)
    group_report(base, "break_box", "A-反包突破箱顶", out_rows)
    group_report(base, "年份", "A-年份", out_rows)
    group_report(base, "exit_reason", "A-卖出原因", out_rows)
    # 推荐组合：评分≥55 + 流通市值30~300亿 + 均额≥1.5亿
    combo = base[(base["ltsz"] >= 30) & (base["ltsz"] <= 300) & (base["amt20"] >= 1.5e8)]
    s = stats(combo)
    print("\n--- A-推荐组合（评分≥55 + 流通市值30~300亿 + 20日均额≥1.5亿）---")
    print(s.to_string())
    for k in (1, 3, 5):
        col = f"ret_{k}d_close"
        print(f"不设止损，买入后第{k}日收盘：胜率 {(combo[col] > 0).mean() * 100:.1f}%，"
              f"均值 {combo[col].mean() * 100:.2f}%")


def report_b(tb, out_rows):
    print("\n" + "=" * 20 + " 策略B：龙头首次分歧转一致 " + "=" * 20)
    sig = tb.drop_duplicates(["code", "signal_date"])
    print("分歧日信号数：", len(sig), " 次日状态：", sig["status"].value_counts().to_dict())
    t = tb[tb["status"] == "成交"].copy()
    if t.empty:
        print("没有成交记录")
        return
    t["市值段"] = pd.cut(t["ltsz"], LTSZ_BINS, labels=LTSZ_LABELS)
    t["连板数"] = np.where(t["streak"] >= 4, "4板+", t["streak"].astype(str) + "板")
    t["大盘20日线上"] = t["dp_ok"].map({1.0: "是", 0.0: "否", True: "是", False: "否"}).fillna("无数据")
    t["年份"] = t["signal_date"].dt.year
    group_report(t, "mode", "B-买点", out_rows)
    for mode in ("突破买", "排板买"):
        m = t[t["mode"] == mode]
        if m.empty:
            continue
        group_report(m, "fq_type", f"B-{mode}-分歧类型", out_rows)
        group_report(m, "连板数", f"B-{mode}-分歧前连板数", out_rows)
        group_report(m, "市值段", f"B-{mode}-流通市值", out_rows)
        group_report(m, "大盘20日线上", f"B-{mode}-大盘过滤", out_rows)
        group_report(m, "年份", f"B-{mode}-年份", out_rows)


# ======================== 主程序 ========================
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    data, names, index = load_data()
    if names is not None:  # 剔除名称含“退”的股票
        bad = set(names.loc[names["code_name"].str.contains("退", na=False), "code"])
        data = data[~data["code"].isin(bad)]
    flags = index_flags(index)
    tasks = []
    for code, df in data.groupby("code"):
        idx_code = "sh.000001" if code.startswith("sh") else "sz.399001"
        tasks.append((code, df, flags.get(idx_code)))
    print(f"股票数 {len(tasks)}，回测区间 {CFG['BT_START']} ~ {CFG['BT_END']}")

    ta, tb = [], []
    with Pool(max(1, (os.cpu_count() or 2) - 1)) as pool:
        for a, b in pool.imap_unordered(calc_stock, tasks, chunksize=20):
            ta.extend(a)
            tb.extend(b)
    ta, tb = pd.DataFrame(ta), pd.DataFrame(tb)
    ta.to_csv(os.path.join(OUT_DIR, "trades_A.csv"), index=False, encoding="utf-8-sig")
    tb.to_csv(os.path.join(OUT_DIR, "trades_B.csv"), index=False, encoding="utf-8-sig")

    out_rows = []
    if len(ta):
        report_a(ta, out_rows)
    else:
        print("策略A 无信号")
    if len(tb):
        report_b(tb, out_rows)
    else:
        print("策略B 无信号")
    if out_rows:
        pd.concat(out_rows, ignore_index=True).to_csv(
            os.path.join(OUT_DIR, "summary.csv"), index=False, encoding="utf-8-sig")
    print(f"\n明细与统计已保存到 {OUT_DIR}")


if __name__ == "__main__":
    main()
