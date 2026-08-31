# -*- coding: utf-8 -*-
"""深挖: 趋势票(15日仅14%·up)与抄底票的正确率提升因子"""
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
TIMEPOINTS = ["2023-02-06","2023-05-10","2023-08-10","2023-11-10","2024-02-05","2024-05-10","2024-08-09","2024-11-08","2025-01-06","2025-04-10","2025-07-10","2025-10-10","2026-02-10","2026-05-10","2026-08-10"]
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
for tp in TIMEPOINTS:
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
        if len(closes) >= 21:
            rec["mom20"] = (closes[-1]/closes[-21]-1)*100
        if len(rr) >= 60:
            hi60 = max(x["high"] for x in rr[-60:])
            rec["dd60"] = rr[-1]["close"]/hi60-1
            rec["gain60"] = rr[-1]["close"]/rr[-60]["close"]-1
            d = layers._direction(closes)
            rec["direction"] = d
            rec["bias20"] = (rr[-1]["close"]/sum(x["close"] for x in rr[-20:])*20-1)*100
        # 放量(最近放量标记)
        rec["vol_confirm"] = any("放量" in t for t in tags)
        rec["resonance"] = any("共振" in t for t in tags)
        rec["grade"] = next((t.replace("形态","") for t in tags if "形态" in t), "weak")
        records.append(rec)
print(f"总记录 {len(records)}")
def winrate(recs, fwd):
    vals=[r[fwd] for r in recs if r[fwd] is not None]
    if not vals: return None, None, 0
    return sum(1 for v in vals if v>0)/len(vals)*100, sum(vals)/len(vals)*100, len(vals)
def rep(title, recs):
    parts=[]
    for f in ["fwd3","fwd7","fwd15"]:
        wr,avg,n = winrate(recs,f)
        parts.append(f"{f[-2:]}d:{wr:.0f}%({avg:+.1f}%)" if wr is not None else f"{f[-2:]}:--")
    print(f"  {title:<28} n={len(recs):<4} "+"  ".join(parts))

# ========== 趋势票深挖 ==========
trends = [r for r in records if r["type"]=="trend"]
print("\n== 趋势票(trend) ==")
rep("全部趋势", trends)
rep("方向=up", [r for r in trends if r.get("direction")=="up"])
rep("方向=side", [r for r in trends if r.get("direction")=="side"])
print("  -- 方向up, 按60日涨幅(趋势阶段) --")
rep("gain60<=15%(刚启动)", [r for r in trends if r.get("direction")=="up" and r.get("gain60",9)<=0.15])
rep("gain60 15-30%", [r for r in trends if r.get("direction")=="up" and 0.15<r.get("gain60",9)<=0.30])
rep("gain60>30%(涨幅过大)", [r for r in trends if r.get("direction")=="up" and r.get("gain60",9)>0.30])
print("  -- 方向up, 按距60日高回撤(是否已冲高) --")
rep("贴近高点(>-3%)", [r for r in trends if r.get("direction")=="up" and r.get("dd60",0)>-0.03])
rep("回撤3-8%", [r for r in trends if r.get("direction")=="up" and -0.08<r.get("dd60",0)<=-0.03])
rep("回撤>8%", [r for r in trends if r.get("direction")=="up" and r.get("dd60",0)<=-0.08])
print("  -- 方向up, 按放量 --")
rep("有放量确认", [r for r in trends if r.get("direction")=="up" and r.get("vol_confirm")])
rep("无放量", [r for r in trends if r.get("direction")=="up" and not r.get("vol_confirm")])
print("  -- 方向up, 按共振 --")
rep("有共振", [r for r in trends if r.get("direction")=="up" and r.get("resonance")])
rep("无共振", [r for r in trends if r.get("direction")=="up" and not r.get("resonance")])
print("  -- 方向up, 按大盘方向 --")
rep("大盘up", [r for r in trends if r.get("direction")=="up" and r.get("mkt")=="up"])
rep("大盘side", [r for r in trends if r.get("direction")=="up" and r.get("mkt")=="side"])

# ========== 抄底票深挖 ==========
rebs = [r for r in records if r["type"]=="rebound"]
print("\n== 抄底票(rebound) ==")
rep("全部抄底", rebs)
rep("浅超跌-20~-25%", [r for r in rebs if -0.25<r.get("dd60",0)<=-0.20])
rep("深超跌≤-25%", [r for r in rebs if r.get("dd60",0)<=-0.25])
rep("有放量", [r for r in rebs if r.get("vol_confirm")])
rep("无放量", [r for r in rebs if not r.get("vol_confirm")])
print("  -- 抄底按大盘 --")
rep("大盘up", [r for r in rebs if r.get("mkt")=="up"])
rep("大盘side", [r for r in rebs if r.get("mkt")=="side"])
