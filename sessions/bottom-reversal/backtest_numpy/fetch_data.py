"""下载A股日线数据（baostock，前复权）。支持断点续传。

用法:
    python fetch_data.py [开始日期] [结束日期] [进程数]
    重复运行会跳过已下载的分片；全部完成后合并为 data/daily.pkl

说明: baostock 不含北交所；股票列表取结束日期当天在市股票（存在幸存者偏差）。
"""
import sys
import os
import time
import pickle
from multiprocessing import Pool

import baostock as bs
import pandas as pd

START = sys.argv[1] if len(sys.argv) > 1 else "2023-01-01"
END = sys.argv[2] if len(sys.argv) > 2 else "2026-11-06"
NPROC = int(sys.argv[3]) if len(sys.argv) > 3 else 12
FIELDS = "date,open,high,low,close,volume,amount,turn,isST,tradestatus,pctChg"
BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "data")
PART_DIR = os.path.join(OUT_DIR, "parts")
BATCH = 100


def rs_to_df(rs):
    if rs.error_code != "0":
        return pd.DataFrame()
    rows = list(rs.data)
    rs.cur_row_num = len(rs.data)
    while rs.error_code == "0" and rs.next():  # 分页
        rows.extend(rs.data)
        rs.cur_row_num = len(rs.data)
    return pd.DataFrame(rows, columns=rs.fields)


def stock_list(day):
    path = os.path.join(OUT_DIR, "names.pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    bs.login()
    df = pd.DataFrame()
    for back in range(10):  # 往前找最近的交易日
        d = (pd.Timestamp(day) - pd.Timedelta(days=back)).strftime("%Y-%m-%d")
        df = rs_to_df(bs.query_all_stock(d))
        if len(df):
            break
    bs.logout()
    mask = df["code"].str.match(r"^(sh\.60|sh\.68|sz\.00|sz\.30)")
    names = df.loc[mask, ["code", "code_name"]].reset_index(drop=True)
    names.to_pickle(path)
    return names


def _init():
    bs.login()


def fetch_one(code):
    for _ in range(3):
        try:
            rs = bs.query_history_k_data_plus(code, FIELDS, START, END, "d", "2")
            if rs.error_code != "0":
                raise RuntimeError(rs.error_msg)
            df = rs_to_df(rs)
            df["code"] = code
            return df
        except Exception:
            time.sleep(1)
            bs.login()
    return pd.DataFrame()


def fetch_index(code):
    rs = bs.query_history_k_data_plus(code, "date,close", START, END, "d")
    df = rs_to_df(rs)
    df["close"] = pd.to_numeric(df["close"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def merge(names):
    frames = [pd.read_pickle(os.path.join(PART_DIR, f))
              for f in sorted(os.listdir(PART_DIR))]
    data = pd.concat(frames, ignore_index=True)
    num = ["open", "high", "low", "close", "volume", "amount", "turn", "pctChg"]
    data[num] = data[num].apply(pd.to_numeric, errors="coerce")
    for col in ["isST", "tradestatus"]:
        data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0).astype(int)
    data["date"] = pd.to_datetime(data["date"])
    bs.login()
    idx = {c: fetch_index(c) for c in ["sh.000001", "sz.399001", "sz.399006"]}
    bs.logout()
    with open(os.path.join(OUT_DIR, "daily.pkl"), "wb") as f:
        pickle.dump({"daily": data, "names": names, "index": idx}, f)
    print("合并完成", data.shape, "股票数", data["code"].nunique(), flush=True)


if __name__ == "__main__":
    os.makedirs(PART_DIR, exist_ok=True)
    names = stock_list(END)
    codes = list(names["code"])
    batches = [codes[i:i + BATCH] for i in range(0, len(codes), BATCH)]
    todo = [i for i in range(len(batches))
            if not os.path.exists(os.path.join(PART_DIR, f"part_{i:03d}.pkl"))]
    print(f"股票 {len(codes)}，分片 {len(batches)}，待下载 {len(todo)}", flush=True)
    t0 = time.time()
    with Pool(NPROC, initializer=_init) as pool:
        for bi in todo:
            dfs = [d for d in pool.map(fetch_one, batches[bi]) if len(d)]
            pd.concat(dfs, ignore_index=True).to_pickle(
                os.path.join(PART_DIR, f"part_{bi:03d}.pkl"))
            print(f"分片 {bi} 完成，用时 {time.time() - t0:.0f}s", flush=True)
    merge(names)
