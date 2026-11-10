import sys
from datetime import date
from pathlib import Path
import polars as pl
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tsp_lab as L
mod, d = L.load_strategy(L.STRATEGY_B)
mod.META["basic_filter"] = {"enabled": False}
s, e = date(2024, 1, 1), date(2026, 12, 31)
panel = L.load_panel(Path(__file__).resolve().parent / "fake_data", s, e, mod.LOOKBACK_DAYS)
mkt = L.Market(panel, {})
hits = L.run_signals(mod, panel, d, s, e)
for mode in ("break", "board"):
    ex = {**L.EXEC_LEADER, "leader_mode": mode, "gap_main": (-1, 1), "gap_20cm": (-1, 1)}
    tr = L.simulate(mkt, hits, ex, {"commission": 0, "stamp": 0, "slippage_bps": 0})
    print(mode, [ (t["symbol"], str(t["signal_date"]), t["status"], t.get("entry"), t.get("exit"), t.get("exit_reason"), t.get("hold_days")) for t in tr])
t = [x for x in tr if x["status"] == "成交"]
sym, sd = (t[0]["symbol"], t[0]["signal_date"]) if t else (hits["symbol"][0], hits["date"][0])
print(panel.filter((pl.col("symbol") == sym) & (pl.col("date") >= sd)).head(5).select("date","open","high","low","close","consecutive_limit_ups","consecutive_limit_downs"))
