# -*- coding: utf-8 -*-
"""组合验证: 池子(393/467) x 离场(ma20/ma20双确认/stop10) x 环境门卫(开/关)"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
klines = json.load(open(os.path.join(BASE,"research","kline_market500.json"), encoding="utf-8"))
ind393 = json.load(open(os.path.join(BASE,"research","ind_idx_market500.json"), encoding="utf-8"))
ind467 = json.load(open(os.path.join(BASE,"research","ind_idx_30e.json"), encoding="utf-8"))
mkt_long = json.load(open(os.path.join(BASE,"research","mkt_long.json"), encoding="utf-8"))
import layers, env_judge
daily_sigs393 = json.load(open(os.path.join(BASE,"research","daily_sigs_2024.json"), encoding="utf-8"))
daily_sigs467 = json.load(open(os.path.join(BASE,"research","daily_sigs_2024_30e.json"), encoding="utf-8"))
dates = sorted(set(list(daily_sigs393.keys()) + list(daily_sigs467.keys())))

def op_on(code, d):
    rows=[r for r in klines.get(code,[]) if r["date"]>d]
    return rows[0]["open"] if rows else None

def sim(sigs, exit_mode, env_gate, ind_long):
    layers._load_ind_cache = lambda: ind_long
    layers.get_market_kline = lambda *a, **k: mkt_long
    cash=100000.0; holdings={}; closed=[]
    for d in dates:
        ss=sigs.get(d,[])
        # 离场
        mrr=[r for r in mkt_long if r["date"]<=d]; mcl=[r["close"] for r in mrr]
        mkt_down=(len(mcl)>=5 and layers._direction(mcl)=="down")
        for code in list(holdings.keys()):
            h=holdings[code]
            rows=[r for r in klines.get(code,[]) if r["date"]<=d]
            if not rows: continue
            px=rows[-1]["close"]; h["price"]=px; h["high"]=max(h["high"],px); h["days"]+=1
            ma20=sum(r["close"] for r in rows[-20:])/20
            ex,reason=None,""
            if exit_mode=="ma20":
                if px<ma20: ex,reason=px,"破MA20"
            elif exit_mode=="ma20conf":
                if px<ma20 and mkt_down: ex,reason=px,"破MA20+大盘弱"
            elif exit_mode=="stop10":
                if px<=h["high"]*0.90: ex,reason=px,"移动止损"
            elif exit_mode=="hold60":
                if h["days"]>=60: ex,reason=px,"60日"
            if ex:
                cash+=ex*h["qty"]; closed.append({**h,"exit":ex,"reason":reason,"ret":ex/h["entry"]-1})
                del holdings[code]
        # 环境门卫
        if env_gate:
            act=env_judge.env_action(asof=d)
            if act["action"]=="filter_out": continue
        # 买入(次日开盘, 前5只, 每仓2万)
        for s in ss[:5]:
            if s["code"] in holdings: continue
            op=op_on(s["code"],d)
            if op is None or cash<20000: continue
            qty=int(20000/op)
            if qty<=0: continue
            cash-=qty*op
            holdings[s["code"]]={"name":s["name"],"type":s["type"],"entry":op,"entry_d":d,"qty":qty,"high":op,"price":op,"days":0}
    final=cash
    for code,h in holdings.items():
        rows=[r for r in klines.get(code,[]) if r["date"]<=dates[-1]]
        p=rows[-1]["close"] if rows else h["price"]; final+=p*h["qty"]
    wins=sum(1 for c in closed if c["ret"]>0)
    big=sum(1 for c in closed if c["ret"]>0.5)
    return {"final":final,"ret":final/100000-1,"n":len(closed),"win":wins/len(closed)*100 if closed else 0,"big":big}

print("池子     离场      环境门卫  总收益     卖出  胜率   +50%赢家")
print("-"*70)
tests = [
    ("393池","stop10","开",daily_sigs393,ind393),
    ("467池","stop10","开",daily_sigs467,ind467),
    ("393池","ma20","开",daily_sigs393,ind393),
    ("467池","ma20","开",daily_sigs467,ind467),
    ("467池","ma20","关",daily_sigs467,ind467),
    ("467池","ma20conf","开",daily_sigs467,ind467),
    ("467池","hold60","开",daily_sigs467,ind467),
    ("467池","hold60","关",daily_sigs467,ind467),
]
for pooln, em, eg, sigs, indl in tests:
    r = sim(sigs, em, eg, indl)
    print(f"{pooln}  {em:<10} {eg:<4} {r['ret']*100:+7.1f}%  {r['n']:>4}笔  {r['win']:>4.0f}%  {r['big']}笔  最终{r['final']:.0f}元")
