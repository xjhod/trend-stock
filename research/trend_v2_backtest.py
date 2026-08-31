# -*- coding: utf-8 -*-
"""新趋势通道B完整模拟: 周线多头主判据 + 全部过滤 + 门槛, 统计3/7/15/30/60日正确率
对比: 旧通道(日线up主判据) vs 新通道(周线多头主判据)"""
import sys, os, json, bisect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import numpy as np
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
klines = json.load(open(os.path.join(BASE,"research","kline_cache_long.json"), encoding="utf-8"))
ind_long = json.load(open(os.path.join(BASE,"research","ind_idx_long.json"), encoding="utf-8"))
mkt_long = json.load(open(os.path.join(BASE,"research","mkt_long.json"), encoding="utf-8"))
pool = json.load(open(os.path.join(BASE,"highfit_pool.json"), encoding="utf-8"))
import layers, analysis as an
layers._load_ind_cache = lambda: ind_long
layers.get_market_kline = lambda *a, **k: mkt_long
import scan_daily

def aggregate_weekly(rows):
    weeks = {}
    for r in rows:
        wk = pd.Timestamp(r["date"]).to_period("W").start_time.strftime("%Y-%m-%d")
        if wk not in weeks:
            weeks[wk] = {"open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r["volume"], "date": r["date"]}
        else:
            w = weeks[wk]
            w["high"] = max(w["high"], r["high"]); w["low"] = min(w["low"], r["low"])
            w["close"] = r["close"]; w["volume"] += r["volume"]; w["date"] = r["date"]
    return [weeks[k] for k in sorted(weeks)]

def weekly_up(wk):
    if len(wk) < 21: return False
    cs = [w["close"] for w in wk]
    ma5=sum(cs[-5:])/5; ma10=sum(cs[-10:])/10; ma20=sum(cs[-20:])/20
    return bool(ma5>ma10>ma20 and cs[-1]>ma10)

def fwd_ret(rows, asof, n):
    dates=[r["date"] for r in rows]
    if asof not in dates: return None
    i=dates.index(asof)
    if i+n>=len(rows): return None
    return rows[i+n]["close"]/rows[i]["close"]-1

mkt_dates=[r["date"] for r in mkt_long if "2023-01-01"<=r["date"]<="2026-08-28"]
SCAN=mkt_dates[::5]
print("预聚合周线...", flush=True)
weekly_map = {c: aggregate_weekly(rows) for c, rows in klines.items()}
print("完成", flush=True)
print("扫描时点:", len(SCAN), flush=True)

def mkt_dir_simple(asof):
    rr=[r for r in mkt_long if r["date"]<=asof]
    if len(rr)<60: return "side"
    cs=[r["close"] for r in rr]
    m5=sum(cs[-5:])/5; m20=sum(cs[-20:])/20; m60=sum(cs[-60:])/60
    if m5>m20 and cs[-1]>m20 and cs[-1]>m60: return "up"
    if m5<m20 and cs[-1]<m20: return "down"
    return "side"

old_recs=[]; new_recs=[]
for k, asof in enumerate(SCAN):
    md = mkt_dir_simple(asof)
    for it in pool:
        rows = klines.get(it["code"], [])
        rr = [x for x in rows if x["date"] <= asof]
        if len(rr) < 70: continue
        df = pd.DataFrame(rr).tail(300)
        # 日线趋势(旧通道主判据)
        tr = an.analyze_trend(df, "日线")
        direction = tr.get("direction","sideways")
        # 周线多头(新通道主判据)
        wmap = weekly_map.get(it["code"])
        dw = [w["date"] for w in wmap] if wmap else []
        idx = bisect.bisect_right(dw, asof)
        wk = wmap[:idx] if wmap else []
        wk_up = weekly_up(wk)
        base = {"asof":asof,"mkt":md,"direction":direction,
                "fwd3":fwd_ret(rows,asof,3),"fwd7":fwd_ret(rows,asof,7),
                "fwd15":fwd_ret(rows,asof,15),"fwd30":fwd_ret(rows,asof,30),"fwd60":fwd_ret(rows,asof,60)}
        # ===== 旧通道: 日线up + 简化过滤(对应scan_daily旧逻辑的核心) =====
        if direction == "up":
            # 简化: 距60日高≥5%且gain60≤60(不过热/不衰竭的近似)
            hi60 = max(df["high"].tolist()[-60:])
            c = float(df["close"].iloc[-1])
            if c/hi60 <= 0.95 and (c/float(df["close"].iloc[-60])-1)*100 <= 60:
                old_recs.append(dict(base))
        # ===== 新通道: 周线多头 + 过滤 + 门槛 =====
        if wk_up:
            # 过滤: 过热(距60日高<8%/RSI>70) / 趋势阶段(gain60/bias/dist_hi)
            c = float(df["close"].iloc[-1])
            hi60 = max(df["high"].tolist()[-60:])
            if c/hi60 > 0.92:  # 距高<8% 过热
                pass
            elif len(df) >= 61 and (c/float(df["close"].iloc[-61])-1)*100 > 100:
                pass
            else:
                # 门槛: 周线多头 + (深回调 or 回调 or 共振)
                m5=sum(df["close"].tolist()[-5:])/5; m10=sum(df["close"].tolist()[-10:])/10; m20=sum(df["close"].tolist()[-20:])/20
                dd = direction == "down"
                side = direction == "side"
                reso = md == "up"
                if dd or side or reso:
                    new_recs.append(dict(base))
    if (k+1)%60==0: print(f"  {k+1}/{len(SCAN)} 旧={len(old_recs)} 新={len(new_recs)}", flush=True)

def wr(rs,f):
    vals=[r[f] for r in rs if r[f] is not None]
    if not vals: return None,None,0
    return sum(1 for v in vals if v>0)/len(vals)*100, sum(vals)/len(vals)*100, len(vals)
def rep(t,rs):
    parts=[]
    for f in ["fwd3","fwd7","fwd15","fwd30","fwd60"]:
        w,a,n=wr(rs,f)
        parts.append(f"{f[3:]}:{w:.0f}%({a:+.1f}%)" if w is not None else f"{f[3:]}:--")
    print(f"  {t:<24} n={len(rs):<5} "+"  ".join(parts))
print("\n===== 完整模拟结果 =====")
rep("旧通道(日线up主判据)", old_recs)
rep("新通道(周线多头主判据)", new_recs)
# 新通道细分子集
print("\n-- 新通道内部 --")
rep("新·深回调(down)", [r for r in new_recs if r["direction"]=="down"])
rep("新·回调(side)", [r for r in new_recs if r["direction"]=="side"])
