"""开发自测用：把 baostock 样本数据转成 TSP 本地数据目录结构（仅用于验证 tsp_lab.py）。

生成：
  fake_data/kline_daily_enriched/date=YYYY-MM-DD/part.parquet
  fake_data/instruments/instruments.parquet
  fake_data/regime_history/part.parquet   （用样本涨跌家数粗略合成，仅测试用）
"""
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

SRC = Path("/projects/sandbox/backtest/data")
OUT = Path(__file__).resolve().parent / "fake_data"

d = pd.concat([pd.read_pickle(SRC / "parts" / f) for f in os.listdir(SRC / "parts")])
for c in ["open", "high", "low", "close", "volume", "amount", "turn", "pctChg"]:
    d[c] = pd.to_numeric(d[c], errors="coerce")
d = d[(d.tradestatus.astype(int) == 1) & (d.volume > 0)].copy()
d["date"] = pd.to_datetime(d["date"]).dt.date
d["symbol"] = d.code.str[3:] + "." + d.code.str[:2].str.upper()
d = d.sort_values(["symbol", "date"])
lim = np.where(d.symbol.str[:3].isin(["300", "301", "302", "688", "689"]), 20, 10)
st = d.isST.astype(int) == 1
lim = np.where(st & (lim == 10), 5, lim)
zt = ((d.pctChg >= lim - 0.25) & (d.close >= d.high * 0.998)).astype(int)
dt = ((d.pctChg <= -lim + 0.25) & (d.close <= d.low * 1.002)).astype(int)


def streak(s):
    return s.groupby((s == 0).cumsum()).cumsum()


d["consecutive_limit_ups"] = zt.groupby(d.symbol).transform(streak)
d["consecutive_limit_downs"] = dt.groupby(d.symbol).transform(streak)

shutil.rmtree(OUT, ignore_errors=True)
base = OUT / "kline_daily_enriched"
panel = pl.DataFrame({
    "symbol": d.symbol.tolist(),
    "date": pl.Series(d.date.tolist(), dtype=pl.Date),
    "open": d.open.to_numpy(), "high": d.high.to_numpy(), "low": d.low.to_numpy(), "close": d.close.to_numpy(),
    "volume": (d.volume / 100).to_numpy(), "amount": d.amount.to_numpy(),
    "raw_close": d.close.to_numpy(), "raw_high": d.high.to_numpy(), "raw_low": d.low.to_numpy(),
    "turnover_rate": d.turn.to_numpy(),
    "consecutive_limit_ups": pl.Series(d.consecutive_limit_ups.to_numpy(), dtype=pl.UInt32),
    "consecutive_limit_downs": pl.Series(d.consecutive_limit_downs.to_numpy(), dtype=pl.UInt32),
})
for part in panel.partition_by("date"):
    ds = part["date"][0].isoformat()
    p = base / f"date={ds}"
    p.mkdir(parents=True, exist_ok=True)
    part.sort("symbol").write_parquet(p / "part.parquet")

names = pd.read_pickle(SRC / "names.pkl")
names["symbol"] = names.code.str[3:] + "." + names.code.str[:2].str.upper()
last = d.groupby("symbol").tail(1)
fs = (last.volume / (last.turn / 100)).replace([np.inf], np.nan)
inst = pl.DataFrame({"symbol": last.symbol.tolist(), "float_shares": fs.to_numpy()}).join(
    pl.DataFrame({"symbol": names.symbol.tolist(), "name": names.code_name.tolist()}), on="symbol", how="left")
(OUT / "instruments").mkdir(parents=True)
inst.write_parquet(OUT / "instruments" / "instruments.parquet")

g = d.groupby("date").agg(up=("pctChg", lambda s: (s > 0).mean()), avg=("pctChg", "mean"))
score = (g.up * 70 + g.avg.clip(-3, 3) * 10).clip(0, 100)
state = pd.cut(score, [-1, 30, 45, 55, 70, 101], labels=["weak", "lean_weak", "range", "lean_strong", "strong"])
(OUT / "regime_history").mkdir(parents=True)
pl.DataFrame({"date": pl.Series(list(g.index), dtype=pl.Date), "state": state.astype(str).tolist(),
              "score": score.round().astype(int).tolist()}).write_parquet(OUT / "regime_history" / "part.parquet")
print("完成", OUT, panel.height, "行", panel["date"].n_unique(), "天")
