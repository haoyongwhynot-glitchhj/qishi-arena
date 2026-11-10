"""用 TSP v0.3.34 源码校验策略文件：安全校验 + 完整代码校验 + 引擎加载 + 回测特征计划"""
import sys
from pathlib import Path

SRC = Path("/projects/sandbox/tsp_lab/strategies")

from app.strategy.ai_generator import AIStrategyGenerator as G  # noqa: E402
from app.strategy.engine import StrategyEngine  # noqa: E402
from app.backtest.strategy import StrategyBacktestService  # noqa: E402,F401

g = G.__new__(G)
g._execution_backend = "polars_expr"
for f in sorted(SRC.glob("custom_*.py")):
    r = g.validate_code(f.read_text(encoding="utf-8"))
    print(f"[validate_code] {f.name}: valid={r['valid']} error={r['error']}")

eng = StrategyEngine(strategy_dirs=[SRC])
for sid in ["custom_bottom_shadow_fanbao", "custom_leader_first_divergence"]:
    s = eng.get(sid)
    print(f"[load] {sid}: backend={s.execution_backend} lookback={s.lookback_days} "
          f"features={sorted(s.required_features)} stop={s.stop_loss} hold={s.max_hold_days} "
          f"params={eng.resolve_params(s, None, None)}")
sys.stdout.flush()
