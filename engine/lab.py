"""加载股票池与特征；运行生产参数与挑战参数的对比（影子测试）。"""
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data
import features
import sim

ROOT = data.ROOT


def load_pool(studio):
    with open(os.path.join(ROOT, "pools", f"{studio}.csv")) as f:
        return list(csv.DictReader(f))


def load_genome(studio):
    with open(os.path.join(ROOT, "studios", studio, "genome.json")) as f:
        return json.load(f)


_FEAT_CACHE = {}


def universe(studio, extra=()):
    """extra: 已不在池但仍持仓的股票，只用于退出检查，不参与选股。"""
    pool = load_pool(studio)
    have = {r["code"] for r in pool}
    pool = pool + [dict(code=c, name="", batch="持仓", entry_date="99999999") for c in extra if c not in have]
    quotes = data.load_quotes()
    idx = {b["date"]: b["close"] for b in data.load_bars(data.HS300, index=True)}
    feats = {}
    for r in pool:
        c = r["code"]
        if c not in _FEAT_CACHE:
            q = quotes.get(c, {})
            _FEAT_CACHE[c] = features.build(c, data.load_bars(c), q.get("name") or r["name"], q.get("mcap"), idx)
        feats[c] = _FEAT_CACHE[c]
    cal = sorted(idx)
    return sim.Universe(feats, pool, cal)


def run(studio, params, start, end, mode):
    u = universe(studio)
    res = sim.run(u, studio, params, start, end, mode)
    return res, sim.metrics(res)
