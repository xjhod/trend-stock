# -*- coding: utf-8 -*-
"""周频滚动扫描(2023-2026): 收集全部推荐+特征, 统计3/7/15日正确率, 样本更大更稳健"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util
import pandas as pd
import layers
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
klines = json.load(open(os.path.join(BASE,"research","kline_cache_long.json"), encoding="utf-8"))
ind_long = json.load(open(os.path.join(BASE,"research","ind_idx_long.json"), encoding="utf-8"))
mkt_long = json.load(open(os.path.join(BASE,"research","mkt_long.json"), encoding="utf-8"))
pool = json.load(open(os.path.join(BASE,"highfit_pool.json"), encoding="utf-8"))
layers._load_ind_cache = lambda: ind_long
layers.get_market_kline = lambda *a, **k: mkt_long
spec = importlib.util.spec_from_file_location("fb", os.path.join(BASE,"research","full_backtest.py"))
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)

# 每周一(或每5交易日)扫描, 从2023-01起
mkt_dates = [r["date"] for r in mkt_long if "2023-01-01" <= r["date"] <= "2026-08-28"]
SCAN_DATES = mkt_dates[::5]   # 每5个交易日扫一次
print(f"扫描时点数: {len(SCAN_DATES)}")

def df_at(code, asof):
    rows = [r for r in klines.get(code, []) if r["date"] <= asof]
    return pd.DataFrame(rows) if len(rows) >= 60 else None
def fwd_ret(rows, asof, n):
    dates = [r["date"] for r in rows]
    if asof not in dates: return None
    i = dates.index(asof)
    if i+n >= len(rows): return None
    return rows[i+n]["close"]/rows[i]["close"]-1

records = []
for k, tp in enumerate(SCAN_DATES):
    gate, sw, mkt_dir = fb.mkt_snapshot(tp)
    if not gate: continue
    for it in pool:
        df = df_at(it["code"], tp)
        if df is None: continue
        r = fb.judge(it, df, tp, mkt_dir)
        if not r: continue
        typ, lv, tags = r
        rows = klines.get(it["code"], [])
        rr = [x for x in rows if x["date"] <= tp]
        closes = [x["close"] for x in rr]
        rec = {"code":it["code"],"name":it.get("name",""),"type":typ,"level":lv,"tags":tags,"asof":tp,"mkt":mkt_dir,
               "fwd3":fwd_ret(rows,tp,3),"fwd7":fwd_ret(rows,tp,7),"fwd15":fwd_ret(rows,tp,15)}
        if len(closes) >= 21: rec["mom20"]=(closes[-1]/closes[-21]-1)*100
        if len(rr) >= 60:
            hi60=max(x["high"] for x in rr[-60:])
            rec["dd60"]=rr[-1]["close"]/hi60-1
            rec["gain60"]=rr[-1]["close"]/rr[-60]["close"]-1
            rec["direction"]=layers._direction(closes)
            rec["bias20"]=(rr[-1]["close"]/sum(x["close"] for x in rr[-20:])*20-1)*100
        records.append(rec)
    if (k+1) % 30 == 0: print(f"  {k+1}/{len(SCAN_DATES)} 已收集{len(records)}条", flush=True)
print(f"\n总推荐记录: {len(records)}")
json.dump(records, open("research/fwd_records.json","w",encoding="utf-8"), ensure_ascii=False)

def winrate(recs, fwd):
    vals=[r[fwd] for r in recs if r[fwd] is not None]
    if not vals: return None, None, 0
    return sum(1 for v in vals if v>0)/len(vals)*100, sum(vals)/len(vals)*100, len(vals)
def rep(title, recs):
    parts=[]
    for f in ["fwd3","fwd7","fwd15"]:
        wr,avg,n=winrate(recs,f)
        parts.append(f"{f[-2:]}d:{wr:.0f}%({avg:+.1f}%)" if wr is not None else f"{f[-2:]}:--")
    print(f"  {title:<26} n={len(recs):<4} "+"  ".join(parts))

print("\n===== 基线(周频, 大样本) =====")
rep("全部推荐", records)
rep("抄底 rebound", [r for r in records if r["type"]=="rebound"])
rep("趋势 trend", [r for r in records if r["type"]=="trend"])
print("\n-- 趋势票按方向 --")
rep("趋势·方向up", [r for r in records if r["type"]=="trend" and r.get("direction")=="up"])
rep("趋势·方向side", [r for r in records if r["type"]=="trend" and r.get("direction")=="side"])
print("\n-- 趋势·方向up 按60日涨幅 --")
rep("up·gain60≤15%", [r for r in records if r["type"]=="trend" and r.get("direction")=="up" and r.get("gain60",9)<=0.15])
rep("up·gain60 15-30%", [r for r in records if r["type"]=="trend" and r.get("direction")=="up" and 0.15<r.get("gain60",9)<=0.30])
rep("up·gain60>30%", [r for r in records if r["type"]=="trend" and r.get("direction")=="up" and r.get("gain60",9)>0.30])
print("\n-- 趋势·方向side 按60日涨幅 --")
rep("side·gain60≤15%", [r for r in records if r["type"]=="trend" and r.get("direction")=="side" and r.get("gain60",9)<=0.15])
rep("side·gain60 15-30%", [r for r in records if r["type"]=="trend" and r.get("direction")=="side" and 0.15<r.get("gain60",9)<=0.30])
rep("side·gain60>30%", [r for r in records if r["type"]=="trend" and r.get("direction")=="side" and r.get("gain60",9)>0.30])
print("\n-- 抄底票按回撤深度 --")
rep("抄底·浅超跌(-20~-25%)", [r for r in records if r["type"]=="rebound" and -0.25<r.get("dd60",0)<=-0.20])
rep("抄底·深超跌(≤-25%)", [r for r in records if r["type"]=="rebound" and r.get("dd60",0)<=-0.25])
print("\n-- 全部按大盘方向 --")
rep("大盘up", [r for r in records if r.get("mkt")=="up"])
rep("大盘side", [r for r in records if r.get("mkt")=="side"])
print("\n-- 按个股方向(全部) --")
rep("个股up", [r for r in records if r.get("direction")=="up"])
rep("个股side", [r for r in records if r.get("direction")=="side"])
