"""拉取股票池日线（东方财富公开接口，前复权），保存为 data/<code>.csv"""
import json, os, time, urllib.request

POOL = {
    "000026": "飞亚达", "002222": "福晶科技", "002583": "海能达", "002600": "领益智造",
    "002708": "光洋股份", "002931": "锋龙股份", "300418": "昆仑万维", "300638": "广和通",
    "600498": "烽火通信", "600776": "东方通信", "600879": "航天电子", "601689": "拓普集团",
    "601801": "皖新传媒", "603112": "XD华翔股", "603626": "科森科技", "688258": "卓易信息",
    "688802": "沐曦股份-U",
    "002149": "西部材料",  # 样板股，不参与选股，只用于校验
}
URL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={m}.{c}"
       "&fields1=f1,f2&fields2=f51,f52,f53,f54,f55,f56,f57,f59,f61"
       "&klt=101&fqt=1&end=20500101&lmt=500")
HEADER = "date,open,close,high,low,volume,amount,pct,turnover"

def market(code):
    return "1" if code.startswith(("6", "9")) else "0"

def main():
    os.makedirs("data", exist_ok=True)
    for code, name in POOL.items():
        with urllib.request.urlopen(URL.format(m=market(code), c=code), timeout=15) as r:
            d = json.load(r)["data"]
        rows = d["klines"] if d else []
        with open(f"data/{code}.csv", "w") as f:
            f.write(HEADER + "\n" + "\n".join(rows) + "\n")
        print(code, name, len(rows), rows[-1] if rows else "NO DATA")
        time.sleep(0.3)

if __name__ == "__main__":
    main()
