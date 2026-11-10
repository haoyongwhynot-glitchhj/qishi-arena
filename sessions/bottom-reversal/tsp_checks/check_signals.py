"""用样本数据（TSP 列格式）在 polars 1.44 下跑 filter_history，与 numpy 回测信号逐条对比"""
import importlib.util
import os
from pathlib import Path

import pandas as pd
import polars as pl

PARTS = Path("/projects/sandbox/backtest/data/parts")
d = pd.concat([pd.read_pickle(PARTS / f) for f in os.listdir(PARTS)])
for col in ["open", "high", "low", "close", "volume", "amount", "turn", "pctChg"]:
    d[col] = pd.to_numeric(d[col], errors="coerce")
d = d[(d.tradestatus.astype(int) == 1) & (d.volume > 0)].copy()
d["date"] = pd.to_datetime(d["date"])
# baostock sh.600000 -> TSP 600000.SH
d["symbol"] = d["code"].str[3:] + "." + d["code"].str[:2].str.upper()
st = set(zip(d.loc[d.isST.astype(int) == 1, "symbol"], d.loc[d.isST.astype(int) == 1, "date"]))
d = d.sort_values(["date", "symbol"])  # 故意打乱为按日期排序，检验策略内部排序
panel = pl.DataFrame({
    "symbol": d["symbol"].tolist(),
    "date": pl.Series(d["date"].dt.date.tolist(), dtype=pl.Date),
    **{c: d[c].to_numpy() for c in ["open", "high", "low", "close", "volume", "amount"]},
    "turnover_rate": d["turn"].to_numpy(),
})

spec = importlib.util.spec_from_file_location("a", "/projects/sandbox/tsp_lab/strategies/custom_bottom_shadow_fanbao.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
hits = mod.filter_history(panel, {"amt20_min": 0})
hits = hits.filter(pl.col("date") >= pl.date(2024, 1, 1))
ps = {(s, pd.Timestamp(t)) for s, t in zip(hits["symbol"].to_list(), hits["date"].to_list())} - st

ta = pd.read_csv("/projects/sandbox/backtest/output/trades_A.csv", parse_dates=["signal_date"])
ta = ta[ta.score >= 55]
ns = {(c[3:] + "." + c[:2].upper(), t) for c, t in zip(ta.code, ta.signal_date)}
print(f"polars={pl.__version__} filter_history命中(非ST,2024+)={len(ps)} numpy={len(ns)} 共同={len(ps & ns)}")
print("仅filter_history:", sorted(ps - ns)[:5])
print("仅numpy:", sorted(ns - ps)[:5])

# 截面选股场景：只给最近260个交易日窗口，取最后一天
dates = panel["date"].unique().sort()
win = panel.filter(pl.col("date") >= dates[-260])
last = mod.filter_history(win, {"amt20_min": 0}).filter(pl.col("date") == dates[-1])
print("选股窗口(260日)最后一天命中:", last.select("symbol", "date").to_dicts())

# 用已知信号日做截面选股复现
known = sorted(ps & ns)[-1]
kd = known[1].date()
win2 = panel.filter(pl.col("date") <= kd).filter(pl.col("date") >= dates.filter(dates <= kd)[-260])
got = mod.filter_history(win2, {"amt20_min": 0}).filter(pl.col("date") == kd)["symbol"].to_list()
print("已知信号", known, "截面选股复现:", known[0] in got, "当日命中", got)
