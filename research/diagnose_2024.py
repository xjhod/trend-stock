# -*- coding: utf-8 -*-
"""2024-2026回测归因: 买点质量 / 卖点(卖飞) / 信号分层 / 漏掉大牛股"""
import sys, os, json, bisect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
klines = json.load(open(os.path.join(BASE,"research","kline_market500.json"), encoding="utf-8"))
ind_long = json.load(open(os.path.join(BASE,"research","ind_idx_market500.json"), encoding="utf-8"))
mkt_long = json.load(open(os.path.join(BASE,"research","mkt_long.json"), encoding="utf-8"))
import layers, env_judge
layers._load_ind_cache = lambda: ind_long
layers.get_market_kline = lambda *a, **k: mkt_long
daily_sigs = json.load(open(os.path.join(BASE,"research","daily_sigs_2024.json"), encoding="utf-8"))
dates = sorted(daily_sigs.keys())

def px_on(code, d):
    rows = [r for r in klines.get(code, []) if r["date"] <= d]
    return rows[-1]["close"] if rows else None

# ===== 复现auto交易, 记录每笔入场特征 =====
trades = []  # 每笔: entry_d, code, name, type, entry, exit_d, exit, ret, reason, dd60_at_entry
cash = 100000.0; holdings = {}; closed = []
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
        holdings[s["code"]] = {"name":s["name"],"type":s["type"],"entry":s["price"],"entry_d":d,
            "qty":qty,"high":s["price"],"price":s["price"],"dd60": s["price"]/hi60-1,
            "chg5": s["chg"]}
    for c in closed:
        # 补入场特征(离场时已删, 从holdings取不到的补在买入时记录) - 简化: 买入时记trades
        pass
# 重新跑一遍记录trades
trades = []
cash = 100000.0; holdings = {}; closed = []
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
        holdings[s["code"]] = {"name":s["name"],"type":s["type"],"entry":s["price"],"entry_d":d,
            "qty":qty,"high":s["price"],"price":s["price"],"dd60":dd60}
        trades.append({"code":s["code"],"name":s["name"],"type":s["type"],"entry_d":d,"entry":s["price"],"dd60":dd60})

# ===== A. 买点质量: 入场时dd60分布 + 入场后20日表现 =====
print("===== A. 买点质量 =====")
import statistics
ddlist = [t["dd60"] for t in trades]
print(f"共{trades.__len__()}次买入(去重持仓), 入场时距60日高点回撤分布:")
for lo,hi,lab in [(-1.0,-0.3,"深跌>30%"),(-0.3,-0.15,"跌15-30%"),(-0.15,-0.05,"跌5-15%"),(-0.05,0.0,"接近高点<5%"),(0.0,0.5,"创近期新高")]:
    n = sum(1 for x in ddlist if lo < x <= hi)
    print(f"  {lab}: {n}次 ({n/max(len(ddlist),1)*100:.0f}%)")
# 入场后20日表现
fwd = []
for t in trades:
    rows = [r for r in klines.get(t["code"], []) if t["entry_d"] < r["date"]]
    if len(rows) >= 20: fwd.append((t["dd60"], rows[19]["close"]/t["entry"]-1))
if fwd:
    for lo,hi,lab in [(-1.0,-0.3,"深跌>30%"),(-0.3,-0.15,"跌15-30%"),(-0.15,-0.05,"跌5-15%"),(-0.05,0.5,"接近高点/新高")]:
        sub = [x for x in fwd if lo < x[0] <= hi]
        if sub:
            avg = sum(x[1] for x in sub)/len(sub)
            print(f"  入场{lab}组: {len(sub)}次, 入场后20日均值{avg*100:+.1f}%, 20日后上涨率{sum(1 for x in sub if x[1]>0)/len(sub)*100:.0f}%")
    all20 = sum(x[1] for x in fwd)/len(fwd)
    print(f"  全部入场后20日均值: {all20*100:+.1f}%")

# ===== B. 卖点质量(卖飞): 离场后20/60日表现 =====
print("\n===== B. 卖点质量(卖飞分析) =====")
for c in closed:
    rows = [r for r in klines.get(c["code"], []) if c["exit_d"] < r["date"]]
    c["f20"] = rows[19]["close"]/c["exit"]-1 if len(rows)>=20 else None
    c["f60"] = rows[59]["close"]/c["exit"]-1 if len(rows)>=60 else None
    c["f120"] = rows[119]["close"]/c["exit"]-1 if len(rows)>=120 else None
for label, key in [("20日","f20"),("60日","f60"),("120日","f120")]:
    sub = [c for c in closed if c.get(key) is not None]
    if sub:
        avg = sum(c[key] for c in sub)/len(sub)
        big = sum(1 for c in sub if c[key] > 0.2)
        print(f"  离场后{label}: 均值{avg*100:+.1f}%, 涨超20%的有{big}/{len(sub)}笔(卖飞)")
# 按离场原因分
for reason in ["移动止损-10%","破MA20+大盘弱","止损-10%"]:
    sub = [c for c in closed if c["reason"]==reason and c.get("f60") is not None]
    if sub:
        avg = sum(c["f60"] for c in sub)/len(sub)
        big = sum(1 for c in sub if c["f60"]>0.2)
        print(f"  [{reason}] {len(sub)}笔, 离场后60日均值{avg*100:+.1f}%, 涨超20%: {big}笔")
# 卖飞最严重的
print("  卖飞最严重(离场后60日涨幅前8):")
for c in sorted([c for c in closed if c.get("f60") is not None], key=lambda x:-x["f60"])[:8]:
    print(f"    {c['name']} 卖{c['exit_d']}@{c['exit']:.2f}({c['ret']*100:+.1f}%) 后续60日{c['f60']*100:+.1f}%")

# ===== C. 信号类型/星级的胜率 =====
print("\n===== C. 信号分层(入场后20日) =====")
from collections import defaultdict
buck = defaultdict(list)
for t in trades:
    rows = [r for r in klines.get(t["code"], []) if t["entry_d"] < r["date"]]
    if len(rows) >= 20:
        buck[t["type"]].append(rows[19]["close"]/t["entry"]-1)
for k,v in buck.items():
    print(f"  {k}: {len(v)}次 20日均值{sum(v)/len(v)*100:+.1f}% 上涨率{sum(1 for x in v if x>0)/len(v)*100:.0f}%")

# ===== D. 漏掉的大牛股: 池内2024-09后涨幅top, 规则推荐次数 =====
print("\n===== D. 漏掉的大牛股 =====")
# 计算每只在2024-09-02到2026-08-28的涨幅
best = []
for code, rows in klines.items():
    rl = [r for r in rows if "2024-09-02" <= r["date"] <= "2026-08-28"]
    if len(rl) < 50: continue
    gain = rl[-1]["close"]/rl[0]["close"]-1
    # 规则推荐次数
    cnt = sum(1 for d in dates if any(s["code"]==code for s in daily_sigs.get(d,[])))
    best.append({"code":code,"name":rl[-1].get("name",""),"gain":gain,"rec":cnt})
best.sort(key=lambda x:-x["gain"])
print("2024-09后涨幅top25 & 规则推荐次数:")
for b in best[:25]:
    print(f"  {b['name']:<6} 涨幅{b['gain']*100:+6.0f}%  推荐{b['rec']}次")

# 推荐次数与涨幅相关性(粗看高推荐是否对应高涨幅)
import math
recs = [b for b in best if b["rec"]>0]
print(f"\n被推荐过的股票{len(recs)}只, 其涨幅均值{sum(b['gain'] for b in recs)/len(recs)*100:.0f}%")
norec = [b for b in best if b["rec"]==0]
print(f"从未推荐{len(norec)}只, 其涨幅均值{sum(b['gain'] for b in norec)/len(norec)*100:.0f}%")
top30 = best[:30]
print(f"涨幅top30中, 从未被推荐的: {sum(1 for b in top30 if b['rec']==0)}只")
for b in top30:
    if b["rec"]==0:
        print(f"   漏掉: {b['name']} 涨幅{b['gain']*100:.0f}%")
