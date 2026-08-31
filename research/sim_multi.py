# -*- coding: utf-8 -*-
"""多起点最优组合模拟: python3 research/sim_multi.py <起点>
最优组合 = 30亿池 + 破MA20+大盘弱双确认离场 + 环境门卫/动态仓位"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START = sys.argv[1] if len(sys.argv)>1 else "2024-09-02"
klines = json.load(open(os.path.join(BASE,"research","kline_market500.json"), encoding="utf-8"))
ind_long = json.load(open(os.path.join(BASE,"research",f"ind_idx_{START}.json"), encoding="utf-8"))
mkt_long = json.load(open(os.path.join(BASE,"research","mkt_long.json"), encoding="utf-8"))
import layers, env_judge
layers._load_ind_cache = lambda: ind_long
layers.get_market_kline = lambda *a, **k: mkt_long
daily_sigs = json.load(open(os.path.join(BASE,"research",f"sigs_{START}.json"), encoding="utf-8"))
dates = sorted(daily_sigs.keys())

def op_on(code, d):
    rows=[r for r in klines.get(code,[]) if r["date"]>d]
    return rows[0]["open"] if rows else None
def sim(exit_mode, env_gate=True, max_new=5, hold_n=60):
    cash=100000.0; holdings={}; closed=[]
    for d in dates:
        ss=daily_sigs.get(d,[])
        mrr=[r for r in mkt_long if r["date"]<=d]; mcl=[r["close"] for r in mrr]
        mkt_down=(len(mcl)>=5 and layers._direction(mcl)=="down")
        for code in list(holdings.keys()):
            h=holdings[code]
            rows=[r for r in klines.get(code,[]) if r["date"]<=d]
            if not rows: continue
            px=rows[-1]["close"]; h["price"]=px; h["high"]=max(h["high"],px); h["days"]+=1
            ma20=sum(r["close"] for r in rows[-20:])/20
            ex,reason=None,""
            if exit_mode=="ma20conf":
                if px<ma20 and mkt_down: ex,reason=px,"破MA20+大盘弱"
            elif exit_mode=="ma20":
                if px<ma20: ex,reason=px,"破MA20"
            elif exit_mode=="stop10":
                if px<=h["high"]*0.90: ex,reason=px,"移动止损"
            elif exit_mode=="hold60":
                if h["days"]>=60: ex,reason=px,"60日"
            if ex:
                cash+=ex*h["qty"]; closed.append({**h,"exit":ex,"reason":reason,"ret":ex/h["entry"]-1})
                del holdings[code]
        if env_gate:
            act=env_judge.env_action(asof=d)
            if act["action"]=="filter_out": continue
        for s in ss[:max_new]:
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
    return {"final":final,"ret":final/100000-1,"n":len(closed),"win":wins/len(closed)*100 if closed else 0,
            "big":big,"closed":closed,"hold":holdings}

print(f"===== 起点 {START} ~ {dates[-1]} ({len(dates)}交易日) =====")
for em,lab in [("stop10","-10%移动止损"),("ma20","破MA20"),("ma20conf","破MA20+大盘弱双确认"),("hold60","持有60日")]:
    r=sim(em)
    print(f"  {lab:<12}: {r['ret']*100:+7.1f}%  卖出{r['n']}笔  胜率{r['win']:.0f}%  +50%赢家{r['big']}笔  最终{r['final']:.0f}元")
r=sim("ma20conf")
print("\n  最优组合大赢家:")
for c in sorted(r["closed"],key=lambda x:-x["ret"])[:10]:
    print(f"    {c['name']} 买{c['entry_d']} {c['ret']*100:+.0f}%")
