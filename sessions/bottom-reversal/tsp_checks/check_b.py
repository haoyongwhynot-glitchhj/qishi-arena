import importlib.util, os
from pathlib import Path
import pandas as pd, polars as pl, numpy as np
P=Path("/projects/sandbox/backtest/data/parts")
d=pd.concat([pd.read_pickle(P/f) for f in os.listdir(P)])
for c in ["open","high","low","close","volume","amount","turn","pctChg"]: d[c]=pd.to_numeric(d[c],errors="coerce")
d=d[(d.tradestatus.astype(int)==1)&(d.volume>0)].copy(); d["date"]=pd.to_datetime(d["date"])
d["symbol"]=d.code.str[3:]+"."+d.code.str[:2].str.upper(); d=d.sort_values(["symbol","date"])
lim=np.where(d.symbol.str[:2].isin(["30","68"]),20,10)
zt=((d.pctChg>=lim-0.25)&(d.close>=d.high*0.998)).astype(int)
d["lu"]=zt.groupby(d.symbol).transform(lambda s: s.groupby((s==0).cumsum()).cumsum())
panel=pl.DataFrame({"symbol":d.symbol.tolist(),"date":pl.Series(d.date.dt.date.tolist(),dtype=pl.Date),
  **{c:d[c].to_numpy() for c in ["open","high","low","close","volume","amount"]},
  "turnover_rate":d.turn.to_numpy(),"consecutive_limit_ups":d.lu.to_numpy()})
spec=importlib.util.spec_from_file_location("b","/projects/sandbox/tsp_lab/strategies/custom_leader_first_divergence.py")
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
h=m.filter_history(panel,{}).filter(pl.col("date")>=pl.date(2024,1,1))
print("B命中",h.height); print(h.select("symbol","date","consecutive_limit_ups","turnover_rate").head(12))
