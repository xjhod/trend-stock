# -*- coding: utf-8 -*-
"""4/15回测数据获取: 高适配池1626只400天K线 + 大盘 + 行业指数"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data_fetcher as df
import layers

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pool = json.load(open(os.path.join(BASE, "highfit_pool.json"), encoding="utf-8"))
print(f"高适配池: {len(pool)}只")

cache_file = os.path.join(BASE, "research", "bt_0415_klines_partial.json")
if os.path.exists(cache_file):
    klines = json.load(open(cache_file, encoding="utf-8"))
    print(f"从部分缓存恢复: {len(klines)}只")
else:
    klines = {}

t0 = time.time()
errors = 0
for i, it in enumerate(pool):
    code = it["code"]
    if code in klines:
        continue
    try:
        kdf = df.get_kline(code, "daily", 400, "")
        if kdf is not None and len(kdf) >= 60:
            klines[code] = kdf.to_dict("records")
        else:
            errors += 1
    except Exception:
        errors += 1
    if (i + 1) % 200 == 0:
        print(f"  进度 {i+1}/{len(pool)}, 成功{len(klines)}只, 错误{errors}, 耗时{time.time()-t0:.0f}s", flush=True)
        json.dump(klines, open(cache_file, "w", encoding="utf-8"), ensure_ascii=False)

print(f"K线获取完成: {len(klines)}只, 错误{errors}, 耗时{time.time()-t0:.0f}s")

# 大盘400天
mkt = df.get_kline("sh000001", "daily", 400, "")
mkt_rows = mkt.to_dict("records") if mkt is not None else []
print(f"大盘: {len(mkt_rows)}天, {mkt_rows[-1]['date']}~{mkt_rows[0]['date']}")

# 行业
ind_cache = layers._load_ind_cache()
ind_rows = {k: v for k, v in ind_cache.items()}
print(f"行业: {len(ind_rows)}个")

out = {"klines": klines, "market": mkt_rows, "industry": ind_rows, "pool": pool}
outpath = os.path.join(BASE, "research", "bt_0415_data.json")
json.dump(out, open(outpath, "w", encoding="utf-8"), ensure_ascii=False)
print(f"已保存: {outpath}, {os.path.getsize(outpath)/1024/1024:.1f}MB")
