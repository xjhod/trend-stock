# -*- coding: utf-8 -*-
"""多起点稳健性验证: python3 research/scan_multi.py <起点日期>
建池(当时市值>=30亿) -> 行业指数 -> 扫描(断点续扫) -> 最优组合模拟"""
import sys, os, json, bisect, time, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASOF = sys.argv[1] if len(sys.argv)>1 else "2024-09-02"
END = "2026-08-28"
klines = json.load(open(os.path.join(BASE,"research","kline_market500.json"), encoding="utf-8"))
mkt_long = json.load(open(os.path.join(BASE,"research","mkt_long.json"), encoding="utf-8"))
a = {x["code"]: x for x in json.load(open(os.path.join(BASE,"bt_data","all_a.json"), encoding="utf-8"))}
import layers, analysis as an, scan_daily, env_judge
PL = os.path.join(BASE,"research",f"pool_{ASOF}.json")
IL = os.path.join(BASE,"research",f"ind_idx_{ASOF}.json")
SL = os.path.join(BASE,"research",f"sigs_{ASOF}.json")

# 1) 建池
if not os.path.exists(PL):
    rows=[]
    for code,rlist in klines.items():
        if not rlist: continue
        rlist=sorted(rlist,key=lambda r:r["date"]); pre=[r for r in rlist if r["date"]<=ASOF]
        if len(pre)<250: continue
        last=pre[-1]; cur=rlist[-1]; mv=a.get(code,{}).get("mv",0)
        if mv<=0: continue
        mv_asof=(mv/cur["close"])*last["close"]
        w60=pre[-60:]; amt60=sum(r["close"]*r["volume"] for r in w60)/60
        rets=[w60[i]["close"]/w60[i-1]["close"]-1 for i in range(1,len(w60))]
        vol60=statistics.pstdev(rets)*15.8 if len(rets)>5 else 0
        if mv_asof>=30e8 and amt60>=1e8 and 0.10<=vol60<=0.70:
            rows.append({"code":code,"name":a.get(code,{}).get("name",code),"ind":a.get(code,{}).get("ind",""),"mv_asof":mv_asof})
    json.dump(rows, open(PL,"w",encoding="utf-8"), ensure_ascii=False)
    print(f"建池 {ASOF}: {len(rows)}只", flush=True)
pool = json.load(open(PL, encoding="utf-8"))
# 2) 行业指数
if not os.path.exists(IL):
    indc = {}
    for r in pool: indc[r["ind"]] = indc.get(r["ind"],0)+1
    ind_cache={}
    for ind,n in sorted(indc.items(),key=lambda x:-x[1]):
        ms = sorted([r for r in pool if r["ind"]==ind], key=lambda r:-r["mv_asof"])[:15]
        series={}
        for m in ms:
            for r in klines.get(m["code"],[]):
                series.setdefault(r["date"],[]).append(r["close"])
        rows=[{"date":d,"close":sum(series[d])/len(series[d])} for d in sorted(series.keys()) if len(series[d])>=5]
        if len(rows)>=100: ind_cache[ind]=rows
    json.dump(ind_cache, open(IL,"w",encoding="utf-8"), ensure_ascii=False)
    print(f"行业指数 {len(ind_cache)}个", flush=True)
ind_long = json.load(open(IL, encoding="utf-8"))
layers._load_ind_cache = lambda: ind_long
layers.get_market_kline = lambda *a, **k: mkt_long

# 3) 扫描
def aggregate_weekly(rows):
    weeks={}
    for r in rows:
        wk=pd.Timestamp(r["date"]).to_period("W").start_time.strftime("%Y-%m-%d")
        if wk not in weeks: weeks[wk]={"high":r["high"],"low":r["low"],"close":r["close"],"date":r["date"]}
        else:
            w=weeks[wk]; w["high"]=max(w["high"],r["high"]); w["low"]=min(w["low"],r["low"]); w["close"]=r["close"]; w["date"]=r["date"]
    return [weeks[k] for k in sorted(weeks)]
def weekly_up(wk):
    if len(wk)<21: return False
    cs=[w["close"] for w in wk]
    return bool(sum(cs[-5:])/5>sum(cs[-10:])/10>sum(cs[-20:])/20 and cs[-1]>sum(cs[-10:])/10)
def ind_weekly(rows):
    weeks={}
    for r in rows:
        wk=pd.Timestamp(r["date"]).to_period("W").start_time.strftime("%Y-%m-%d")
        weeks[wk]=r["close"]
    return [weeks[k] for k in sorted(weeks)]
weekly_map={c:aggregate_weekly(rows) for c,rows in klines.items()}
def hist_scan(it, asof):
    code=it["code"]; ind=it.get("ind","")
    rows=[r for r in klines.get(code,[]) if r["date"]<=asof]
    if len(rows)<70: return None
    df=pd.DataFrame(rows).tail(300)
    trend=an.analyze_trend(df,"日线"); direction=trend.get("direction","sideways")
    last=df.iloc[-1]; close=float(last["close"]); chg=close/float(df.iloc[-2]["close"])-1
    hi60=max(df["high"].tolist()[-60:]); dd60=close/hi60-1
    mrr=[r for r in mkt_long if r["date"]<=asof]; mcl=[r["close"] for r in mrr]
    mkt_ok=True
    if len(mcl)>=65:
        if layers._direction(mcl)=="down": mkt_ok=False
        elif mcl[-1]<mcl[-6]*0.985: mkt_ok=False
    if not mkt_ok: return None
    pats=scan_daily.detect_bullish(df)
    irows=[r for r in ind_long.get(ind,[]) if r["date"]<=asof]; icl=[r["close"] for r in irows]
    ind_ok=True
    if len(icl)>=61 and layers._direction(icl)=="down": ind_ok=False
    stabilized=scan_daily._stabilized(df)
    if dd60<=-0.20 and pats and stabilized and ind_ok:
        best=-99; bg="weak"
        for pn,pi,pvc in pats:
            sc,lv,_=scan_daily._pattern_quality(df,pi,pn,pvc)
            if sc>best: best,bg=sc,lv
        if bg!="weak":
            return {"type":"rebound","level":1,"name":it.get("name",""),"code":code,"price":round(close,2),"chg":chg,"tags":["超跌企稳"]}
    wmap=weekly_map.get(code); dw=[w["date"] for w in wmap]; idx=bisect.bisect_right(dw,asof); wk=wmap[:idx]
    if not weekly_up(wk): return None
    if len(irows)<15: return None
    iw=ind_weekly(irows)
    if not (len(iw)>=10 and sum(iw[-5:])/5>sum(iw[-10:])/10 and iw[-1]>sum(iw[-10:])/10): return None
    over,_=scan_daily._overheated(df)
    if over: return None
    tm=scan_daily._trend_metrics(df)
    if tm is None: return None
    g60,bias,dist,days=tm
    if g60>100 or bias>20 or dist<5: return None
    if g60>60 or bias>10: return None
    if len(icl)>=61 and layers._direction(icl)=="down": return None
    if len(irows)>=25 and icl[-1]<sum(icl[-20:])/20: return None
    ig=icl[-1]/icl[-61]-1 if len(icl)>=61 else 0
    if ig>0.25: return None
    score=1
    if direction=="down": score+=1
    if pats: score+=1
    if score<2: return None
    return {"type":"trend","level":min(score,3),"name":it.get("name",""),"code":code,"price":round(close,2),"chg":chg,"tags":["周线趋势"]}
dates=sorted(set(r["date"] for r in mkt_long if ASOF<=r["date"]<=END))
daily_sigs={}
if os.path.exists(SL):
    try: daily_sigs=json.load(open(SL,encoding="utf-8"))
    except Exception: daily_sigs={}
t0=time.time(); nscan=0
for d in dates:
    if d in daily_sigs: continue
    sigs=[]
    for it in pool:
        nscan+=1
        r=hist_scan(it,d)
        if r: sigs.append(r)
    daily_sigs[d]=sigs
    json.dump(daily_sigs,open(SL,"w",encoding="utf-8"),ensure_ascii=False)
    if sigs: print(f"{d} 推荐{len(sigs)}只",flush=True)
print(f"扫描完成 覆盖到{dates[-1]} 总{nscan}次 耗时{time.time()-t0:.0f}s",flush=True)
