"""龙头首次分歧观察池：前面至少2个一致板，今日首次出现换手板/烂板或断板承接"""
import polars as pl

META = {
    "id": "custom_leader_first_divergence",
    "name": "龙头首次分歧观察池",
    "description": "连续一致板后的首次分歧日，次日竞价高开并突破分歧日最高价时确认转一致",
    "tags": ["自定义", "龙头", "连板", "分歧转一致"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 200,
        "market_cap_min": None,
        "float_cap_min": 3000000000, "float_cap_max": 30000000000,
        "amount_min": 100000000, "amount_max": None,
        "turnover_min": None, "turnover_max": None,
        "exclude_st": True, "exclude_new_days": 60,
        "boards": ["沪主板", "深主板", "创业板", "科创板"],
    },
    "params": [
        {"id": "hs_max_main", "label": "主板换手率上限%", "type": "float",
         "default": 30, "min": 10, "max": 60, "step": 5},
        {"id": "hs_max_20cm", "label": "20cm换手率上限%", "type": "float",
         "default": 45, "min": 10, "max": 80, "step": 5},
    ],
    "scoring": {
        "change_pct": 0.5, "vol_ratio_5d": 0.5,
    },
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "python_history_legacy"
LOOKBACK_DAYS = 70
WARMUP_BARS = 61             # 至少61根K线后才可能出信号（排除新股）
REQUIRED_FEATURES = {"turnover_rate", "consecutive_limit_ups"}
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.07
MAX_HOLD_DAYS = 3

RULES = """
1. 前面至少2个一致板（涨停且振幅不超过涨停幅度的40%），再往前的连板中不能有分歧板
2. 今日分歧：涨停但振幅不低于涨停幅度的60%或换手率是昨日1.8倍以上；或者断板但跌幅不超过涨停幅度的30%、振幅大、收在当日振幅40%以上
3. 换手率主板不超过30%，20cm不超过45%；今日不是一字板
4. 本策略只选出分歧日观察池。次日确认：竞价高开主板2~6%、20cm 3~10%，开盘后突破分歧日最高价买入
5. 需人工确认：是否为题材龙头、板块是否有跟风涨停、有无监管异动公告
"""


def filter_history(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    if df.is_empty() or "date" not in df.columns:
        return df
    hs_main = float(params.get("hs_max_main", 30))
    hs_20 = float(params.get("hs_max_20cm", 45))

    o, h, l, c = (pl.col(x) for x in ["open", "high", "low", "close"])
    digits = pl.col("symbol").cast(pl.Utf8).str.extract(r"(\d{6})")
    is20 = digits.str.starts_with("30") | digits.str.starts_with("68")
    ZF = pl.when(is20).then(0.2).otherwise(0.1)

    rc1 = c.shift(1)
    zfu = (h - l) / pl.max_horizontal(rc1, pl.lit(0.01))
    rng = pl.max_horizontal(h - l, pl.lit(0.01))
    lu = pl.col("consecutive_limit_ups").fill_null(0)
    ZT = lu >= 1                                  # 平台按交易所规则判定的涨停
    YZ = (ZT & (zfu <= ZF * 0.4)).fill_null(False)

    hs = pl.col("turnover_rate")
    hs_jump = (hs >= hs.shift(1) * 1.8).fill_null(False)
    hs_ok = (hs <= pl.when(is20).then(hs_20).otherwise(hs_main)).fill_null(False)

    FQ_ZT = ZT & ((zfu >= ZF * 0.6).fill_null(False) | hs_jump)
    FQ_DB = (~ZT & (c >= rc1 * (1 - 0.3 * ZF)) & (zfu >= ZF * 0.6) & ((c - l) / rng >= 0.4)).fill_null(False)
    FQ = FQ_ZT | FQ_DB

    # 首次分歧：截至昨日连板≥2，且这段连板全部是一致板
    streak = lu.shift(1).fill_null(0)
    prev_ok = streak >= 2
    for k in range(1, 11):
        prev_ok = prev_ok & ((streak < k) | YZ.shift(k).fill_null(False))

    not_yz_today = ~(ZT & (h == l))
    expr = ((pl.int_range(pl.len()) >= 60) & prev_ok & FQ & hs_ok & not_yz_today).fill_null(False)
    return df.sort(["symbol", "date"]).filter(expr.over("symbol"))
