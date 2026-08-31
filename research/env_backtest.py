# -*- coding: utf-8 -*-
"""带市场环境评分的滚动回测: 支持任意起止日期 + 环境阈值过滤
环境评分>=阈值 才允许扫描推荐买入. 完整复刻 full_backtest 规则"""
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

PER = 20000.0; MAX_POS = 5; INIT = 100000.0

def env_score(asof):
    rr = [r for r in mkt_long if r["date"] <= asof]
    closes = [r["close"] for r in rr]
    if len(closes) < 260: return None
    c = closes[-1]
    d = layers._direction(closes)
    s_trend = {"up":2,"side":1,"down":0}[d]
    hi250 = max(closes[-250:]); dd250 = (c/hi250-1)*100
    s_pos = 1 if -15 <= dd250 <= -1.5 else 0
    up20=total=0; newhi=0
    for code, rows in bk.items():
        rr2 = [r for r in rows if r["date"] <= asof]
        if len(rr2) < 65: continue
        total += 1
        if rr2[-1]["close"] > rr2[-21]["close"]: up20 += 1
        if rr2[-1]["close"] >= max(r["close"] for r in rr2[-60:]): newhi += 1
    if total == 0: return None
    s_breadth = 1 if (up20/total*100 > 55 and newhi/total*100 > 6) else 0
    mom20 = (c/closes[-21]-1)*100
    s_mom = 1 if 0 <= mom20 <= 6 else 0
    return s_trend + s_pos + s_breadth + s_mom

def run(start, end, env_th):
    """滚动回测. env_th=None 表示不启用环境过滤"""
    mkt_dates = [r["date"] for r in mkt_long if start <= r["date"] <= end]
    cash = INIT; positions = []; trades = []
    for asof in mkt_dates:
        gate, sysweak, mkt_dir = fb.mkt_snapshot(asof)
        # 离场(先卖)
        for p in positions[:]:
            rows = klines.get(p["code"], [])
            rr = [x for x in rows if x["date"] <= asof]
            if not rr: continue
            px = rr[-1]["close"]; p["hi"] = max(p["hi"], px)
            reason = None
            c20 = sum(x["close"] for x in rr[-21:-1])/20 if len(rr) >= 21 else px
            if px <= p["hi"]*0.90: reason = "移动止损"
            elif sysweak:
                if px <= p["entry"] or px < c20: reason = "清弱势"
            elif px < c20 and mkt_dir == "down": reason = "双确认"
            if reason:
                trades.append({"name":p["name"],"entry_date":p["entry_date"],"exit_date":asof,
                               "ret_pct":round((px/p["entry"]-1)*100,2),"reason":reason})
                cash += px*p["qty"]; positions.remove(p)
        # 环境过滤
        if env_th is not None:
            sc = env_score(asof)
            if sc is None or sc < env_th:
                gate = False
        # 扫描+买入
        if gate and len(positions) < MAX_POS:
            recs = []
            for it in pool:
                rows = klines.get(it["code"], [])
                rr = [x for x in rows if x["date"] <= asof]
                if len(rr) < 60: continue
                df = pd.DataFrame(rr)
                r = fb.judge(it, df, asof, mkt_dir)
                if r: recs.append((it, r))
            recs.sort(key=lambda x: -x[1][1])
            for it, (typ, lv, tags) in recs:
                if len(positions) >= MAX_POS or cash < PER: break
                rows = klines.get(it["code"], []); dates = [r["date"] for r in rows]
                idx = next((k for k, d in enumerate(dates) if d > asof), None)
                if idx is None: continue
                opx = rows[idx]["open"]
                if opx <= 0 or any(p["code"]==it["code"] for p in positions): continue
                qty = int(PER/opx)
                if qty <= 0: continue
                cash -= qty*opx
                positions.append({"code":it["code"],"name":it.get("name",""),"entry":opx,
                                  "entry_date":dates[idx],"qty":qty,"cost":qty*opx,"hi":opx})
    final = cash + sum(klines.get(p["code"],[])[-1]["close"]*p["qty"] for p in positions)
    tds = trades
    w = sum(1 for x in tds if x["ret_pct"]>0)
    return {"final": final, "ret": (final/INIT-1)*100, "n": len(tds), "win": w,
            "hold": len(positions), "cash": cash,
            "winrate": (w/len(tds)*100 if tds else 0), "trades": tds}

def report(start, end, label):
    print(f"\n===== {label} ({start} ~ {end}) =====")
    r0 = run(start, end, None)
    print(f"  无环境过滤: 收益{r0['ret']:+.1f}%  平仓{r0['n']}  胜率{r0['winrate']:.0f}%")
    for th in [4, 5]:
        r = run(start, end, th)
        print(f"  环境评分>={th}: 收益{r['ret']:+.1f}%  平仓{r['n']}  胜率{r['winrate']:.0f}%  现金{r['cash']:.0f}")

if __name__ == "__main__":
    report("2025-01-06", "2026-08-28", "2025至今(牛市基准)")
    report("2023-01-03", "2024-12-31", "2023-2024(弱市验证)")
