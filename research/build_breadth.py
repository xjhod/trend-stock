# -*- coding: utf-8 -*-
"""拉取广度抽样300只K线(2022起), 保存供市场广度计算"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from concurrent.futures import ThreadPoolExecutor, as_completed
from data_fetcher import get_kline
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sample = json.load(open(os.path.join(BASE,"research","breadth_sample.json"), encoding="utf-8"))
OUT = os.path.join(BASE,"research","breadth_klines.json")
def fetch(x):
    try:
        df = get_kline(x["code"], "daily", 1200, "qfq")
        if df is None or len(df) < 100: return None
        return (x["code"], [{"date":str(r["date"]),"close":float(r["close"])} for r in df.to_dict("records")])
    except Exception:
        return None
cache = {}
with ThreadPoolExecutor(max_workers=12) as ex:
    futs = [ex.submit(fetch, x) for x in sample]
    for f in as_completed(futs):
        r = f.result()
        if r: cache[r[0]] = r[1]
json.dump(cache, open(OUT,"w",encoding="utf-8"), ensure_ascii=False)
d0 = min(r["date"] for rows in cache.values() for r in rows)
d1 = max(r["date"] for rows in cache.values() for r in rows)
print(f"广度抽样K线: {len(cache)}只 {d0} ~ {d1}")
