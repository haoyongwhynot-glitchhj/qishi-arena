"""每个交易日收盘后（15:30 以后）运行一次：拉行情 → 结算当日成交 → 生成明日计划。
用法：python3 run_day.py
"""
import csv
import data
import lab
import settle
import daily

if __name__ == "__main__":
    codes = set()
    for s in "ABCDE":
        codes |= {r["code"] for r in lab.load_pool(s)}
        codes |= {h["code"] for h in __import__("json").load(open(f"{lab.ROOT}/studios/{s}/ledger.json"))["positions"]}
    failed = data.update_all(codes, sleep=0.1)
    if failed:
        print("行情拉取失败：", failed)
    print("\n".join(settle.run()))
    print(daily.run(fetch=False))
