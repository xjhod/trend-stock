# -*- coding: utf-8 -*-
"""胜率改善变体回测: 同一回测框架, 换离场规则"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util
spec = importlib.util.spec_from_file_location("fb", "research/full_backtest.py")
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)

# 复用fb的数据预加载与交易日, 但替换离场逻辑
pool = fb.load_pool()
klines = fb.preload_kline(pool)
mkt_all = [r["date"] for r in fb.layers.get_market_kline(400)]
trade_days = [d for d in mkt_all if d >= "2025-01-01"]

def run(stop_mode, label):
    """stop_mode: 'pct10' 固定-10% | 'breakeven' 保本追踪 | 'pct7' 固定-7%"""
    cash, positions, trades = 100000.0, [], []
    for asof in trade_days:
        gate, sysweak, mkt_dir = fb.mkt_snapshot(asof)
        new_pos = []
        for p in positions:
            rows = klines.get(p["code"])
            if not rows: new_pos.append(p); continue
            rr = [x for x in rows if x["date"] <= asof]
            if not rr: new_pos.append(p); continue
            px = rr[-1]["close"]
            p["hi"] = max(p["hi"], px); p["days"] += 1
            reason = None
            c20 = sum(x["close"] for x in rr[-21:-1])/20 if len(rr) >= 21 else px
            if stop_mode == "pct10":
                if px <= p["hi"]*0.90: reason = "移动止损-10%"
            elif stop_mode == "pct7":
                if px <= p["hi"]*0.93: reason = "移动止损-7%"
            elif stop_mode == "pct5":
                if px <= p["hi"]*0.95: reason = "移动止损-5%"
            elif stop_mode == "pct8":
                if px <= p["hi"]*0.92: reason = "移动止损-8%"
            elif stop_mode == "breakeven":
                # 曾达+10%浮盈 → 止损线上移到成本(保本); 未达 → -10%移动止损
                if p["hi"] >= p["entry"]*1.10:
                    if px <= p["entry"]*1.00: reason = "保本止损(曾+10%回落)"
                else:
                    if px <= p["hi"]*0.90: reason = "移动止损-10%"
            if reason is None:
                if sysweak:
                    if px <= p["entry"] or px < c20: reason = "系统性转弱·清弱势"
                else:
                    if px < c20 and mkt_dir == "down": reason = "破MA20+大盘弱"
            if reason:
                trades.append({"code":p["code"],"name":p["name"],"entry_date":p["entry_date"],
                               "exit_date":asof,"ret_pct":round((px/p["entry"]-1)*100,2),
                               "reason":reason,"profit":round(px*p["qty"]-p["cost"],2)})
                cash += px*p["qty"]
            else:
                new_pos.append(p)
        positions = new_pos
        # 买入(次日开盘)
        if gate:
            recs = []
            for it in pool:
                rows = klines.get(it["code"])
                if not rows: continue
                df = fb.df_at(rows, asof)
                if df is None: continue
                rr = fb.judge(it, df, asof, mkt_dir)
                if rr: recs.append((it, rr))
            recs.sort(key=lambda x: -x[1][1])
        else:
            recs = []
        if recs and len(positions) < 5:
            for it, (typ, lv, tags) in recs:
                if len(positions) >= 5 or cash < 20000: break
                rows = klines.get(it["code"])
                dates = [x["date"] for x in rows]
                idx = next((k for k,d in enumerate(dates) if d>asof), None)
                if idx is None: continue
                opx = rows[idx]["open"]
                if opx <= 0 or any(p["code"]==it["code"] for p in positions): continue
                qty = int(20000/opx)
                if qty <= 0: continue
                cash -= qty*opx
                positions.append({"code":it["code"],"name":it.get("name",""),"entry":opx,
                                  "entry_date":dates[idx],"qty":qty,"cost":qty*opx,"hi":opx,"days":0})
    fin = cash + sum(klines.get(p["code"])[-1]["close"]*p["qty"] for p in positions)
    tds = trades
    w = sum(1 for x in tds if x["ret_pct"]>0)
    aw = sum(x["ret_pct"] for x in tds if x["ret_pct"]>0)/max(1,w)
    al = sum(x["ret_pct"] for x in tds if x["ret_pct"]<=0)/max(1,len(tds)-w)
    print(f"[{label}] 期末{fin:.0f} ({(fin/100000-1)*100:+.1f}%)  平仓{len(tds)} 胜率{w/len(tds)*100:.0f}%  "
          f"均盈+{aw:.1f}% 均亏{al:.1f}% 盈亏比{abs(aw/al):.1f}")
    from collections import Counter
    print(f"        离场: {dict(Counter(x['reason'] for x in tds))}")
    return fin/100000-1, w/len(tds), abs(aw/al) if al else 0

if __name__ == "__main__":
    print("对照: 有20日 = +21.7% / 胜率41% / 盈亏比1.96 (旧规则)")
    print("      无20日 = +51.0% / 胜率27% / 盈亏比5.15 (当前)")
    print()
    run("pct5", "收紧止损-5%")
    run("pct8", "收紧止损-8%")
