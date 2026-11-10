"""TSP 研究回测器：直接读取 TSP 本地数据，复用 TSP 策略文件，按自定义交易规则回测。

和 TSP 自带回测的区别：
  - 止损可以设在价位（反包日最低 / 上影日最低 / 分歧日最低），不只是固定百分比
  - 支持次日高开过滤、龙头次日竞价高开 + 突破确认买入
  - 结果按年份、市场环境、流通市值、评分、板块、卖出原因分组
  - 参数网格按 TSP 的稳健分（0.6×年度中位 + 0.4×年度最差）排名

用法（Windows PowerShell）：
    python tsp_lab.py --data "D:\\TSP\\data"                 # 跑 RUNS 里全部方案
    python tsp_lab.py --data "D:\\TSP\\data" --run A-默认     # 只跑一个方案
    python tsp_lab.py --data "D:\\TSP\\data" --grid A          # 跑策略A参数网格
    python tsp_lab.py --data "D:\\TSP\\data" --check           # 只检查数据
    python tsp_lab.py --data "D:\\TSP\\data" --sample 500      # 随机500只股票快速试跑
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

HERE = Path(__file__).resolve().parent

# =============================== 配置 ===============================
CONFIG = {
    "data_dir": "auto",               # TSP 数据目录；auto=自动查找，命令行 --data 可指定
    "start": "2022-01-01",            # 信号日起
    "end": "2026-12-31",              # 信号日止
    "fees": {
        "commission": 0.0002,         # 佣金 万2（双边）
        "stamp": 0.0005,              # 印花税 0.05%（仅卖出，2023-08-28 起）
        "slippage_bps": 5,            # 滑点 bp（双边）
    },
    "portfolio": {"max_positions": 10},   # 组合模拟：等权，最多同时持有N只
    "exright": "auto",                # 疑似除权交易：auto=数据未复权时剔除；drop=总是剔除；keep=保留
    "out_dir": str(HERE / "output"),
}

STRATEGY_A = str(HERE / "strategies" / "custom_bottom_shadow_fanbao.py")
STRATEGY_B = str(HERE / "strategies" / "custom_leader_first_divergence.py")

# 交易规则说明
#   entry:  next_open      次日开盘买（gap_max 以上高开放弃，一字涨停买不进）
#           close          信号日收盘买（收盘涨停买不进）
#           leader_confirm 次日竞价高开在区间内，且盘中突破信号日最高价才买
#   stop:   pct            买入价 × (1 + stop_pct)
#           signal_low     信号日最低价 × (1 - stop_buffer)
#           shadow_low     上影日最低价（需策略输出 fb_shadow_low 列）
#           none           不设止损
#   hold:   最长持有交易日（含买入日），到期收盘卖
#   states: 只在这些市场环境（信号日）开仓，None=不过滤
#           可选 strong 强势 / lean_strong 偏强 / range 震荡 / lean_weak 偏弱 / weak 弱势
EXEC_FANBAO = {
    "entry": "next_open", "gap_max": 0.03, "gap_min": None,
    "stop": "signal_low", "stop_pct": -0.07, "stop_buffer": 0.0,
    "hold": 5, "t2_open_stop": None, "states": None,
}
EXEC_LEADER = {
    "entry": "leader_confirm", "leader_mode": "break",   # break=突破买；board=回封涨停排板买（偏乐观）
    "gap_main": (0.02, 0.06), "gap_20cm": (0.03, 0.10),
    "stop": "signal_low", "stop_pct": -0.07, "stop_buffer": 0.0,
    "hold": 3, "t2_open_stop": -0.03, "states": None,
}
# 与 TSP 自带回测口径对齐（次日开盘买、固定百分比止损），用于核对两边结果
EXEC_TSP = {
    "entry": "next_open", "gap_max": None, "gap_min": None,
    "stop": "pct", "stop_pct": -0.07, "stop_buffer": 0.0,
    "hold": 5, "t2_open_stop": None, "states": None,
}

RUNS = [
    {"name": "A-TSP口径", "strategy": STRATEGY_A, "params": {}, "exec": EXEC_TSP},
    {"name": "A-默认", "strategy": STRATEGY_A, "params": {}, "exec": EXEC_FANBAO},
    # 第二轮全市场结果：只有“强势”环境为正，150亿以上、20cm 较好，持有3天优于5天，评分高反而差
    {"name": "A-强势", "strategy": STRATEGY_A, "params": {},
     "exec": {**EXEC_FANBAO, "stop": "shadow_low", "hold": 3, "states": ["strong"]}},
    {"name": "A-强势+150亿", "strategy": STRATEGY_A, "params": {},
     "exec": {**EXEC_FANBAO, "stop": "shadow_low", "hold": 3, "states": ["strong"]},
     "basic_filter": {"float_cap_min": 1.5e10, "float_cap_max": None}},
    {"name": "A-强势+20cm", "strategy": STRATEGY_A, "params": {},
     "exec": {**EXEC_FANBAO, "stop": "shadow_low", "hold": 3, "states": ["strong"]},
     "basic_filter": {"boards": ["创业板", "科创板"]}},
    {"name": "A-强势+不限评分", "strategy": STRATEGY_A, "params": {"sc_min": 0},
     "exec": {**EXEC_FANBAO, "stop": "shadow_low", "hold": 3, "states": ["strong"]}},
    # 第三轮网格最优附近的候选规则（用于模拟盘前的最终确认，注意：参数是在同一段历史上挑出来的）
    {"name": "A-候选", "strategy": STRATEGY_A, "params": {"sc_min": 65},
     "exec": {**EXEC_FANBAO, "stop": "signal_low", "hold": 2, "states": ["strong"]}},
    {"name": "A-候选+150亿", "strategy": STRATEGY_A, "params": {"sc_min": 65},
     "exec": {**EXEC_FANBAO, "stop": "signal_low", "hold": 2, "states": ["strong"]},
     "basic_filter": {"float_cap_min": 1.5e10, "float_cap_max": None}},
    {"name": "B-突破买", "strategy": STRATEGY_B, "params": {}, "exec": EXEC_LEADER},
    # 竞价区间放宽，增加样本量
    {"name": "B-突破买-宽竞价", "strategy": STRATEGY_B, "params": {},
     "exec": {**EXEC_LEADER, "gap_main": (0.0, 0.09), "gap_20cm": (0.0, 0.15)}},
    {"name": "B-强势-宽竞价", "strategy": STRATEGY_B, "params": {},
     "exec": {**EXEC_LEADER, "gap_main": (0.0, 0.09), "gap_20cm": (0.0, 0.15), "states": ["strong"]}},
]

# 参数网格：params 改变会重算信号，exec 只改交易规则
GRIDS = {
    "A": {
        "strategy": STRATEGY_A, "base_exec": EXEC_FANBAO,
        "params": {"sc_min": [0, 55, 65], "win": [1, 3]},
        "exec": {"stop": ["signal_low", "shadow_low"], "hold": [2, 3, 5],
                 "states": [None, ["strong", "lean_strong"], ["strong"]]},
    },
    "B": {
        "strategy": STRATEGY_B, "base_exec": EXEC_LEADER,
        "params": {},
        "exec": {"leader_mode": ["break", "board"], "hold": [2, 3, 5],
                 "gap_main": [(0.02, 0.06), (0.0, 0.09)],
                 "states": [None, ["strong"]]},
    },
}
GRID_MIN_TRADES_PER_YEAR = 10

STATE_CN = {"strong": "强势", "lean_strong": "偏强", "range": "震荡",
            "lean_weak": "偏弱", "weak": "弱势"}
LTSZ_BINS = [0, 30, 50, 80, 150, 300, 1e12]
LTSZ_LABELS = ["<30亿", "30-50亿", "50-80亿", "80-150亿", "150-300亿", ">300亿"]
BASE_COLS = ["symbol", "date", "open", "high", "low", "close", "volume", "amount",
             "raw_close", "turnover_rate", "consecutive_limit_ups", "consecutive_limit_downs"]


# =============================== 数据 ===============================
SAVED_DATA_DIR = HERE / "data_dir.txt"


def _is_data_dir(p: Path) -> bool:
    try:
        return (p / "kline_daily_enriched").is_dir()
    except OSError:
        return False


def find_data_dir() -> Path | None:
    """自动查找 TSP 数据目录：先看上次保存的，再看安装默认位置，最后扫描各盘浅层目录"""
    import os
    import string
    if SAVED_DATA_DIR.exists():
        p = Path(SAVED_DATA_DIR.read_text(encoding="utf-8").strip())
        if _is_data_dir(p):
            return p
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [Path(r"D:\TSP\data"), Path(local) / "Programs" / "TSP" / "data",
                  Path(r"C:\TSP\data"), Path(r"E:\TSP\data")]
    if os.environ.get("DATA_DIR"):
        candidates.insert(0, Path(os.environ["DATA_DIR"]))
    for p in candidates:
        if _is_data_dir(p):
            return p
    if sys.platform != "win32":
        return None
    print("正在各磁盘查找 TSP 数据目录（只查前3层文件夹）...", flush=True)
    skip = {"windows", "$recycle.bin", "system volume information", "programdata", "recovery"}
    for letter in string.ascii_uppercase[2:]:
        root = Path(f"{letter}:\\")
        if not root.exists():
            continue
        level = [root]
        for _ in range(3):
            nxt = []
            for d in level:
                try:
                    for e in os.scandir(d):
                        if e.is_dir(follow_symlinks=False) and e.name.lower() not in skip:
                            p = Path(e.path)
                            if e.name.lower() == "data" and _is_data_dir(p):
                                return p
                            nxt.append(p)
                except OSError:
                    continue
            level = nxt
    return None


def resolve_data_dir(cfg: dict) -> Path:
    raw = cfg.get("data_dir") or "auto"
    p = find_data_dir() if raw == "auto" else Path(raw)
    if p is None or not _is_data_dir(p):
        raise SystemExit(
            "找不到 TSP 数据目录（里面应有 kline_daily_enriched 文件夹）。\n"
            "请右键桌面 TSP 图标 → 打开文件所在的位置，把那里的 data 文件夹路径用 --data 传进来，例如：\n"
            '  python tsp_lab.py --data "D:\\TSP\\data"')
    try:
        p = p.resolve()
        SAVED_DATA_DIR.write_text(str(p), encoding="utf-8")
    except OSError:
        pass
    print(f"TSP 数据目录：{p}", flush=True)
    return p
def _partition_files(root: Path, lo: date, hi: date) -> list[str]:
    files = []
    for p in root.glob("date=*"):
        try:
            d = date.fromisoformat(p.name[5:15])
        except ValueError:
            continue
        f = p / "part.parquet"
        if lo <= d <= hi and f.exists():
            files.append(str(f))
    return sorted(files)


def data_range(data_dir: Path) -> tuple[date | None, date | None, int]:
    days = []
    for p in (data_dir / "kline_daily_enriched").glob("date=*"):
        try:
            days.append(date.fromisoformat(p.name[5:15]))
        except ValueError:
            pass
    if not days:
        return None, None, 0
    return min(days), max(days), len(days)


def load_instruments(data_dir: Path) -> pl.DataFrame:
    files = sorted(str(p) for p in (data_dir / "instruments").glob("**/*.parquet"))
    if not files:
        return pl.DataFrame(schema={"symbol": pl.Utf8, "name": pl.Utf8, "float_shares": pl.Float64})
    df = pl.concat([pl.read_parquet(f) for f in files], how="diagonal_relaxed")
    keep = [c for c in ["symbol", "name", "float_shares", "total_shares", "listing_date"] if c in df.columns]
    df = df.select(keep).unique(subset=["symbol"], keep="last")
    for c in ("float_shares", "total_shares"):
        if c in df.columns:
            df = df.with_columns(pl.col(c).cast(pl.Float64, strict=False))
    return df


def load_regime(data_dir: Path) -> dict:
    p = data_dir / "regime_history" / "part.parquet"
    if not p.exists():
        return {}
    df = pl.read_parquet(p)
    if "state" not in df.columns:
        return {}
    df = df.with_columns(pl.col("date").cast(pl.Date))
    return dict(zip(df["date"].to_list(), df["state"].to_list()))


def load_panel(data_dir: Path, start: date, end: date, lookback: int, hold_buffer: int = 30,
               sample: int | None = None) -> pl.DataFrame:
    lo = start - timedelta(days=int(lookback * 1.6) + 30)
    hi = end + timedelta(days=hold_buffer)
    files = _partition_files(data_dir / "kline_daily_enriched", lo, hi)
    if not files:
        raise SystemExit(f"在 {data_dir / 'kline_daily_enriched'} 找不到 {lo}~{hi} 的数据")
    t0 = time.perf_counter()
    try:
        lf = pl.scan_parquet(files, missing_columns="insert", extra_columns="ignore")
    except TypeError:  # 老版本 polars 没有这两个参数
        lf = pl.scan_parquet(files)
    have = lf.collect_schema().names()
    cols = [c for c in BASE_COLS if c in have]
    df = lf.select(cols).collect()
    casts = {c: pl.Float64 for c in cols if c not in ("symbol", "date")}
    for c in ("consecutive_limit_ups", "consecutive_limit_downs"):
        if c in casts:
            casts[c] = pl.Int32
    df = df.with_columns([pl.col(c).cast(t, strict=False) for c, t in casts.items()]
                         + [pl.col("date").cast(pl.Date)])
    for c in ("consecutive_limit_ups", "consecutive_limit_downs"):
        if c not in df.columns:
            df = df.with_columns(pl.lit(0, dtype=pl.Int32).alias(c))
    inst = load_instruments(data_dir)
    if not inst.is_empty():
        df = df.join(inst, on="symbol", how="left")
    df = df.filter(pl.col("volume") > 0)
    if sample:
        syms = df["symbol"].unique().sort().sample(min(sample, df["symbol"].n_unique()), seed=42)
        df = df.filter(pl.col("symbol").is_in(syms.to_list()))
    df = df.sort(["symbol", "date"])
    print(f"载入 {len(files)} 个交易日，{df['symbol'].n_unique()} 只股票，{len(df):,} 行，"
          f"{time.perf_counter() - t0:.1f}s", flush=True)
    return df


def add_indicators(df: pl.DataFrame, need: set[str]) -> pl.DataFrame:
    """补齐 TSP 运行时计算的常用指标（只算策略声明需要的）"""
    c, v = pl.col("close"), pl.col("volume")
    exprs = {
        "prev_close": c.shift(1),
        "change_pct": c / c.shift(1) - 1,
        "amplitude": (pl.col("high") - pl.col("low")) / c.shift(1),
        "vol_ma5": v.rolling_mean(5), "vol_ma10": v.rolling_mean(10),
        "vol_ratio_5d": v / v.rolling_mean(5).shift(1),
        **{f"ma{n}": c.rolling_mean(n) for n in (5, 10, 20, 30, 60)},
        **{f"momentum_{n}d": c / c.shift(n) - 1 for n in (5, 10, 20, 30, 60)},
        "high_60d": pl.col("high").rolling_max(60), "low_60d": pl.col("low").rolling_min(60),
    }
    todo = [e.over("symbol").alias(k) for k, e in exprs.items() if k in need and k not in df.columns]
    if todo:
        df = df.with_columns(todo)
    missing = [k for k in need if k not in df.columns]
    if missing:
        print(f"警告：策略需要的列本工具未实现：{missing}", flush=True)
    return df


def basic_filter_expr(bf: dict, columns: list[str]) -> pl.Expr:
    """与 TSP StrategyEngine._basic_filter_expr 相同的规则（缺列的条件跳过，与 TSP 一致）"""
    ex = [pl.lit(True)]
    c = pl.col("close")
    if bf.get("price_min") is not None:
        ex.append(c >= bf["price_min"])
    if bf.get("price_max") is not None:
        ex.append(c <= bf["price_max"])
    for key, col, op in [("market_cap_min", "total_shares", ">="), ("market_cap_max", "total_shares", "<="),
                         ("float_cap_min", "float_shares", ">="), ("float_cap_max", "float_shares", "<=")]:
        if bf.get(key) is not None and col in columns:
            cap = c * pl.col(col)
            ex.append((cap >= bf[key]) if op == ">=" else (cap <= bf[key]))
    if bf.get("amount_min") is not None:
        ex.append(pl.col("amount") >= bf["amount_min"])
    if bf.get("amount_max") is not None:
        ex.append(pl.col("amount") <= bf["amount_max"])
    if bf.get("turnover_min") is not None and "turnover_rate" in columns:
        ex.append(pl.col("turnover_rate") >= bf["turnover_min"])
    if bf.get("turnover_max") is not None and "turnover_rate" in columns:
        ex.append(pl.col("turnover_rate") <= bf["turnover_max"])
    if bf.get("exclude_st") and "name" in columns:
        ex.append(~pl.col("name").fill_null("").str.contains("(?i)ST|\\*ST|退"))
    boards = bf.get("boards") or []
    if boards:
        s = pl.col("symbol")
        m = {"沪主板": s.str.starts_with("60"), "深主板": s.str.starts_with("00"),
             "创业板": s.str.starts_with("300") | s.str.starts_with("301") | s.str.starts_with("302"),
             "科创板": s.str.starts_with("688") | s.str.starts_with("689"),
             "北交所": s.str.ends_with(".BJ")}
        ex.append(pl.any_horizontal([m[b] for b in boards if b in m] or [pl.lit(True)]))
    return pl.all_horizontal(ex).fill_null(False)


# =============================== 策略 ===============================
def load_strategy(path: str):
    spec = importlib.util.spec_from_file_location(Path(path).stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "filter_history"):
        raise SystemExit(f"{path} 没有 filter_history()，本工具只支持 TSP 历史窗口策略")
    defaults = {p["id"]: p.get("default") for p in mod.META.get("params", [])}
    return mod, defaults


def run_signals(mod, panel: pl.DataFrame, params: dict, start: date, end: date) -> pl.DataFrame:
    t0 = time.perf_counter()
    hits = mod.filter_history(panel, params)
    hits = hits.filter((pl.col("date") >= start) & (pl.col("date") <= end))
    bf = mod.META.get("basic_filter") or {}
    if bf.get("enabled", True):
        missing = [k for k, col in [("float_cap_min", "float_shares"), ("market_cap_min", "total_shares"),
                                    ("exclude_st", "name")] if bf.get(k) and col not in hits.columns]
        if missing:
            print(f"  提示：缺少个股维表列，跳过基础过滤 {missing}", flush=True)
        hits = hits.filter(basic_filter_expr(bf, hits.columns))
    print(f"  信号 {len(hits)} 个（{time.perf_counter() - t0:.1f}s）", flush=True)
    return hits


# =============================== 撮合 ===============================
class Market:
    """面板转 numpy，行号连续，同一股票按日期升序"""

    def __init__(self, panel: pl.DataFrame, regime: dict):
        p = panel.with_row_index("_row")
        self.row_of = dict(zip(zip(p["symbol"].to_list(), p["date"].to_list()), p["_row"].to_list()))
        self.sym = p["symbol"].to_numpy()
        self.dates = p["date"].to_list()
        self.O, self.H, self.L, self.C = (p[c].to_numpy().astype(float) for c in ["open", "high", "low", "close"])
        self.LU = p["consecutive_limit_ups"].fill_null(0).to_numpy()
        self.LD = p["consecutive_limit_downs"].fill_null(0).to_numpy()
        fs = p["float_shares"].to_numpy().astype(float) if "float_shares" in p.columns else np.full(len(p), np.nan)
        self.LTSZ = self.C * fs / 1e8
        self.name = p["name"].to_list() if "name" in p.columns else [""] * len(p)
        self.n = len(p)
        self.regime = regime
        # 疑似除权日：跌幅超过涨跌停幅度（未复权数据里只有除权除息会这样）
        sym_start = np.zeros(self.n, dtype=np.int64)
        new_sym = np.ones(self.n, dtype=bool)
        new_sym[1:] = self.sym[1:] != self.sym[:-1]
        starts = np.where(new_sym)[0]
        sym_start[starts] = starts
        self.sym_start = np.maximum.accumulate(sym_start)
        prev_c = np.full(self.n, np.nan)
        prev_c[1:] = self.C[:-1]
        prev_c[new_sym] = np.nan
        lim = np.array([0.2 if is_20cm(s) else 0.1 for s in self.sym])
        with np.errstate(invalid="ignore", divide="ignore"):
            drop = np.fmin(self.O, self.L) / prev_c - 1
        self.exright = np.nan_to_num(drop, nan=0.0) < -(lim + 0.015)
        self.exr_cum = np.cumsum(self.exright)

    def exright_between(self, a: int, b: int) -> bool:
        """同一股票第 a 行到第 b 行之间（含）是否有疑似除权日"""
        a = max(a, self.sym_start[b])
        before = self.exr_cum[a - 1] if a > 0 else 0
        return bool(self.exr_cum[b] - before > 0)

    def same(self, i, j):
        return 0 <= j < self.n and self.sym[i] == self.sym[j]

    def one_price(self, j):
        prices = (self.O[j], self.H[j], self.L[j], self.C[j])
        return max(prices) - min(prices) <= max(abs(self.C[j]) * 1e-4, 0.01)

    def one_up(self, j):
        return self.LU[j] >= 1 and self.one_price(j)

    def one_down(self, j):
        return self.LD[j] >= 1 and self.one_price(j)


def is_20cm(symbol: str) -> bool:
    return symbol.startswith(("300", "301", "302", "688", "689"))


def simulate(mkt: Market, hits: pl.DataFrame, ex: dict, fees: dict) -> list[dict]:
    slip = fees["slippage_bps"] / 1e4
    buy_cost = fees["commission"] + slip
    sell_cost = fees["commission"] + fees["stamp"] + slip
    has_score = "fb_score" in hits.columns
    has_shadow = "fb_shadow_low" in hits.columns
    out = []
    rows = hits.select(["symbol", "date"] + (["fb_score"] if has_score else [])
                       + (["fb_shadow_low"] if has_shadow else [])).sort(["date", "symbol"]).to_dicts()
    busy_until: dict[str, int] = {}   # 同一股票持仓期间不重复买（与 TSP 一致）
    for r in rows:
        i = mkt.row_of.get((r["symbol"], r["date"]))
        if i is None:
            continue
        sym = r["symbol"]
        if i <= busy_until.get(sym, -1):
            out.append({"symbol": sym, "name": mkt.name[i], "signal_date": r["date"], "status": "持仓中忽略"})
            continue
        state = mkt.regime.get(r["date"])
        rec = {"symbol": sym, "name": mkt.name[i], "signal_date": r["date"],
               "score": r.get("fb_score"), "ltsz": mkt.LTSZ[i],
               "board": "20cm" if is_20cm(sym) else "主板",
               "state": STATE_CN.get(state, state or "无数据")}
        if ex.get("states") and state not in ex["states"]:
            out.append({**rec, "status": "环境过滤"})
            continue

        # ---- 买入 ----
        if ex["entry"] == "close":
            if mkt.LU[i] >= 1:
                out.append({**rec, "status": "收盘涨停买不进"})
                continue
            e, entry = i, mkt.C[i]
        else:
            e = i + 1
            if not mkt.same(i, e):
                out.append({**rec, "status": "无后续数据"})
                continue
            gap = mkt.O[e] / mkt.C[i] - 1
            rec["gap"] = gap
            if mkt.one_up(e):
                out.append({**rec, "status": "一字涨停买不进"})
                continue
            if ex["entry"] == "next_open":
                if ex.get("gap_max") is not None and gap > ex["gap_max"]:
                    out.append({**rec, "status": "高开过多放弃"})
                    continue
                if ex.get("gap_min") is not None and gap < ex["gap_min"]:
                    out.append({**rec, "status": "低开放弃"})
                    continue
                entry = mkt.O[e]
            else:  # leader_confirm
                lo, hi = ex["gap_20cm"] if is_20cm(sym) else ex["gap_main"]
                if not (lo <= gap <= hi):
                    out.append({**rec, "status": "竞价不符放弃"})
                    continue
                if ex.get("leader_mode") == "board":
                    if mkt.LU[e] < 1:
                        out.append({**rec, "status": "未封板未买"})
                        continue
                    entry = mkt.H[e]
                else:
                    if mkt.H[e] < mkt.H[i]:
                        out.append({**rec, "status": "未突破未买"})
                        continue
                    entry = max(mkt.O[e], mkt.H[i])

        # ---- 止损价 ----
        st = ex["stop"]
        if st == "pct":
            stop = entry * (1 + ex["stop_pct"])
        elif st == "signal_low":
            stop = mkt.L[i] * (1 - ex.get("stop_buffer", 0))
        elif st == "shadow_low" and has_shadow and r.get("fb_shadow_low") is not None:
            stop = r["fb_shadow_low"] * (1 - ex.get("stop_buffer", 0))
        elif st == "shadow_low":
            stop = mkt.L[i] * (1 - ex.get("stop_buffer", 0))
        else:
            stop = -np.inf
        if stop >= entry:  # 止损价不低于买入价时退回固定百分比，避免买入即止损
            stop = entry * (1 + ex["stop_pct"])

        # ---- 卖出（T+1：买入当天不卖；hold 与 TSP MAX_HOLD_DAYS 同义：买入后第 hold 个交易日收盘卖）----
        hold = int(ex["hold"])
        j, px, why, blocked = e + 1, None, None, 0
        last = e + hold
        pending = None
        max_high = -np.inf
        while mkt.same(i, j):
            max_high = max(max_high, mkt.H[j])
            if pending:
                if mkt.one_down(j):
                    blocked += 1
                    j += 1
                    continue
                px, why = mkt.O[j], pending + "(顺延)"
                break
            t2 = ex.get("t2_open_stop")
            cand = None
            if t2 is not None and j == e + 1 and mkt.O[j] <= entry * (1 + t2):
                cand = (mkt.O[j], "次日低开卖出")
            elif mkt.O[j] <= stop:
                cand = (mkt.O[j], "跳空止损")
            elif mkt.L[j] <= stop:
                cand = (stop, "止损")
            elif j >= last:
                if mkt.LD[j] >= 1:      # 收盘封跌停按收盘价卖不出
                    pending, blocked = "到期", blocked + 1
                    j += 1
                    continue
                cand = (mkt.C[j], "到期")
            if cand:
                if mkt.one_down(j):
                    pending, blocked = cand[1], blocked + 1
                    j += 1
                    continue
                px, why = cand
                break
            j += 1
        if px is None:
            if j - 1 > e:  # 数据到头：按最后收盘价记录平仓（与 TSP 期末强制平仓一致）
                j -= 1
                px, why = mkt.C[j], "数据结束平仓"
            else:
                out.append({**rec, "status": "数据不足未平仓"})
                continue
        busy_until[sym] = j
        ret = px * (1 - sell_cost) / (entry * (1 + buy_cost)) - 1
        # 信号前200根到卖出日之间有疑似除权：形态计算或持仓收益可能被假跳空污染
        exr = mkt.exright_between(i - 200, j)
        out.append({**rec, "status": "成交", "exright": exr,
                    "entry_date": mkt.dates[e], "exit_date": mkt.dates[j],
                    "entry": round(entry, 4), "exit": round(px, 4), "ret": ret,
                    "hold_days": j - e + 1, "exit_reason": why, "blocked_days": blocked,
                    "max_up": max_high / entry - 1 if np.isfinite(max_high) else np.nan})
    return out


def portfolio(trades: list[dict], max_pos: int) -> dict:
    """等权槽位组合：资金分成 max_pos 份，空槽才买；同日按流通市值从大到小（全市场回测显示评分高的反而差，大市值更好）"""
    done = sorted([t for t in trades if t.get("status") == "成交"],
                  key=lambda t: (t["entry_date"], -float(np.nan_to_num(t.get("ltsz") or 0.0)), t["symbol"]))
    slots = [{"free": date.min, "eq": 1.0} for _ in range(max_pos)]
    taken, skipped, events = 0, 0, []
    for t in done:
        free = [s for s in slots if s["free"] < t["entry_date"]]
        if not free:
            skipped += 1
            t["in_portfolio"] = False
            continue
        s = free[0]
        s["eq"] *= 1 + t["ret"]
        s["free"] = t["exit_date"]
        t["in_portfolio"] = True
        taken += 1
        events.append((t["exit_date"], sum(x["eq"] for x in slots) / max_pos))
    if not events:
        return {"组合成交": 0}
    events.sort()
    eq = np.array([v for _, v in events])
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    dd = (eq / peak - 1).min()
    years = max((events[-1][0] - done[0]["entry_date"]).days / 365.25, 1 / 12)
    total = eq[-1] - 1
    return {"组合成交": taken, "满仓跳过": skipped, "总收益%": round(total * 100, 2),
            "年化%": round(((1 + total) ** (1 / years) - 1) * 100, 2), "最大回撤%": round(dd * 100, 2)}


# =============================== 统计 ===============================
def stats(rets: np.ndarray, max_up: np.ndarray | None = None) -> dict:
    r = rets[~np.isnan(rets)]
    if len(r) == 0:
        return {"笔数": 0}
    win, loss = r[r > 0], r[r <= 0]
    pf = win.sum() / -loss.sum() if loss.sum() < 0 else math.inf
    res = {"笔数": len(r), "胜率%": round(len(win) / len(r) * 100, 1),
           "平均%": round(r.mean() * 100, 2), "中位%": round(float(np.median(r)) * 100, 2),
           "均盈%": round(win.mean() * 100, 2) if len(win) else 0.0,
           "均亏%": round(loss.mean() * 100, 2) if len(loss) else 0.0,
           "盈亏比": round(-win.mean() / loss.mean(), 2) if len(win) and len(loss) and loss.mean() < 0 else None,
           "利润因子": round(pf, 2) if math.isfinite(pf) else None,
           "最大单亏%": round(r.min() * 100, 2)}
    if max_up is not None:
        mu = max_up[~np.isnan(max_up)]
        res["持有期最高涨≥5%占比"] = round(float((mu >= 0.05).mean()) * 100, 1) if len(mu) else None
    return res


def group_table(df: pl.DataFrame, by: str) -> pl.DataFrame:
    rows = []
    for key, g in df.group_by(by, maintain_order=True):
        k = key[0] if isinstance(key, tuple) else key
        rows.append({by: str(k), **stats(g["ret"].to_numpy(), g["max_up"].to_numpy())})
    return pl.DataFrame(rows).sort(by) if rows else pl.DataFrame()


def report(name: str, trades: list[dict], cfg: dict) -> tuple[dict, list[tuple[str, pl.DataFrame]]]:
    allt = pl.DataFrame(trades, infer_schema_length=None) if trades else pl.DataFrame()
    print(f"\n{'=' * 22} {name} {'=' * 22}")
    if allt.is_empty():
        print("没有信号")
        return {}, []
    print("信号状态：", dict(allt.group_by("status").len().sort("len", descending=True).iter_rows()))
    t = allt.filter(pl.col("status") == "成交")
    if t.is_empty():
        print("没有成交")
        return {}, []
    pf = portfolio(trades, cfg["portfolio"]["max_positions"])
    overall = {**stats(t["ret"].to_numpy(), t["max_up"].to_numpy()), **pf}
    print("整体：", json.dumps(overall, ensure_ascii=False))
    t = t.with_columns(
        pl.col("signal_date").dt.year().alias("年份"),
        pl.col("ltsz").cut(LTSZ_BINS[1:-1], labels=LTSZ_LABELS).cast(pl.Utf8).fill_null("无数据").alias("流通市值"),
        pl.when(pl.col("score").is_null()).then(pl.lit("无"))
        .when(pl.col("score") >= 75).then(pl.lit("75+"))
        .when(pl.col("score") >= 65).then(pl.lit("65-74"))
        .otherwise(pl.lit("<65")).alias("评分段"),
    )
    tables = []
    for by, title in [("年份", "按年份"), ("state", "按信号日市场环境"), ("流通市值", "按流通市值"),
                      ("评分段", "按评分"), ("board", "按板块"), ("exit_reason", "按卖出原因")]:
        g = group_table(t, by)
        if not g.is_empty():
            print(f"\n--- {title} ---")
            print(g)
            tables.append((title, g))
    return overall, tables


def yearly_robust(trades: list[dict]) -> dict:
    """TSP 稳健分思路：按年份算每笔平均收益，取 0.6×中位 + 0.4×最差"""
    t = [x for x in trades if x.get("status") == "成交"]
    by = {}
    for x in t:
        by.setdefault(x["signal_date"].year, []).append(x["ret"])
    yrs = {y: v for y, v in by.items() if len(v) >= GRID_MIN_TRADES_PER_YEAR}
    if not yrs:
        return {"稳健分%": None, "有效年数": 0}
    means = np.array([np.mean(v) for v in yrs.values()])
    res = {"稳健分%": round((0.6 * np.median(means) + 0.4 * means.min()) * 100, 3),
           "有效年数": len(yrs), "最差年%": round(means.min() * 100, 2)}
    for y in sorted(by):
        res[f"{y}年均%"] = round(np.mean(by[y]) * 100, 2)
        res[f"{y}笔"] = len(by[y])
    return res


# =============================== 主流程 ===============================
def _dates(cfg):
    return date.fromisoformat(cfg["start"]), date.fromisoformat(cfg["end"])


def prepare(cfg: dict, strategy_paths: list[str]):
    data_dir = resolve_data_dir(cfg)
    start, end = _dates(cfg)
    mods = {p: load_strategy(p) for p in set(strategy_paths)}
    lookback = max(int(getattr(m, "LOOKBACK_DAYS", 1) or 1) for m, _ in mods.values())
    lo, hi, n_days = data_range(data_dir)
    if lo is None:
        raise SystemExit("TSP 数据目录里没有日线数据，请先在 TSP 里同步数据")
    print(f"本地日线：{lo} ~ {hi}，共 {n_days} 个交易日", flush=True)
    end = min(end, hi)
    need = set().union(*[set(getattr(m, "REQUIRED_FEATURES", set()) or set()) for m, _ in mods.values()])
    panel = add_indicators(load_panel(data_dir, start, end, lookback, sample=cfg.get("sample")), need)
    unadj = warn_if_unadjusted(panel)
    mode = cfg.get("exright", "auto")
    cfg["drop_exright"] = (mode == "drop") or (mode == "auto" and unadj)
    if cfg["drop_exright"]:
        print("已开启：统计时剔除疑似除权影响的交易（CONFIG['exright'] 可改为 keep 保留）", flush=True)
    mkt = Market(panel, load_regime(data_dir))
    if mkt.regime and min(mkt.regime) > start + timedelta(days=30):
        print(f"注意：市场环境数据从 {min(mkt.regime)} 才开始，更早的交易显示“无数据”，"
              "按环境过滤时这些交易会被全部过滤。请在 TSP“市场环境”页点“重算”补全历史。", flush=True)
    if not mkt.regime:
        print("提示：没有找到 regime_history，市场环境分组显示为“无数据”", flush=True)
    mkt.trade_dates = panel["date"].unique().sort().to_list()
    return panel, mkt, mods, start, end


def effective_start(mod, mkt: "Market", start: date) -> date | None:
    """策略至少要有 WARMUP_BARS 根K线才可能出信号；本地数据不够早时自动后移起点"""
    warm = int(getattr(mod, "WARMUP_BARS", getattr(mod, "LOOKBACK_DAYS", 1)) or 1)
    dates = mkt.trade_dates
    if len(dates) <= warm:
        print(f"  本地只有 {len(dates)} 个交易日，这个策略至少需要 {warm} 个，无法回测。"
              f"请在 TSP“数据 → 日K → 向前扩展历史”补数据", flush=True)
        return None
    first_ok = dates[warm]
    if start < first_ok:
        print(f"  提示：策略需要 {warm} 个交易日预热，回测起点从 {start} 后移到 {first_ok}"
              f"（想测更早，请在 TSP“数据 → 日K → 向前扩展历史”补数据）", flush=True)
        return first_ok
    return start


def warn_if_unadjusted(panel: pl.DataFrame) -> bool:
    if "raw_close" not in panel.columns:
        return False
    same = panel.select(((pl.col("close") - pl.col("raw_close")).abs() < 1e-6).mean()).item()
    if same is not None and same > 0.999:
        print("注意：本地数据的复权价和原始价完全相同，说明没有复权因子（TickFlow 免费档没有复权因子）。"
              "除权除息日会出现假跳空，可能误触发止损或形态信号；送转股的影响最大。", flush=True)
        return True
    return False


def apply_exright(trades: list[dict], cfg: dict) -> list[dict]:
    """未复权数据：剔除信号前200日到卖出日之间有疑似除权（跌幅超过跌停幅度）的交易"""
    done = [t for t in trades if t.get("status") == "成交"]
    hit = [t for t in done if t.get("exright")]
    if not done:
        return trades
    r_all = np.array([t["ret"] for t in done])
    r_cln = np.array([t["ret"] for t in done if not t.get("exright")])
    print(f"  疑似除权影响 {len(hit)}/{len(done)} 笔；全部平均 {r_all.mean() * 100:.2f}%，"
          f"剔除后平均 {r_cln.mean() * 100 if len(r_cln) else float('nan'):.2f}%", flush=True)
    if cfg.get("drop_exright"):
        for t in hit:
            t["status"] = "除权影响剔除"
    return trades


def save(name: str, trades: list[dict], tables, cfg: dict):
    out = Path(cfg["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    if trades:
        pl.DataFrame(trades, infer_schema_length=None).write_csv(out / f"trades_{safe}.csv", include_bom=True)
    if tables:
        frames = [g.with_columns(pl.lit(title).alias("分组")).select(
            ["分组", pl.col(g.columns[0]).cast(pl.Utf8).alias("分组值"), *g.columns[1:]]) for title, g in tables]
        pl.concat(frames, how="diagonal_relaxed").write_csv(out / f"summary_{safe}.csv", include_bom=True)


def cmd_runs(cfg: dict, only: str | None):
    runs = [r for r in RUNS if only is None or r["name"] == only]
    if not runs:
        raise SystemExit(f"RUNS 里没有方案 {only}，可选：{[r['name'] for r in RUNS]}")
    panel, mkt, mods, start, end = prepare(cfg, [r["strategy"] for r in runs])
    cache, summary = {}, []
    for r in runs:
        mod, defaults = mods[r["strategy"]]
        params = {**defaults, **r["params"]}
        base_bf = copy.deepcopy(mod.META.get("basic_filter") or {})
        bf_over = r.get("basic_filter") or {}
        mod.META["basic_filter"] = {**base_bf, **bf_over}
        key = (r["strategy"], json.dumps(params, sort_keys=True), json.dumps(bf_over, sort_keys=True))
        print(f"\n>>> {r['name']}  参数 {params}", flush=True)
        run_start = effective_start(mod, mkt, start)
        if run_start is None:
            mod.META["basic_filter"] = base_bf
            summary.append({"方案": r["name"], "笔数": 0})
            continue
        key = key + (str(run_start),)
        if key not in cache:
            cache[key] = run_signals(mod, panel, params, run_start, end)
        mod.META["basic_filter"] = base_bf
        trades = apply_exright(simulate(mkt, cache[key], r["exec"], cfg["fees"]), cfg)
        overall, tables = report(r["name"], trades, cfg)
        save(r["name"], trades, tables, cfg)
        summary.append({"方案": r["name"], **overall, **yearly_robust(trades)})
    if summary:
        s = pl.DataFrame(summary, infer_schema_length=None)
        print("\n" + "=" * 22 + " 方案汇总（完整列见 runs_summary.csv）" + "=" * 22)
        key = [c for c in ["方案", "笔数", "胜率%", "平均%", "盈亏比", "利润因子", "总收益%", "年化%",
                           "最大回撤%", "稳健分%", "最差年%"] if c in s.columns]
        print(s.select(key))
        Path(cfg["out_dir"]).mkdir(parents=True, exist_ok=True)
        s.write_csv(Path(cfg["out_dir"]) / "runs_summary.csv", include_bom=True)
    print(f"\n结果已保存到 {cfg['out_dir']}")


def _product(space: dict) -> list[dict]:
    combos = [{}]
    for k, vals in space.items():
        combos = [{**c, k: v} for c in combos for v in vals]
    return combos


def cmd_grid(cfg: dict, gname: str):
    g = GRIDS.get(gname)
    if g is None:
        raise SystemExit(f"没有网格 {gname}，可选：{list(GRIDS)}")
    panel, mkt, mods, start, end = prepare(cfg, [g["strategy"]])
    mod, defaults = mods[g["strategy"]]
    start = effective_start(mod, mkt, start)
    if start is None:
        return
    rows = []
    pcombos, ecombos = _product(g["params"]), _product(g["exec"])
    print(f"网格：{len(pcombos)} 组策略参数 × {len(ecombos)} 组交易规则", flush=True)
    for pc in pcombos:
        params = {**defaults, **pc}
        print(f"\n参数 {pc}", flush=True)
        hits = run_signals(mod, panel, params, start, end)
        for ec in ecombos:
            ex = {**copy.deepcopy(g["base_exec"]), **ec}
            trades = simulate(mkt, hits, ex, cfg["fees"])
            if cfg.get("drop_exright"):
                for t in trades:
                    if t.get("status") == "成交" and t.get("exright"):
                        t["status"] = "除权影响剔除"
            done = np.array([t["ret"] for t in trades if t.get("status") == "成交"])
            rows.append({**{f"p.{k}": str(v) for k, v in pc.items()},
                         **{f"e.{k}": str(v) for k, v in ec.items()},
                         **stats(done), **portfolio(trades, cfg["portfolio"]["max_positions"]),
                         **yearly_robust(trades)})
    df = pl.DataFrame(rows, infer_schema_length=None).sort("稳健分%", descending=True, nulls_last=True)
    out = Path(cfg["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    df.write_csv(out / f"grid_{gname}.csv", include_bom=True)
    key = [c for c in df.columns if c.startswith(("p.", "e."))] + [
        c for c in ["笔数", "胜率%", "平均%", "盈亏比", "总收益%", "最大回撤%", "稳健分%", "有效年数", "最差年%"]
        if c in df.columns]
    with pl.Config(tbl_rows=20, tbl_cols=-1, tbl_width_chars=250, fmt_str_lengths=40):
        print("\n=== 稳健分前20（0.6×年度中位 + 0.4×年度最差，每年至少"
              f"{GRID_MIN_TRADES_PER_YEAR}笔才计入；完整列见 CSV）===")
        print(df.select(key).head(20))
    print(f"\n完整结果：{out / f'grid_{gname}.csv'}")


def cmd_check(cfg: dict):
    data_dir = resolve_data_dir(cfg)
    lo, hi, n = data_range(data_dir)
    print(f"数据目录：{data_dir}")
    if not n:
        raise SystemExit("没有找到 kline_daily_enriched/date=*/part.parquet，请确认 --data 指向 TSP 的 data 目录")
    print(f"Enriched 日线：{lo} ~ {hi}，共 {n} 个交易日")
    inst = load_instruments(data_dir)
    print(f"个股维表：{len(inst)} 只，含列 {inst.columns}")
    reg = load_regime(data_dir)
    print(f"市场环境：{len(reg)} 天" + (f"（{min(reg)} ~ {max(reg)}）" if reg else "，未找到"))
    for label, path in [("策略A", STRATEGY_A), ("策略B", STRATEGY_B)]:
        mod, _ = load_strategy(path)
        warm = int(getattr(mod, "WARMUP_BARS", 1))
        usable = n - warm
        print(f"{label}：需要 {warm} 个交易日预热，可回测约 {max(usable, 0)} 个交易日"
              + ("" if usable >= 480 else "（不到2年，结论不可靠）"))
    if n < 190 + 480:
        print(f"建议：在 TSP“数据”页的日K卡片里点“向前扩展历史”，补到至少 3~4 年"
              f"（策略A 想从 2023 年测起，日K需从 2022 年初开始）")
    latest = sorted((data_dir / "kline_daily_enriched").glob("date=*"))[-1] / "part.parquet"
    try:
        df = pl.read_parquet(latest, columns=["close", "raw_close"])
        if df.height and ((df["close"] - df["raw_close"]).abs() < 1e-6).mean() > 0.999:
            # 最新一天前复权价=原始价是正常的，需要看更早的日子
            first = sorted((data_dir / "kline_daily_enriched").glob("date=*"))[0] / "part.parquet"
            d0 = pl.read_parquet(first, columns=["close", "raw_close"])
            if d0.height and ((d0["close"] - d0["raw_close"]).abs() < 1e-6).mean() > 0.999:
                print("注意：最早一天的复权价也等于原始价，可能没有复权因子（TickFlow 免费档没有）。"
                      "除权除息日会出现假跳空，送转股影响最大。")
            else:
                print("复权：正常（历史价格已前复权）")
    except Exception as e:  # noqa: BLE001
        print(f"复权检查跳过：{e}")


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)

    def flush(self):
        for st in self.streams:
            st.flush()


def main():
    ap = argparse.ArgumentParser(description="TSP 研究回测器")
    ap.add_argument("--data", help="TSP 数据目录（含 kline_daily_enriched）")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--run", help="只跑 RUNS 中指定名称的方案")
    ap.add_argument("--grid", help="跑 GRIDS 中的参数网格，如 A")
    ap.add_argument("--check", action="store_true", help="只检查数据")
    ap.add_argument("--sample", type=int, help="随机抽取N只股票快速试跑（固定随机种子）")
    a = ap.parse_args()
    cfg = copy.deepcopy(CONFIG)
    cfg["sample"] = a.sample
    if a.data:
        cfg["data_dir"] = a.data
    if a.start:
        cfg["start"] = a.start
    if a.end:
        cfg["end"] = a.end
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    # 屏幕输出同时写入 output/log_时间.txt，方便发给别人看
    Path(cfg["out_dir"]).mkdir(parents=True, exist_ok=True)
    log_path = Path(cfg["out_dir"]) / f"log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    sys.stdout = _Tee(sys.stdout, open(log_path, "w", encoding="utf-8"))
    pl.Config.set_tbl_rows(50)
    pl.Config.set_tbl_cols(-1)
    pl.Config.set_tbl_width_chars(220)
    if a.check:
        cmd_check(cfg)
    elif a.grid:
        cmd_grid(cfg, a.grid)
    else:
        cmd_runs(cfg, a.run)


if __name__ == "__main__":
    main()
