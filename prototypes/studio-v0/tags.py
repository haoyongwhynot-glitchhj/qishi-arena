"""人工标签（主观判断，可直接改）。
theme: 与当前主线（商业航天 + 卫星通信）的关联度 0~1
soe:   国资背景 1 / 部分 0.5 / 民营 0
pe:    截图里的市盈率(TTM)，None 表示亏损
回测时这些标签会被置为中性，避免用今天的题材判断去评过去（未来函数）。
"""
TAGS = {
    "000026": dict(name="飞亚达",     theme=0.8, soe=1,   pe=86.72),
    "002222": dict(name="福晶科技",   theme=0.4, soe=0.5, pe=56.81),
    "002583": dict(name="海能达",     theme=0.7, soe=0,   pe=None),
    "002600": dict(name="领益智造",   theme=0.2, soe=0,   pe=41.99),
    "002708": dict(name="光洋股份",   theme=0.3, soe=0,   pe=117.33),
    "002931": dict(name="锋龙股份",   theme=0.1, soe=0,   pe=442.33),
    "300418": dict(name="昆仑万维",   theme=0.1, soe=0,   pe=None),
    "300638": dict(name="广和通",     theme=0.6, soe=0,   pe=87.23),
    "600498": dict(name="烽火通信",   theme=0.5, soe=1,   pe=154.16),
    "600776": dict(name="东方通信",   theme=0.8, soe=1,   pe=54.60),
    "600879": dict(name="航天电子",   theme=1.0, soe=1,   pe=783.25),
    "601689": dict(name="拓普集团",   theme=0.1, soe=0,   pe=31.40),
    "601801": dict(name="皖新传媒",   theme=0.0, soe=1,   pe=24.39),
    "603112": dict(name="XD华翔股",   theme=0.1, soe=0,   pe=17.03),
    "603626": dict(name="科森科技",   theme=0.2, soe=0,   pe=None),
    "688258": dict(name="卓易信息",   theme=0.1, soe=0,   pe=65.10),
    "688802": dict(name="沐曦股份-U", theme=0.1, soe=0,   pe=1010.42),
}
SAMPLE = {"002149": dict(name="西部材料", theme=0.9, soe=1, pe=60.0)}  # 样板股，仅用于校验；pe未从截图读到，填中性值
NEUTRAL = dict(theme=0.5, soe=0.5, pe=60.0)
