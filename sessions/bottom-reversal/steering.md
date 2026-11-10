# 长期规则：底部反包 / TSP 回测相关工作

- 用户的选股平台是 tick-stock-panel（TSP）v0.3.34 Windows 桌面版，数据目录 `H:\TSP\data`；TickFlow 免费档，无分钟 K、无复权因子。
- 写 TSP 策略时：需要多日历史的逻辑必须用 `EXECUTION_BACKEND = "python_history_legacy"` + `filter_history()` + `LOOKBACK_DAYS`，并声明 `REQUIRED_FEATURES`；不要用只拿到当日截面的 `filter()`。保存前用 TSP 的 `AIStrategyGenerator.validate_code` 校验。
- TSP 回测页单位：佣金=万分之、印花税=千分之（仅卖出）、滑点=bp（双边）。
- 回测结论必须按年份拆开看；只在一年赚钱的规则不能算有效。参数是在历史上挑出来的，必须用之后的新数据（样本外）验证后才建议放大资金。
- 未复权数据要剔除疑似除权交易（tsp_lab 默认开启），并在结论中说明误差。
- 新的回测需求优先改 `tsp_lab/tsp_lab.py` 开头的 `RUNS` / `GRIDS` / `EXEC_*`，交易规则保持与 TSP 源码一致（T+1、涨跌停、盘中止损价、`hold`=买入后第 N 个交易日收盘）。
- 给用户的工具要能双击运行（`run.bat`），并打成单个 zip 下载。
