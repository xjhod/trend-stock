# -*- coding: utf-8 -*-
"""新标准(周线多头主判据+行业周线多头)扫描2024-2025, 统计完整选股表现"""
import sys, os, json, bisect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
klines = json.load(open(os.path.join(BASE,"research","kline_cache_long.json"), encoding="utf-8"))
ind_long = json.load(open(os.path.join(BASE,"research","ind_idx_long.json"), encoding="utf-8"))
mkt_long = json.load(open(os.path.join(BASE,"research","mkt_long.json"), encoding="utf-8"))
pool = json.load(open(os.path.join(BASE,"highfit_pool.json"), encoding="utf-8"))
import analysis as an
from scan_daily import _overheated, _trend_metrics
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
    return bool(sum(cs[-5:])/5 > sum(cs[-10:])/10 > sum(cs[-20:])/20 and cs[-1] > sum(cs[-10:])/10)
def ind_weekly(rows):
    weeks = {}
    for r in rows:
        wk = pd.Timestamp(r["date"]).to_period("W").start_time.strftime("%Y-%m-%d")
        weeks[wk] = r["close"]
    return [weeks[k] for k in sorted(weeks)]
def fwd_ret(rows, asof, n):
    dates=[r["date"] for r in rows]
    if asof not in dates: return None
    i=dates.index(asof)
    if i+n>=len(rows): return None
    return rows[i+n]["close"]/rows[i]["close"]-1
weekly_map = {c: aggregate_weekly(rows) for c, rows in klines.items()}
mkt_dates=[r["date"] for r in mkt_long if "2024-01-01"<=r["date"]<="2025-12-31"]
SCAN=mkt_dates[::5]
print("扫描时点数:", len(SCAN), "范围:", SCAN[0], "~", SCAN[-1], flush=True)
recs=[]
for k, asof in enumerate(SCAN):
    for it in pool:
        rows = klines.get(it["code"], [])
        rr = [x for x in rows if x["date"] <= asof]
        if len(rr) < 70: continue
        df = pd.DataFrame(rr).tail(300)
        wmap = weekly_map.get(it["code"])
        dw = [w["date"] for w in wmap] if wmap else []
        idx = bisect.bisect_right(dw, asof)
        wk = wmap[:idx] if wmap else []
        if not weekly_up(wk): continue
        over,_ = _overheated(df)
        if over: continue
        tm = _trend_metrics(df)
        if tm is None: continue
        g60,bias,dist,days = tm
        if g60>100 or bias>20 or dist<5: continue
        if g60>60 or bias>10: continue
        tr = an.analyze_trend(df,"日线")
        direction = tr.get("direction","sideways")
        score = 1 + (1 if direction=="down" else 0)
        if score < 2: continue
        # 行业周线多头过滤
        irows = [r for r in ind_long.get(it.get("ind",""), []) if r["date"]<=asof]
        if len(irows) < 15: continue
        iw = ind_weekly(irows)
        if not (len(iw)>=10 and sum(iw[-5:])/5 > sum(iw[-10:])/10 and iw[-1] > sum(iw[-10:])/10): continue
        recs.append({"asof":asof,"code":it["code"],"name":it.get("name",""),"ind":it.get("ind",""),"dir":direction,
            "fwd5":fwd_ret(rows,asof,5),"fwd15":fwd_ret(rows,asof,15),"fwd60":fwd_ret(rows,asof,60)})
    if (k+1)%50==0: print(f"  {k+1}/{len(SCAN)} 已收集{len(recs)}", flush=True)
print(f"\n总推荐: {len(recs)}")
json.dump(recs, open(os.path.join(BASE,"research","hits_2024_2025.json"),"w",encoding="utf-8"), ensure_ascii=False)
def stat(rs, key):
    vals=[r[key] for r in rs if r[key] is not None]
    if not vals: return None,None,0
    return sum(1 for v in vals if v>0)/len(vals)*100, sum(vals)/len(vals)*100, len(vals)
def rep(t, rs):
    w5,a5,_=stat(rs,"fwd5"); w15,a15,_=stat(rs,"fwd15"); w60,a60,n=stat(rs,"fwd60")
    print(f"  {t:<26} n={len(rs):<5} 5日:{w5:.0f}%({a5:+.1f}%) 15日:{w15:.0f}%({a15:+.1f}%) 60日:{w60:.0f}%({a60:+.1f}%)")
print("== 分年 ==")
rep("2024全年", [r for r in recs if r["asof"]<"2025-01-01"])
rep("2025全年", [r for r in recs if r["asof"]>="2025-01-01"])
print("== 分季度 ==")
for q in ["2024-Q1","2024-Q2","2024-Q3","2024-Q4","2025-Q1","2025-Q2","2025-Q3","2025-Q4"]:
    y,m = q.split("-Q")
    ms = [r for r in recs if r["asof"][:4]==y and int(r["asof"][5:7]) in range((int(m)-1)*3+1, int(m)*3+1)]
    rep(q, ms)
