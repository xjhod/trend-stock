# -*- coding: utf-8 -*-
"""用长K线(1000根≈4年)重建行业指数缓存：用行业内所有成员、最低3只，覆盖更长历史。"""
import json
import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
import pool_manager as pm
from data_fetcher import get_kline

pool = json.load(open(pm.HIGHFIT_FILE, encoding="utf-8"))
print(f"池子 {len(pool)} 只，并行拉取 1000 根K线…")

t0 = time.time()
klines = {}
done = 0
def fetch(code):
    try:
        df = get_kline(code, "daily", 1000, "qfq")
        if df is not None and len(df) >= 120:
            return df
    except Exception:
        pass
    return None

with ThreadPoolExecutor(max_workers=12) as ex:
    futs = {ex.submit(fetch, r["code"]): r for r in pool}
    for fut in as_completed(futs):
        done += 1
        if done % 300 == 0:
            print(f"  拉取 {done}/{len(pool)}… ({time.time()-t0:.0f}s)")
        r = fut.result()
        if r is not None:
            klines[futs[fut]["code"]] = r

print(f"拉取完成 {len(klines)}/{len(pool)} 只，耗时 {time.time()-t0:.0f}s")

# 按行业分组，用所有成员合成（最低3只/日期）
ind_members = {}
for r in pool:
    ind_members.setdefault(r["ind"], []).append(r)

ind_cache = {}
for ind, ms in ind_members.items():
    series = {}
    ok = 0
    for m in ms:
        df = klines.get(m["code"])
        if df is None:
            continue
        try:
            for _, row in df.iterrows():
                series.setdefault(str(row["date"]), []).append(float(row["close"]))
            ok += 1
        except Exception:
            continue
    if ok < 3:
        continue
    rows = [{"date": d, "close": sum(v) / len(v)}
            for d, v in series.items() if len(v) >= 3]
    rows.sort(key=lambda x: x["date"])
    if len(rows) >= 100:
        ind_cache[ind] = rows

# 直接用新构建的长历史指数覆盖旧缓存
with open(pm.IND_CACHE_FILE, "w", encoding="utf-8") as f:
    json.dump(ind_cache, f, ensure_ascii=False)

# 统计
lens = sorted([len(v) for v in ind_cache.values()], reverse=True)
print(f"\n行业指数缓存：{len(ind_cache)} 个行业")
print(f"最长 {lens[0]} 根, 中位数 {lens[len(lens)//2]} 根, 最短 {lens[-1]} 根")
buckets = {'<300':0, '300-500':0, '500-800':0, '800-1000':0, '>1000':0}
for v in ind_cache.values():
    n=len(v)
    if n<300: buckets['<300']+=1
    elif n<500: buckets['300-500']+=1
    elif n<800: buckets['500-800']+=1
    elif n<1000: buckets['800-1000']+=1
    else: buckets['>1000']+=1
print('长度分布:', buckets)
for k in list(ind_cache)[:5]:
    v = ind_cache[k]
    print(f"  {k}: {len(v)}根, {v[0]['date']} ~ {v[-1]['date']}")
print(f"总耗时 {time.time()-t0:.0f}s")
