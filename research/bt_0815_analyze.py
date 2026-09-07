# -*- coding: utf-8 -*-
"""分析: 8/15-9/5期间哪些股票涨了, 程序为什么没推荐它们"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import data_fetcher as df
import scan_daily
import layers
import analysis as an

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = json.load(open(os.path.join(BASE, "research", "bt_0815_data.json"), encoding="utf-8"))
klines = data["klines"]
mkt_rows = data["market"]
ind_rows = data["industry"]
pool = data["pool"]

SCAN_DATE = "2026-08-15"
END_DATE = "2026-09-05"

# 1. 计算每只股票8/15-9/5的涨幅
print("===== 8/15-9/5 涨幅排名 =====")
gains = []
for it in pool:
    code = it["code"]
    rows = klines.get(code, [])
    if not rows:
        continue
    start_rows = [r for r in rows if r["date"] >= SCAN_DATE]
    end_rows = [r for r in rows if r["date"] <= END_DATE]
    if not start_rows or not end_rows:
        continue
    start_price = start_rows[0]["close"]
    end_price = end_rows[-1]["close"]
    gain = end_price / start_price - 1
    # 期间最高价
    high_rows = [r for r in rows if SCAN_DATE <= r["date"] <= END_DATE]
    max_high = max(r["high"] for r in high_rows) if high_rows else start_price
    max_gain = max_high / start_price - 1
    gains.append({
        "code": code, "name": it.get("name",""), "ind": it.get("ind",""),
        "start": start_price, "end": end_price, "gain": gain,
        "max_gain": max_gain, "mv": it.get("mv",0),
    })

gains.sort(key=lambda x: -x["gain"])
print(f"统计股票数: {len(gains)}")
print(f"上涨家数: {sum(1 for g in gains if g['gain']>0)} ({sum(1 for g in gains if g['gain']>0)/len(gains)*100:.0f}%)")
print(f"下跌家数: {sum(1 for g in gains if g['gain']<0)} ({sum(1 for g in gains if g['gain']<0)/len(gains)*100:.0f}%)")
print(f"平均涨幅: {sum(g['gain'] for g in gains)/len(gains)*100:+.1f}%")
print(f"中位数涨幅: {sorted(g['gain'] for g in gains)[len(gains)//2]*100:+.1f}%")

print(f"\n===== 涨幅前20名 =====")
print(f"{'排名':<4}{'股票':<12}{'行业':<12}{'起始价':<8}{'结束价':<8}{'涨幅':<8}{'最高涨幅':<8}")
for i, g in enumerate(gains[:20]):
    print(f"{i+1:<4}{g['name']:<12}{g['ind'][:8]:<12}{g['start']:<8.2f}{g['end']:<8.2f}{g['gain']*100:<+8.1f}{g['max_gain']*100:<+8.1f}")

# 2. 分析涨幅前20在8/15时的技术特征
print(f"\n===== 涨幅前20在8/15时的技术特征(为什么没被推荐) =====")

ASOF = SCAN_DATE
def hist_get_kline(code, period="daily", limit=300, adjust="qfq"):
    rows = [r for r in klines.get(code, []) if r["date"] <= ASOF]
    if not rows: return pd.DataFrame()
    return pd.DataFrame(rows).tail(limit)

df.get_kline = hist_get_kline
scan_daily.get_kline = hist_get_kline
layers.get_market_kline = lambda *a, **k: [r for r in mkt_rows if r["date"] <= ASOF][-80:]
layers._load_ind_cache = lambda: {k: [r for r in v if r["date"] <= ASOF] for k, v in ind_rows.items()}

print(f"{'股票':<12}{'60日涨幅':<8}{'距60日高':<8}{'趋势':<8}{'周线多头':<8}{'看涨形态':<10}{'超跌':<6}{'被推荐':<6}{'原因'}")
for g in gains[:20]:
    code = g["code"]
    rows = [r for r in klines.get(code, []) if r["date"] <= SCAN_DATE]
    if len(rows) < 60:
        continue
    kdf = pd.DataFrame(rows).tail(300)
    last = kdf.iloc[-1]
    close = float(last["close"])
    hi60 = max(kdf["high"].tolist()[-60:])
    dd60 = close / hi60 - 1
    gain60 = close / float(kdf.iloc[-60]["close"]) - 1 if len(kdf) >= 60 else 0
    trend = an.analyze_trend(kdf, "日线")
    direction = trend.get("direction", "sideways")
    pats = scan_daily.detect_bullish(kdf)
    pat_names = "+".join(sorted(set(p[0] for p in pats))[:2]) if pats else "无"
    wk_up = scan_daily._weekly_trend_up(code)
    stabilized = scan_daily._stabilized(kdf)
    over, _ = scan_daily._overheated(kdf)
    
    # 运行_scan_one看是否被推荐
    it = {"code": code, "name": g["name"], "ind": g["ind"]}
    try:
        sig = scan_daily._scan_one(it)
        recommended = "是" if sig else "否"
    except:
        recommended = "错"
    
    # 分析没被推荐的原因
    reasons = []
    if dd60 > -0.20:
        reasons.append("不够超跌")
    if not wk_up:
        reasons.append("周线非多头")
    if over:
        reasons.append("过热过滤")
    if not pats:
        reasons.append("无看涨形态")
    if gain60 > 0.60:
        reasons.append(f"60日已涨{gain60*100:.0f}%(中继/衰竭)")
    if direction == "up" and not pats:
        reasons.append("追高无形态")
    
    reason_str = "; ".join(reasons[:3]) if reasons else "其他"
    print(f"{g['name']:<12}{gain60*100:<+8.1f}{dd60*100:<+8.1f}{direction:<8}{'是' if wk_up else '否':<8}{pat_names:<10}{'是' if dd60<=-0.20 else '否':<6}{recommended:<6}{reason_str}")

# 3. 行业分布: 涨幅前20集中在哪些行业
print(f"\n===== 涨幅前50行业分布 =====")
from collections import Counter
top50_inds = Counter(g["ind"] for g in gains[:50])
for ind, cnt in top50_inds.most_common(10):
    print(f"  {ind}: {cnt}只")

# 4. 对比: 程序推荐的216只 vs 实际上涨的
print(f"\n===== 程序推荐vs实际上涨对比 =====")
# 重新扫描获取推荐列表
ASOF = SCAN_DATE
sigs = []
for it in pool:
    code = it["code"]
    if code not in klines: continue
    rows = [r for r in klines[code] if r["date"] <= SCAN_DATE]
    if len(rows) < 60: continue
    try:
        r = scan_daily._scan_one(it)
        if r: sigs.append(r)
    except: pass

rec_codes = set(s["code"] for s in sigs)
rec_gains = [g for g in gains if g["code"] in rec_codes]
not_rec_gains = [g for g in gains if g["code"] not in rec_codes]

print(f"程序推荐: {len(rec_codes)}只")
print(f"  推荐股平均涨幅: {sum(g['gain'] for g in rec_gains)/len(rec_gains)*100:+.1f}%" if rec_gains else "  无推荐股")
print(f"  推荐股上涨率: {sum(1 for g in rec_gains if g['gain']>0)}/{len(rec_gains)} = {sum(1 for g in rec_gains if g['gain']>0)/len(rec_gains)*100:.0f}%" if rec_gains else "")
print(f"未推荐: {len(not_rec_gains)}只")
print(f"  未推荐股平均涨幅: {sum(g['gain'] for g in not_rec_gains)/len(not_rec_gains)*100:+.1f}%")
print(f"  未推荐股上涨率: {sum(1 for g in not_rec_gains if g['gain']>0)}/{len(not_rec_gains)} = {sum(1 for g in not_rec_gains if g['gain']>0)/len(not_rec_gains)*100:.0f}%")

# 涨幅前20中有几只是被推荐的
top20_rec = sum(1 for g in gains[:20] if g["code"] in rec_codes)
print(f"\n涨幅前20中被程序推荐: {top20_rec}/20")
top50_rec = sum(1 for g in gains[:50] if g["code"] in rec_codes)
print(f"涨幅前50中被程序推荐: {top50_rec}/50")
