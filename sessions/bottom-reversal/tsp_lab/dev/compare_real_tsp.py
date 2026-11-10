"""对比 TSP 真实引擎（tsp_real_trades.json）与 tsp_lab 的 TSP 口径，逐笔核对"""
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tsp_lab as L  # noqa: E402

real = pd.DataFrame(json.loads((HERE / "tsp_real_trades.json").read_text(encoding="utf-8")))
real["entry_date"] = pd.to_datetime(real.entry_date).dt.date

mod, defaults = L.load_strategy(L.STRATEGY_A)
mod.META["basic_filter"] = {"enabled": False}
s, e = date(2024, 1, 1), date(2026, 12, 31)
panel = L.load_panel(HERE / "fake_data", s, e, mod.LOOKBACK_DAYS)
mkt = L.Market(panel, {})
hits = L.run_signals(mod, panel, {**defaults, "amt20_min": 0}, s, e)
tr = L.simulate(mkt, hits, L.EXEC_TSP, {"commission": 0, "stamp": 0, "slippage_bps": 0})
lab = pd.DataFrame([t for t in tr if t["status"] == "成交"])

k = ["symbol", "entry_date"]
m = lab.merge(real, on=k, how="outer", indicator=True, suffixes=("_lab", "_tsp"))
both = m[m._merge == "both"]
print(f"TSP真实引擎 {len(real)} 笔，tsp_lab {len(lab)} 笔，共同 {len(both)} 笔")
print("仅TSP:", m[m._merge == "right_only"][k].head(8).values.tolist())
print("仅lab:", m[m._merge == "left_only"][k + ["status"]].head(8).values.tolist())
both = both.assign(d_entry=(both.entry - both.entry_price).abs(), d_exit=(both.exit - both.exit_price).abs(),
                   d_ret=(both.ret - both.pnl_pct).abs())
print("买入价一致率", (both.d_entry < 1e-3).mean(), "卖出价一致率", (both.d_exit < 1e-3).mean(),
      "收益最大差", round(both.d_ret.max(), 6))
bad = both[(both.d_entry >= 1e-3) | (both.d_exit >= 1e-3)]
print(bad[k + ["entry", "entry_price", "exit", "exit_price", "exit_reason_lab", "exit_reason_tsp",
               "exit_date_lab", "exit_date_tsp"]].head(10).to_string())
