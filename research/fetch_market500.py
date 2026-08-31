# -*- coding: utf-8 -*-
"""拉取pool_100e当前市值前500只的日线(复用已有182只缓存), 存kline_market500.json"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from concurrent.futures import ThreadPoolExecutor, as_completed
from data_fetcher import get_kline
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIMIT = 1200

pool100 = json.load(open(os.path.join(BASE,"bt_data","pool_100e.json"), encoding="utf-8"))
pool100.sort(key=lambda x: -(x.get("mv") or 0))
top500 = pool100[:500]
# 已有缓存
have = {}
if os.path.exists(os.path.join(BASE,"research","kline_market500.json")):
    have = json.load(open(os.path.join(BASE,"research","kline_market500.json"), encoding="utf-8"))
else:
    try:
        have = json.load(open(os.path.join(BASE,"research","kline_cache_long.json"), encoding="utf-8"))
    except Exception:
        have = {}
todo = [p for p in top500 if p["code"] not in have]
print(f"候选500只, 已有{len(have)}只缓存, 需拉{len(todo)}只", flush=True)
def fetch(p):
    try:
        df = get_kline(p["code"], "daily", LIMIT, "qfq")
        if df is None or len(df) < 250: return None
        return (p["code"], [{"date": str(r["date"]), "open": float(r["open"]), "high": float(r["high"]),
                             "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["volume"])}
                            for r in df.to_dict("records")])
    except Exception as e:
        return None
done = 0
with ThreadPoolExecutor(max_workers=12) as ex:
    futs = {ex.submit(fetch, p): p for p in todo}
    for f in as_completed(futs):
        r = f.result()
        if r: have[r[0]] = r[1]
        done += 1
        if done % 50 == 0:
            print(f"  已处理 {done}/{len(todo)}, 缓存{len(have)}只", flush=True)
            json.dump(have, open(os.path.join(BASE,"research","kline_market500.json"),"w",encoding="utf-8"), ensure_ascii=False)
json.dump(have, open(os.path.join(BASE,"research","kline_market500.json"),"w",encoding="utf-8"), ensure_ascii=False)
print(f"完成: 共缓存{len(have)}只", flush=True)
