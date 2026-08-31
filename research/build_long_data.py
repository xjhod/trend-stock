# -*- coding: utf-8 -*-
"""重建长历史数据(2022起): 个股缓存 + 行业指数缓存 + 大盘K线, 供多时点验证"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from concurrent.futures import ThreadPoolExecutor, as_completed
from data_fetcher import get_kline
import layers

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_STOCK = os.path.join(BASE, "research", "kline_cache_long.json")
OUT_IND = os.path.join(BASE, "research", "ind_idx_long.json")
OUT_MKT = os.path.join(BASE, "research", "mkt_long.json")
LIMIT = 1200

def load_all_a():
    return json.load(open(os.path.join(BASE, "bt_data", "all_a.json"), encoding="utf-8"))

def load_pool():
    return json.load(open(os.path.join(BASE, "highfit_pool.json"), encoding="utf-8"))

def fetch(code):
    try:
        df = get_kline(code, "daily", LIMIT, "qfq")
        if df is None or len(df) < 100: return None
        return (code, [{"date": str(r["date"]), "open": float(r["open"]), "high": float(r["high"]),
                        "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["volume"])}
                       for r in df.to_dict("records")])
    except Exception:
        return None

def build_stock_cache(pool):
    cache = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = [ex.submit(fetch, p["code"]) for p in pool]
        for f in as_completed(futs):
            r = f.result()
            if r: cache[r[0]] = r[1]
    json.dump(cache, open(OUT_STOCK, "w", encoding="utf-8"), ensure_ascii=False)
    return cache

def build_ind_cache(pool, all_a):
    """每行业取市值top15合成等权指数"""
    inds = sorted({p["ind"] for p in pool})
    ind_cache = {}
    for k, ind in enumerate(inds):
        members = [x for x in all_a if x.get("ind") == ind]
        members.sort(key=lambda x: -(x.get("mv") or 0))
        members = members[:15]
        series = {}
        for m in members:
            try:
                df = get_kline(m["code"], "daily", LIMIT, "qfq")
                if df.empty: continue
                for r in df.to_dict("records"):
                    series.setdefault(str(r["date"]), []).append(float(r["close"]))
            except Exception:
                continue
            time.sleep(0.05)
        dates = sorted(series.keys())
        if len(dates) >= 100:
            ind_cache[ind] = [{"date": d, "close": round(sum(series[d])/len(series[d]), 2)} for d in dates]
        print(f"  [{k+1}/{len(inds)}] {ind}: {len(ind_cache.get(ind, []))}条", flush=True)
    json.dump(ind_cache, open(OUT_IND, "w", encoding="utf-8"), ensure_ascii=False)
    return ind_cache

if __name__ == "__main__":
    pool = load_pool()
    print(f"[1/3] 拉取个股长历史K线 {len(pool)}只...")
    stock_cache = build_stock_cache(pool)
    d0 = min(r["date"] for rows in stock_cache.values() for r in rows)
    d1 = max(r["date"] for rows in stock_cache.values() for r in rows)
    print(f"  个股缓存: {len(stock_cache)}只 {d0} ~ {d1}")
    print(f"[2/3] 重建行业指数缓存...")
    all_a = load_all_a()
    ind_cache = build_ind_cache(pool, all_a)
    print(f"  行业缓存: {len(ind_cache)}个行业")
    print(f"[3/3] 大盘长K线...")
    mkt = layers.get_market_kline(LIMIT)
    json.dump(mkt, open(OUT_MKT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  大盘: {mkt[0]['date']} ~ {mkt[-1]['date']} {len(mkt)}条")
    print("完成")
