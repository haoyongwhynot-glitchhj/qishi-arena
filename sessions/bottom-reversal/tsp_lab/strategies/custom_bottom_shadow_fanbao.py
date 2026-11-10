"""底部横盘·长上影试盘·1~3日内阳线反包（V3 评分版）"""
import polars as pl

META = {
    "id": "custom_bottom_shadow_fanbao",
    "name": "底部横盘长上影反包V3",
    "description": "底部长期横盘，近期有资金异动，1~3日前出现长上影试盘K，今日阳线收盘站上上影最高价",
    "tags": ["自定义", "底部", "反包", "仙人指路"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 200,
        "market_cap_min": None,
        "float_cap_min": 3000000000, "float_cap_max": 30000000000,
        "amount_min": 50000000, "amount_max": None,
        "turnover_min": None, "turnover_max": None,
        "exclude_st": True, "exclude_new_days": 200,
        "boards": ["沪主板", "深主板", "创业板", "科创板"],
    },
    "params": [
        {"id": "sc_min", "label": "评分门槛", "type": "int",
         "default": 55, "min": 0, "max": 100, "step": 5, "group": "评分"},
        {"id": "win", "label": "上影后几日内反包", "type": "int",
         "default": 3, "min": 1, "max": 3, "step": 1, "group": "形态"},
        {"id": "fb_mode", "label": "反包标准", "type": "select", "default": 1,
         "options": [{"label": "收盘站上上影最高价", "value": 1},
                     {"label": "突破上影高点且收盘吞没上影70%", "value": 2}],
         "group": "形态"},
        {"id": "box_max", "label": "横盘箱体最大振幅%", "type": "float",
         "default": 28, "min": 15, "max": 45, "step": 1, "group": "形态"},
        {"id": "amt20_min", "label": "20日均成交额下限(元)", "type": "float",
         "default": 150000000, "min": 0, "max": 1000000000, "step": 50000000, "group": "流动性"},
    ],
    "scoring": {
        "change_pct": 0.5, "vol_ratio_5d": 0.5,
    },
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "python_history_legacy"
LOOKBACK_DAYS = 260          # 选股时加载的历史窗口
WARMUP_BARS = 190            # 至少190根K线后才可能出信号（180日底部位置 + 偏移）
REQUIRED_FEATURES = {"amount"}
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.07
MAX_HOLD_DAYS = 5

RULES = """
1. 底部：上影日前一天收盘位于180日高低区间50%以下，今日不超过60%，180日区间振幅不低于15%
2. 横盘与资金（评分项）：55日收盘箱体振幅不超过28%（20cm放宽到35%），60日线走平；上影日前有堆量或量价异动，红肥绿瘦，无放量大阴
3. 试盘：1~3日前出现长上影，上影长度不低于2%且不低于常态波幅的一半，占当日振幅40%以上，放量但不是天量
4. 反包：今日实体阳线，收盘站上上影日最高价，上影日后最低价不破上影日最低，且是上影后首次收盘站上
5. 防追高：5日涨幅主板不超过20%、20cm不超过32%，收盘在20日线上；20日均成交额不低于1.5亿
6. 评分满分100，默认55分以上入选
"""

M, N = 60, 180


def _mx(*xs):
    return pl.max_horizontal(*xs)


def _hit_expr(sc_min, win, fb_mode, box_max, amt20_min):
    o, h, l, c, v, amt = (pl.col(x) for x in ["open", "high", "low", "close", "volume", "amount"])

    digits = pl.col("symbol").cast(pl.Utf8).str.extract(r"(\d{6})")
    is20 = digits.str.starts_with("30") | digits.str.starts_with("68")
    K = pl.when(is20).then(1.6).otherwise(1.0)
    KS = pl.when(is20).then(1.5).otherwise(1.0)
    BOX_LIM = pl.when(is20).then(box_max * 1.25).otherwise(box_max)

    rc1 = c.shift(1)
    tr = _mx(h - l, (h - rc1).abs(), (l - rc1).abs())
    vb = _mx(v.rolling_mean(40).shift(5), pl.lit(1.0))
    atrp = tr.rolling_mean(20).shift(1) / _mx(rc1, pl.lit(0.01)) * 100
    ups = h - _mx(o, c)
    rng = _mx(h - l, pl.lit(0.01))

    # 逐日序列：长上影试盘K、量价异动、放量大阴
    SD = ((ups / _mx(rc1, pl.lit(0.01)) * 100 >= _mx(pl.lit(2.0), atrp * 0.5))
          & (ups / rng >= 0.40)
          & (c >= rc1 * (1 - 0.03 * K))
          & (v >= vb * 1.1) & (v <= vb * 6)).fill_null(False)
    YD_A = (v >= vb * 1.6) & (c > o) & (c / rc1 >= 1 + 0.025 * K) & ((c - l) / rng >= 0.55)
    YD_B = (v >= vb * 1.5) & (h / rc1 >= 1 + 0.03 * K) & (c >= rc1 * 0.99)
    LJYD = (YD_A | YD_B).fill_null(False).cast(pl.Int32)
    CH_BAD = ((c < rc1 * (1 - 0.05 * K)) & (v >= vb * 1.5)).fill_null(False).cast(pl.Int32)
    up_v = pl.when((c > o) | ((c == o) & (c >= rc1))).then(v).otherwise(0)
    dn_v = pl.when((c < o) | ((c == o) & (c < rc1))).then(v).otherwise(0)
    ma60 = c.rolling_mean(60)
    body = (c - o) / rng

    JC = ((pl.int_range(pl.len()) >= N + 9)
          & (amt.rolling_mean(5) > 3e7)
          & (amt.rolling_mean(20) >= amt20_min)
          & (v > 0))
    NO_CHASE = (c / c.shift(5) <= 1 + 0.2 * K) & (c > c.rolling_mean(20))

    def pts(cond, score):
        return pl.when(cond.fill_null(False)).then(score).otherwise(0)

    hit = pl.lit(False)
    best_score = pl.lit(None, dtype=pl.Float64)
    shadow_low = pl.lit(None, dtype=pl.Float64)
    for d in range(1, win + 1):
        b = d + 1
        # 最近一根上影K恰好在 d 天前
        is_d = SD.shift(d).fill_null(False)
        for k in range(1, d):
            is_d = is_d & ~SD.shift(k).fill_null(False)

        sh, sl = h.shift(d), l.shift(d)
        sh_top = _mx(o, c).shift(d)
        sh_up = sh - sh_top
        nobreak = l.rolling_min(d) >= sl * 0.99
        first = c.rolling_max(d).shift(1) < sh

        hh, ll = h.rolling_max(N).shift(b), l.rolling_min(N).shift(b)
        rg = _mx(hh - ll, pl.lit(0.01))
        cb = c.shift(b)
        is_bot = ((cb - ll) / rg * 100 <= 50) & ((c - ll) / rg * 100 <= 60) & (hh / _mx(ll, pl.lit(0.01)) >= 1.15)

        h55 = c.rolling_max(M - 5).shift(b + 3)
        l55 = c.rolling_min(M - 5).shift(b + 3)
        amp_box = (h55 / _mx(l55, pl.lit(0.01)) - 1) * 100
        box_ok = ((amp_box <= BOX_LIM)
                  & ((cb / _mx(l.rolling_min(M).shift(b), pl.lit(0.01)) - 1) * 100 <= BOX_LIM)
                  & (cb <= h55 * 1.08))
        flat60 = (ma60.shift(b) / _mx(ma60.shift(b + 20), pl.lit(0.01)) - 1).abs() * 100 <= 10

        v_base = _mx(v.rolling_mean(40).shift(b + 3), pl.lit(1.0))
        yy = up_v.rolling_sum(10).shift(b) / _mx(dn_v.rolling_sum(10).shift(b), pl.lit(1.0))
        dui_v = v.rolling_mean(5).shift(b) / v_base
        nyd = LJYD.rolling_sum(20).shift(b)
        nbad = CH_BAD.rolling_sum(10).shift(b)

        t_yang = ((c > o) & (c > rc1) & ~((o == h) & (h == l))
                  & (body >= 0.40) & ((h - c) / c * 100 <= 2.5 * KS) & (v >= v_base * 1.1))
        fb = (c >= sh) if fb_mode == 1 else ((h >= sh) & (c >= sh_top + sh_up * 0.70))

        score = (pl.when(box_ok.fill_null(False)).then(20)
                 .when((amp_box <= BOX_LIM * 1.3).fill_null(False)).then(10).otherwise(0)
                 + pts(flat60, 10)
                 + pl.when(((dui_v >= 1.2) | (nyd >= 2)).fill_null(False)).then(15)
                   .when(((dui_v >= 1.05) | (nyd >= 1)).fill_null(False)).then(8).otherwise(0)
                 + pl.when((yy >= 1.3).fill_null(False)).then(10)
                   .when((yy >= 1.1).fill_null(False)).then(5).otherwise(0)
                 + pts(nbad == 0, 5)
                 + pts(sh >= h55 * 0.97, 10)
                 + pts(c > h55, 10)
                 + pl.when((v >= v_base * 1.6).fill_null(False)).then(10)
                   .when((v >= v_base * 1.2).fill_null(False)).then(5).otherwise(0)
                 + pts((body >= 0.6) & ((h - c) / c * 100 <= 1.5 * KS), 10))

        hard_d = (is_d & is_bot & nobreak & first & t_yang & fb & (score >= sc_min)).fill_null(False)
        hit = hit | hard_d
        # 命中时记录评分与上影日最低价（供自建回测按价位止损；TSP 只取 symbol/date，不受影响）
        best_score = pl.when(hard_d).then(score.cast(pl.Float64)).otherwise(best_score)
        shadow_low = pl.when(hard_d).then(sl).otherwise(shadow_low)

    hit = (JC & NO_CHASE).fill_null(False) & hit
    return hit.over("symbol"), best_score.over("symbol"), shadow_low.over("symbol")


def filter_history(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    if df.is_empty() or "date" not in df.columns:
        return df
    hit, score, shadow_low = _hit_expr(
        sc_min=float(params.get("sc_min", 55)),
        win=max(1, min(3, int(params.get("win", 3)))),
        fb_mode=int(params.get("fb_mode", 1)),
        box_max=float(params.get("box_max", 28)),
        amt20_min=float(params.get("amt20_min", 1.5e8)),
    )
    hist = df.sort(["symbol", "date"]).with_columns(
        hit.alias("fb_hit"), score.alias("fb_score"), shadow_low.alias("fb_shadow_low"),
    )
    return hist.filter(pl.col("fb_hit")).drop("fb_hit")
