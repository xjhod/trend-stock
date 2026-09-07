# -*- coding: utf-8 -*-
"""补充分析: 6/15-8/14 期间涨幅排名 + 程序命中 + 大盘位置"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = json.load(open(os.path.join(BASE, "research", "bt_0815_data.json"), encoding="utf-8"))
klines = data["klines"]
mkt_rows = data["market"]
pool = data["pool"]

SCAN_DATE = "2026-06-15"
END_DATE = "2026-08-14"

# 1. 大盘位置分析
print("===== 大盘位置(6/15前后) =====")
mrr = [r for r in mkt_rows if SCAN_DATE >= r["date"] >= "2026-03-01"]
mcl = [r["close"] for r in mrr]
for r in mrr[-6:]:
    print(f"  {r['date']}: {r['close']:.0f}")
hi250 = max(r["close"] for r in mrr)
c = mrr[-1]["close"]
print(f"  3月以来最高: {hi250:.0f}, 6/15收盘: {c:.0f}, 距高点: {(c/hi250-1)*100:.1f}%")
# 近20日涨幅
if len(mcl) >= 21:
    print(f"  近20日涨幅: {(mcl[-1]/mcl[-21]-1)*100:+.1f}%")
# 近5日
if len(mcl) >= 6:
    print(f"  近5日涨幅: {(mcl[-1]/mcl[-6]-1)*100:+.1f}%")

# 2. 6/15-8/14 涨幅排名
print(f"\n===== {SCAN_DATE}-{END_DATE} 涨幅排名 =====")
gains = []
for it in pool:
    code = it["code"]
    rows = klines.get(code, [])
    if not rows: continue
    start_rows = [r for r in rows if r["date"] >= SCAN_DATE]
    end_rows = [r for r in rows if r["date"] <= END_DATE]
    if not start_rows or not end_rows: continue
    start_price = start_rows[0]["close"]
    end_price = end_rows[-1]["close"]
    gain = end_price / start_price - 1
    gains.append({
        "code": code, "name": it.get("name",""), "ind": it.get("ind",""),
        "start": start_price, "end": end_price, "gain": gain,
    })
gains.sort(key=lambda x: -x["gain"])
print(f"统计{len(gains)}只, 上涨{sum(1 for g in gains if g['gain']>0)}只({sum(1 for g in gains if g['gain']>0)/len(gains)*100:.0f}%), 平均{sum(g['gain'] for g in gains)/len(gains)*100:+.1f}%")
print(f"\n涨幅前20:")
print(f"{'#':<3}{'股票':<12}{'行业':<10}{'起始价':<8}{'结束价':<8}{'涨幅':<8}")
for i, g in enumerate(gains[:20]):
    print(f"{i+1:<3}{g['name']:<12}{g['ind'][:8]:<10}{g['start']:<8.2f}{g['end']:<8.2f}{g['gain']*100:<+8.1f}")

# 3. 程序推荐命中(加载回测结果)
res = json.load(open(os.path.join(BASE, "research", "bt_0615_v2_result.json"), encoding="utf-8"))
rec_codes = set(s["code"] for s in res["sigs"])
print(f"\n===== 程序命中 =====")
print(f"程序推荐: {len(rec_codes)}只")
rec_gains = [g for g in gains if g["code"] in rec_codes]
not_rec_gains = [g for g in gains if g["code"] not in rec_codes]
print(f"  推荐股平均涨幅: {sum(g['gain'] for g in rec_gains)/len(rec_gains)*100:+.1f}%")
print(f"  推荐股上涨率: {sum(1 for g in rec_gains if g['gain']>0)}/{len(rec_gains)} = {sum(1 for g in rec_gains if g['gain']>0)/len(rec_gains)*100:.0f}%")
print(f"  未推荐股平均涨幅: {sum(g['gain'] for g in not_rec_gains)/len(not_rec_gains)*100:+.1f}%")
print(f"  未推荐股上涨率: {sum(1 for g in not_rec_gains if g['gain']>0)}/{len(not_rec_gains)} = {sum(1 for g in not_rec_gains if g['gain']>0)/len(not_rec_gains)*100:.0f}%")
print(f"\n涨幅前20中被程序推荐: {sum(1 for g in gains[:20] if g['code'] in rec_codes)}/20")
print(f"涨幅前50中被程序推荐: {sum(1 for g in gains[:50] if g['code'] in rec_codes)}/50")

# 4. 涨幅前20的行业分布
from collections import Counter
top50 = Counter(g["ind"] for g in gains[:50])
print(f"\n涨幅前50行业分布:")
for ind, cnt in top50.most_common(8):
    print(f"  {ind}: {cnt}只")

# 5. 程序推荐的133只里, 表现最好的和行业分布
print(f"\n===== 程序推荐股票的表现 =====")
print(f"推荐股中涨幅前10:")
for g in sorted(rec_gains, key=lambda x: -x["gain"])[:10]:
    print(f"  {g['name']:<12}{g['ind'][:8]:<10}{g['gain']*100:+.1f}%")
print(f"\n推荐股行业分布:")
rec_ind = Counter(g["ind"] for g in rec_gains)
for ind, cnt in rec_ind.most_common(8):
    print(f"  {ind}: {cnt}只")
