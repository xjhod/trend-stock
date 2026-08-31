# -*- coding: utf-8 -*-
"""环境评分 v2 变体: 对比不同因子组合, 找稳健的环境过滤"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util
import pandas as pd
import layers
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
klines = json.load(open(os.path.join(BASE,"research","kline_cache_long.json"), encoding="utf-8"))
ind_long = json.load(open(os.path.join(BASE,"research","ind_idx_long.json"), encoding="utf-8"))
mkt_long = json.load(open(os.path.join(BASE,"research","mkt_long.json"), encoding="utf-8"))
bk = json.load(open(os.path.join(BASE,"research","breadth_klines.json"), encoding="utf-8"))
pool = json.load(open(os.path.join(BASE,"highfit_pool.json"), encoding="utf-8"))
layers._load_ind_cache = lambda: ind_long
layers.get_market_kline = lambda *a, **k: mkt_long
spec = importlib.util.spec_from_file_location("fb", os.path.join(BASE,"research","full_backtest.py"))
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)

def env_metrics(asof):
    rr = [r for r in mkt_long if r["date"] <= asof]
    closes = [r["close"] for r in rr]
    if len(closes) < 260: return None
    c = closes[-1]
    d = layers._direction(closes)
    dd250 = (c/max(closes[-250:])-1)*100
    mom20 = (c/closes[-21]-1)*100
    up20=total=0; newhi=0
    for code, rows in bk.items():
        rr2 = [r for r in rows if r["date"] <= asof]
        if len(rr2) < 65: continue
        total += 1
        if rr2[-1]["close"] > rr2[-21]["close"]: up20 += 1
        if rr2[-1]["close"] >= max(r["close"] for r in rr2[-60:]): newhi += 1
    if total == 0: return None
    return {"dir": d, "dd250": dd250, "mom20": mom20,
            "b_up": up20/total*100, "b_new": newhi/total*100}

# 变体判定函数: 返回 True=允许买入
def gate_v1(m):   # 原4因子>=4 (含动量限制)
    s = {"up":2,"side":1,"down":0}[m["dir"]]
    s += 1 if -15 <= m["dd250"] <= -1.5 else 0
    s += 1 if (m["b_up"]>55 and m["b_new"]>6) else 0
    s += 1 if 0 <= m["mom20"] <= 6 else 0
    return s >= 4
def gate_v2(m):   # 趋势up + 广度 (无动量/位置限制)
    return m["dir"]=="up" and m["b_up"]>55 and m["b_new"]>6
def gate_v3(m):   # 趋势up + 广度 + 位置非顶(放宽到-1%)
    return m["dir"]=="up" and m["b_up"]>55 and m["b_new"]>6 and m["dd250"] < -1
def gate_v4(m):   # 趋势非down + 广度(较弱)
    return m["dir"]!="down" and m["b_up"]>50 and m["b_new"]>5
def gate_v5(m):   # 仅趋势up (现有门卫强化)
    return m["dir"]=="up"

def run(start, end, gatefn):
    mkt_dates = [r["date"] for r in mkt_long if start <= r["date"] <= end]
    cash=100000.0; positions=[]; trades=[]
    for asof in mkt_dates:
        gate, sysweak, mkt_dir = fb.mkt_snapshot(asof)
        for p in positions[:]:
            rr=[x for x in klines.get(p["code"],[]) if x["date"]<=asof]
            if not rr: continue
            px=rr[-1]["close"]; p["hi"]=max(p["hi"],px)
            reason=None
            c20=sum(x["close"] for x in rr[-21:-1])/20 if len(rr)>=21 else px
            if px<=p["hi"]*0.90: reason="止损"
            elif sysweak:
                if px<=p["entry"] or px<c20: reason="清弱势"
            elif px<c20 and mkt_dir=="down": reason="双确认"
            if reason:
                trades.append((px/p["entry"]-1)*100)
                cash+=px*p["qty"]; positions.remove(p)
        m = env_metrics(asof)
        if m and gatefn(m): gate = gate and True
        elif m: gate = False
        if gate and len(positions)<5:
            recs=[]
            for it in pool:
                rr=[x for x in klines.get(it["code"],[]) if x["date"]<=asof]
                if len(rr)<60: continue
                r=fb.judge(it, pd.DataFrame(rr), asof, mkt_dir)
                if r: recs.append((it,r))
            recs.sort(key=lambda x:-x[1][1])
            for it,(typ,lv,tags) in recs:
                if len(positions)>=5 or cash<20000: break
                rows=klines.get(it["code"],[]); dates=[r["date"] for r in rows]
                idx=next((k for k,d in enumerate(dates) if d>asof),None)
                if idx is None: continue
                opx=rows[idx]["open"]
                if opx<=0 or any(p["code"]==it["code"] for p in positions): continue
                qty=int(20000/opx)
                if qty<=0: continue
                cash-=qty*opx
                positions.append({"code":it["code"],"name":it.get("name",""),"entry":opx,"entry_date":dates[idx],"qty":qty,"hi":opx})
    final=cash+sum(klines.get(p["code"],[])[-1]["close"]*p["qty"] for p in positions)
    w=sum(1 for t in trades if t>0)
    return {"ret":(final/100000-1)*100,"n":len(trades),"wr":(w/len(trades)*100 if trades else 0)}

GATES = {"v1原4因子>=4":gate_v1, "v2趋势up+广度":gate_v2, "v3+位置非顶":gate_v3,
         "v4趋势非down+广度弱":gate_v4, "v5仅趋势up":gate_v5}
SEGS = [("2023-2024","2023-01-03","2024-12-31"),("2025H1","2025-01-06","2025-06-30"),
        ("2025H2","2025-07-01","2025-12-31"),("2026H1","2026-01-01","2026-08-28")]
print("== 无过滤基准 ==")
for name, s, e in SEGS:
    r = run(s,e,lambda m: True)
    print(f"  {name}: +{r['ret']:.1f}% ({r['n']}笔, 胜率{r['wr']:.0f}%)")
for gname, fn in GATES.items():
    print(f"\n== {gname} ==")
    for name, s, e in SEGS:
        r = run(s,e,fn)
        print(f"  {name}: +{r['ret']:.1f}% ({r['n']}笔, 胜率{r['wr']:.0f}%)")
