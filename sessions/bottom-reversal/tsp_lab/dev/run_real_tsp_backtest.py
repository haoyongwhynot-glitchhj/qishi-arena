"""自测：用 TSP v0.3.34 真实回测引擎跑 fake_data，和 tsp_lab 的 “TSP口径” 逐笔对比。

运行环境：装了 TSP 后端依赖的 Python（cwd 为 TSP backend 目录）。
"""
import json
import shutil
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "fake_tsp_home"
shutil.rmtree(DATA, ignore_errors=True)
shutil.copytree(HERE / "fake_data", DATA)
(DATA / "strategies" / "custom").mkdir(parents=True)
shutil.copy(HERE.parent / "strategies" / "custom_bottom_shadow_fanbao.py", DATA / "strategies" / "custom")

import os  # noqa: E402
os.environ["DATA_DIR"] = str(DATA)

from app.backtest.engine import BacktestEngine  # noqa: E402
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService  # noqa: E402
from app.strategy.engine import StrategyEngine  # noqa: E402
from app.tickflow.repository import DataStore, KlineRepository  # noqa: E402

from app.strategy import custom_signals  # noqa: E402
custom_signals.seed_if_empty(DATA)       # 种入内置信号（含涨跌停）

store = DataStore(DATA)
repo = KlineRepository(store)
eng = StrategyEngine(strategy_dirs=[DATA / "strategies" / "custom"])
svc = StrategyBacktestService(BacktestEngine(repo), eng)
cfg = StrategyBacktestConfig(
    strategy_id="custom_bottom_shadow_fanbao", symbols=None,
    start=date(2024, 1, 1), end=date(2026, 12, 31),
    params={"amt20_min": 0},
    overrides={"basic_filter": {"enabled": False}},
    entry_fill="open_t+1", exit_fill="close_t",
    commission_pct=0.0, stamp_tax_pct=0.0, slippage_bps=0.0,
    max_positions=1000, initial_capital=1e10, mode="position",
)
res = asdict(svc.run(cfg))
if res.get("error"):
    print("TSP 回测报错:", res["error"])
    sys.exit(1)
trades = res.get("trades") or []
out = [{k: t.get(k) for k in ("symbol", "entry_date", "exit_date", "entry_price", "exit_price",
                               "pnl_pct", "exit_reason", "entry_signal_date")} for t in trades]
(HERE / "tsp_real_trades.json").write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
print("TSP 真实引擎成交笔数:", len(out))
print("统计:", {k: res.get("stats", {}).get(k) for k in ("total_trades", "win_rate", "total_return")})
