# -*- coding: utf-8 -*-
"""回测数据获取: 高适配池1626只K线 + 大盘 + 行业指数, 保存到JSON"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data_fetcher as df
import layers

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pool = json.load(open(os.path.join(BASE, "highfit_pool.json"), encoding="utf-8"))
print(f"高适配池: {len(pool)}只")

# 1. 获取所有股票K线(最近300天)
klines = {}
t0 = time.time()
for i, it in enumerate(pool):
    code = it["code"]
    try:
        kdf = df.get_kline(code, "daily", 300, "")
        if kdf is not None and len(kdf) >= 60:
            klines[code] = kdf.to_dict("records")
    except Exception:
        pass
    if (i+1) % 200 == 0:
        print(f"  已获取 {i+1}/{len(pool)}, 成功{len(klines)}只, 耗时{time.time()-t0:.0f}s", flush=True)
print(f"K线获取完成: {len(klines)}只, 耗时{time.time()-t0:.0f}s")

# 2. 大盘数据
mkt = df.get_kline("sh000001", "daily", 300, "")
mkt_rows = mkt.to_dict("records") if mkt is not None else []
print(f"大盘: {len(mkt_rows)}天, {mkt_rows[0]['date']}~{mkt_rows[-1]['date']}")

# 3. 行业指数数据
ind_cache = layers._load_ind_cache()
ind_rows = {k: v for k, v in ind_cache.items()}
print(f"行业指数: {len(ind_rows)}个")

# 保存
out = {
    "klines": klines,
    "market": mkt_rows,
    "industry": ind_rows,
    "pool": pool,
}
outpath = os.path.join(BASE, "research", "bt_0815_data.json")
json.dump(out, open(outpath, "w", encoding="utf-8"), ensure_ascii=False)
print(f"已保存: {outpath}")
print(f"文件大小: {os.path.getsize(outpath)/1024/1024:.1f}MB")
