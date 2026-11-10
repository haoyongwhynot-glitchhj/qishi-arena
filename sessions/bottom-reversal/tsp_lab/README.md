# tsp_lab：TSP 研究回测器（Windows）

直接读取 TSP（tick-stock-panel v0.3.34）本地数据，调用和 TSP 里同一份策略文件，按 TSP 做不到的交易规则回测。

## 目录

```
tsp_lab/
  tsp_lab.py                 主程序（配置都在文件开头）
  strategies/                策略文件，和导入 TSP 的是同一份
    custom_bottom_shadow_fanbao.py     策略A 底部横盘长上影反包
    custom_leader_first_divergence.py  策略B 龙头首次分歧
  dev/                       开发自测脚本（依赖开发环境路径，使用者不用管）
  output/                    运行后生成：交易明细、分组统计、网格结果（CSV，Excel 可直接打开）
```

## 安装（只需一次）

1. 安装 Python 3.11 或更新版本（python.org 下载，安装时勾选 “Add python.exe to PATH”）。
2. 打开 PowerShell，执行：
   ```
   pip install polars numpy
   ```

## 找到 TSP 数据目录

Windows 桌面版的数据在 **TSP 安装目录下的 `data` 文件夹**。确认方法：里面有 `kline_daily_enriched` 文件夹，再往里是很多 `date=2025-01-02` 这样的子文件夹。

建议在 TSP 不做数据同步的时候运行本工具（同步中分区文件会被改写）。

## 最简单的用法：双击 run.bat

解压后双击 `run.bat`，按菜单输入数字：1 检查数据、2 快速试跑、3 全市场跑全部方案、4/5 参数网格。

- 第一次运行会自动安装 polars、numpy。
- TSP 数据目录自动查找：先看 `D:\TSP\data`、`%LOCALAPPDATA%\Programs\TSP\data`（TSP 安装程序的两个默认位置），找不到再扫描各磁盘前 3 层文件夹。找到后记在 `data_dir.txt`，下次直接用。
- 本地数据不够早时，回测起点自动往后调。
- 跑完自动打开 `output` 文件夹；屏幕输出同时存为 `output/log_时间.txt`。

## 命令行用法

在 `tsp_lab` 文件夹里打开 PowerShell（文件夹空白处 Shift+右键 →“在此处打开 PowerShell 窗口”），把下面的路径换成你的：

```
# 1. 先检查数据：日期范围、个股维表、市场环境数据是否齐全
python tsp_lab.py --data "D:\TSP\data" --check

# 2. 随机抽 500 只股票快速试跑，确认没问题
python tsp_lab.py --data "D:\TSP\data" --sample 500

# 3. 全市场跑全部方案
python tsp_lab.py --data "D:\TSP\data"

# 只跑某一个方案 / 改区间
python tsp_lab.py --data "D:\TSP\data" --run A-默认 --start 2023-01-01 --end 2026-11-06

# 参数网格（按年度稳健分排名）
python tsp_lab.py --data "D:\TSP\data" --grid A
python tsp_lab.py --data "D:\TSP\data" --grid B
```

全市场 + 4 年数据大约需要 3~6 GB 内存。内存不够时先缩短区间，或者用 `--sample`。

中文显示乱码时，先在 PowerShell 执行 `chcp 65001` 再运行。

## 内置方案（RUNS）

| 方案 | 说明 |
|---|---|
| A-TSP口径 | 次日开盘买、买入价 -7% 止损、持有 5 天。和 TSP 自带回测同口径，用来核对两边结果 |
| A-默认 | 次日开盘买，高开超过 3% 不买；止损在反包日最低价；持有 5 天 |
| A-评分70+环境 | 评分≥70，只在信号日市场环境为强势/偏强/震荡时开仓 |
| B-突破买 | 分歧日次日竞价高开（主板 2~6%，20cm 3~10%），盘中突破分歧日最高价买入；止损分歧日最低；次日低开 3% 卖；持有 3 天 |
| B-排板买 | 同上，但只在次日收盘封涨停时按涨停价买（假设排得进，结果偏乐观） |

在 `tsp_lab.py` 开头的 `RUNS`、`EXEC_*`、`GRIDS` 里增删改方案。

## 交易规则（和 TSP 一致的部分）

- T+1：买入当天不卖。
- `hold` 和 TSP 的 `MAX_HOLD_DAYS` 同义：买入后第 hold 个交易日收盘卖。
- 盘中止损：开盘就低于止损价按开盘价卖，否则最低价触及止损价按止损价卖。
- 一字涨停买不进；按信号日收盘买时，收盘封涨停也买不进。
- 一字跌停卖不出；到期收盘封跌停卖不出；都顺延到下一个能卖的交易日开盘。
- 同一只股票持仓期间的新信号忽略。
- 涨跌停判断直接用 TSP 算好的 `consecutive_limit_ups` / `consecutive_limit_downs`。
- 基础过滤（价格、流通市值、成交额、换手率、排除 ST、板块）照搬 TSP 的规则。流通市值 = 前复权收盘价 × 当前流通股本，和 TSP 一样是近似值。
- 费用：佣金万 2（双边）、印花税 0.05%（卖出）、滑点 5bp（双边），在 `CONFIG["fees"]` 改。

## TSP 做不到、本工具补上的部分

- 止损设在价位：`signal_low`（信号日最低价）、`shadow_low`（上影日最低价）。
- 次日高开过滤：`gap_max`、`gap_min`。
- 龙头次日确认买入：`leader_confirm`。
- 分组统计：按年份、信号日市场环境、流通市值、评分、板块、卖出原因。
- 参数网格按 TSP 的稳健分排名：每年平均每笔收益，取 0.6×年度中位 + 0.4×年度最差，每年至少 10 笔才计入。

## 输出文件（output/）

- `trades_<方案>.csv`：每个信号一行。status 列写明成交，或者没成交的原因（高开过多放弃、一字涨停买不进、环境过滤、持仓中忽略等）。
- `summary_<方案>.csv`：各种分组的统计。
- `runs_summary.csv`：方案汇总，含每年的平均收益和笔数。
- `grid_A.csv` / `grid_B.csv`：网格全部结果，按稳健分排序。

组合统计是简化的：资金等分成 `max_positions` 份，有空位才买，同一天按评分从高到低；只按平仓时点算净值，所以最大回撤是近似值。

## 已知局限

- 没有分钟数据，炸板时间、竞价金额、分时均线这类条件测不了。
- 市场环境只用 TSP 的 5 档 state（`regime_history`）；按信号日的状态过滤，信号日收盘后已知，没有用到未来数据。
- 停牌、退市股：只要 TSP 本地有数据就会参与；TSP 没同步的已退市股票不在样本内，结果会偏乐观。
- 策略文件需要是 TSP 的 `filter_history()` 写法。策略用到 TSP 运行时才算的指标列时，本工具只补了常用的几列（ma5/10/20/30/60、change_pct、vol_ma5/10、vol_ratio_5d、momentum_*、high_60d/low_60d），其他列会给出提示。

## 已做过的验证（开发环境）

- 两个策略文件过 TSP v0.3.34 的保存校验（`validate_code`），能被 TSP 策略引擎加载。
- 用 TSP v0.3.34 真实回测引擎和本工具的 “TSP口径” 跑同一份测试数据（182 只股票，2024-01~2026-11）：TSP 175 笔，本工具 174 笔，174 笔买入价全部一致，171 笔卖出价一致。不一致的 3 笔是测试数据里涨跌停标记和 TSP 自己算的有差别（真实数据里两边用的是同一列，不会有这个问题），少的 1 笔是这个差别引起的持仓日期连锁变化。
- 策略A 的信号和之前独立写的 numpy 版逐个一致。
