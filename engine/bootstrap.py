"""首次部署：拉取全部池内股票、持仓股和沪深300的日线与市值（约2–5分钟）。"""
import json
import os

import data
import lab

codes = set()
for s in "ABCDE":
    codes |= {r["code"] for r in lab.load_pool(s)}
    codes |= {h["code"] for h in json.load(open(os.path.join(lab.ROOT, "studios", s, "ledger.json")))["positions"]}
print("拉取", len(codes), "只股票 + 沪深300 …")
failed = data.update_all(codes, sleep=0.1)
print("完成；失败：", failed or "无")
