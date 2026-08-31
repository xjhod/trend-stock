# -*- coding: utf-8 -*-
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
klines = json.load(open(os.path.join(BASE,"research","kline_market500.json"), encoding="utf-8"))
ind_long = json.load(open(os.path.join(BASE,"research","ind_idx_market500.json"), encoding="utf-8"))
mkt_long = json.load(open(os.path.join(BASE,"research","mkt_long.json"), encoding="utf-8"))
import layers, env_judge
layers._load_ind_cache = lambda: ind_long
layers.get_market_kline = lambda *a, **k: mkt_long
daily_sigs = json.load(open(os.path.join(BASE,"research","daily_sigs_2024.json"), encoding="utf-8"))
dates = sorted(daily_sigs.keys())

# 单次模拟auto, 记录入场特征
trades = []; closed = []
cash = 100000.0; holdings = {}
for d in dates:
    sigs = daily_sigs.get(d, [])
    act = env_judge.env_action(asof=d)
    if act["action"] == "filter_out": continue
    avail_pct = act["pos_pct"]
    mrr = [r for r in mkt_long if r["date"] <= d]
    mcl = [r["close"] for r in mrr]
    mkt_down = (len(mcl)>=5 and layers._direction(mcl)=="down")
    for code in list(holdings.keys()):
        h = holdings[code]
        rows = [r for r in klines.get(code, []) if r["date"] <= d]
        if not rows: continue
        px = rows[-1]["close"]
        h["price"] = px; h["high"] = max(h["high"], px)
        exit_px, reason = None, ""
        if px <= h["high"]*0.90: exit_px, reason = px, "移动止损-10%"
        elif mkt_down:
            ma20 = sum(r["close"] for r in rows[-20:])/20
            if px < ma20: exit_px, reason = px, "破MA20+大盘弱"
        elif px < h["entry"]*0.9: exit_px, reason = px, "止损-10%"
        if exit_px:
            cash += exit_px*h["qty"]
            ret = exit_px/h["entry"]-1
            closed.append({**h,"exit_d":d,"exit":exit_px,"ret":ret,"reason":reason})
            del holdings[code]
    buy_list = sigs[:5]
    alloc = 100000.0*(avail_pct/100.0)/5
    for s in buy_list:
        if s["code"] in holdings: continue
        if cash < alloc: continue
        qty = int(alloc/s["price"])
        if qty <= 0: continue
        cash -= qty*s["price"]
        rows = [r for r in klines.get(s["code"], []) if r["date"] <= d]
        hi60 = max(r["high"] for r in rows[-60:]) if len(rows)>=60 else s["price"]
        dd60 = s["price"]/hi60-1
        holdings[s["code"]] = {"code":s["code"],"name":s["name"],"type":s["type"],"entry":s["price"],"entry_d":d,
            "qty":qty,"high":s["price"],"price":s["price"],"dd60":dd60}
        trades.append({"code":s["code"],"name":s["name"],"type":s["type"],"entry_d":d,"entry":s["price"],"dd60":dd60})

def fwd(code, ref_d, ref_px, n):
    rows = [r for r in klines.get(code, []) if r["date"] > ref_d]
    if len(rows) >= n: return rows[n-1]["close"]/ref_px-1
    if rows: return rows[-1]["close"]/ref_px-1
    return None

# A. 买点质量
print("===== A. 买点质量 (59次买入) =====")
buck = [(-1.0,-0.3,"深跌>30%"),(-0.3,-0.15,"跌15-30%"),(-0.15,-0.05,"跌5-15%"),(-0.05,0.5,"接近高点/新高")]
for lo,hi,lab in buck:
    sub = [t for t in trades if lo < t["dd60"] <= hi]
    if not sub: continue
    f20 = [x for x in [fwd(t["code"],t["entry_d"],t["entry"],20) for t in sub] if x is not None]
    f60 = [x for x in [fwd(t["code"],t["entry_d"],t["entry"],60) for t in sub] if x is not None]
    print(f"  {lab}: {len(sub)}次, 20日均值{sum(f20)/len(f20)*100:+.1f}%涨{sum(1 for x in f20 if x>0)/len(f20)*100:.0f}% | 60日均值{sum(f60)/len(f60)*100:+.1f}%涨{sum(1 for x in f60 if x>0)/len(f60)*100:.0f}%")
all20 = [x for x in [fwd(t["code"],t["entry_d"],t["entry"],20) for t in trades] if x is not None]
print(f"  全部: 20日均值{sum(all20)/len(all20)*100:+.1f}%")

# B. 卖点质量
print("\n===== B. 卖点质量 (卖飞分析) =====")
for c in closed:
    c["f20"]=fwd(c["code"],c["exit_d"],c["exit"],20); c["f60"]=fwd(c["code"],c["exit_d"],c["exit"],60); c["f120"]=fwd(c["code"],c["exit_d"],c["exit"],120)
for label,key in [("20日","f20"),("60日","f60"),("120日","f120")]:
    sub=[c for c in closed if c.get(key) is not None]
    avg=sum(c[key] for c in sub)/len(sub)
    big=sum(1 for c in sub if c[key]>0.2)
    print(f"  离场后{label}: 均值{avg*100:+.1f}%, 涨超20% {big}/{len(sub)}笔")
for reason in ["移动止损-10%","破MA20+大盘弱","止损-10%"]:
    sub=[c for c in closed if c["reason"]==reason and c.get("f60") is not None]
    if sub:
        avg=sum(c["f60"] for c in sub)/len(sub); big=sum(1 for c in sub if c["f60"]>0.2)
        print(f"  [{reason}] {len(sub)}笔, 离场后60日均值{avg*100:+.1f}%, 涨超20%:{big}笔")
print("  卖飞最严重(离场后60日):")
for c in sorted([c for c in closed if c.get("f60") is not None], key=lambda x:-x["f60"])[:8]:
    print(f"    {c['name']} 卖{c['exit_d']}({c['ret']*100:+.1f}%) 后续60日{c['f60']*100:+.1f}%")
print(f"  持有到现在的收益均值: {sum(c['f120'] for c in closed if c.get('f120') is not None)/len([c for c in closed if c.get('f120') is not None])*100:.1f}% (120日后)")

# C. 信号分层
print("\n===== C. 信号分层(入场后20日) =====")
from collections import defaultdict
buck2 = defaultdict(list)
for t in trades:
    f = fwd(t["code"],t["entry_d"],t["entry"],20)
    if f is not None: buck2[t["type"]].append(f)
for k,v in buck2.items():
    print(f"  {k}: {len(v)}次 20日均值{sum(v)/len(v)*100:+.1f}% 上涨率{sum(1 for x in v if x>0)/len(v)*100:.0f}%")

# D. 漏掉大牛股
print("\n===== D. 漏掉的大牛股 (2024-09后涨幅top25) =====")
best=[]
for code,rows in klines.items():
    rl=[r for r in rows if "2024-09-02" <= r["date"] <= "2026-08-28"]
    if len(rl)<50: continue
    gain=rl[-1]["close"]/rl[0]["close"]-1
    cnt=sum(1 for d in dates if any(s["code"]==code for s in daily_sigs.get(d,[])))
    best.append({"code":code,"name":rl[-1].get("name",""),"gain":gain,"rec":cnt})
best.sort(key=lambda x:-x["gain"])
for b in best[:25]:
    mark=" ★漏掉" if b["rec"]==0 else ""
    print(f"  {b['name']:<8} 涨幅{b['gain']*100:+6.0f}%  推荐{b['rec']}次{mark}")
recs=[b for b in best if b["rec"]>0]; norec=[b for b in best if b["rec"]==0]
print(f"\n  被推荐过{len(recs)}只 涨幅均值{sum(b['gain'] for b in recs)/len(recs)*100:.0f}%")
print(f"  从未推荐{len(norec)}只 涨幅均值{sum(b['gain'] for b in norec)/len(norec)*100:.0f}%")
print(f"  涨幅top30中从未被推荐: {sum(1 for b in best[:30] if b['rec']==0)}只")
for b in best[:30]:
    if b["rec"]==0: print(f"    漏掉: {b['name']} {b['gain']*100:.0f}%")
