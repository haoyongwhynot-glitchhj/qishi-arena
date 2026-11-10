"""自测：tsp_lab 撮合结果 与 /projects/sandbox/backtest/backtest.py 的 numpy 回测逐笔对比。

两边规则：次日开盘买、高开>3%放弃、一字涨停买不进、反包日最低止损、持有5日到期收盘卖、一字跌停顺延。
为了可比：关闭基础过滤、20日均额门槛=0、手续费=0。
"""
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import polars as pl

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tsp_lab as L  # noqa: E402

mod, defaults = L.load_strategy(L.STRATEGY_A)
mod.META["basic_filter"] = {"enabled": False}
start, end = date(2024, 1, 1), date(2026, 12, 31)
panel = L.load_panel(HERE / "fake_data", start, end, mod.LOOKBACK_DAYS)
mkt = L.Market(panel, {})
hits = L.run_signals(mod, panel, {**defaults, "amt20_min": 0}, start, end)
ex = {**L.EXEC_FANBAO, "stop": "signal_low", "hold": 4}  # numpy 版持有5日含买入日 = TSP口径 hold=4
trades = L.simulate(mkt, hits, ex, {"commission": 0, "stamp": 0, "slippage_bps": 0})
lab = pd.DataFrame([t for t in trades if t["status"] == "成交"])
lab["code"] = lab.symbol.str[-2:].str.lower() + "." + lab.symbol.str[:6]
lab["signal_date"] = pd.to_datetime(lab.signal_date)

ref = pd.read_csv("/projects/sandbox/backtest/output/trades_A.csv", parse_dates=["signal_date"])
ref = ref[(ref.score >= 55) & (ref.status == "成交")]
m = lab.merge(ref, on=["code", "signal_date"], suffixes=("_lab", "_ref"))
print(f"tsp_lab 成交 {len(lab)} 笔，numpy 成交 {len(ref)} 笔，两边共同 {len(m)} 笔")
m["entry_ok"] = (m.entry_lab - m.entry_ref).abs() < 1e-3
m["exit_ok"] = (m.exit_lab - m.exit_ref).abs() < 1e-3
m["ret_diff"] = (m.ret_lab - (m.ret_ref + 0.002)).abs()
print("买入价一致:", m.entry_ok.mean(), " 卖出价一致:", m.exit_ok.mean(),
      " 收益最大差:", round(m.ret_diff.max(), 6))
bad = m[~(m.entry_ok & m.exit_ok)]
if len(bad):
    print(bad[["code", "signal_date", "entry_lab", "entry_ref", "exit_lab", "exit_ref",
               "exit_reason_lab", "exit_reason_ref"]].head(10).to_string())
only_lab = set(zip(lab.code, lab.signal_date)) - set(zip(ref.code, ref.signal_date))
only_ref = set(zip(ref.code, ref.signal_date)) - set(zip(lab.code, lab.signal_date))
print("仅 tsp_lab:", sorted(only_lab)[:5])
print("仅 numpy:", sorted(only_ref)[:5])
