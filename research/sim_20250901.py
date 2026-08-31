# -*- coding: utf-8 -*-
"""2025年9月1日10万模拟交易: 严格按规则(auto) vs 无视环境硬买, 到8月28日结算"""
import sys, os, json, bisect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
klines = json.load(open(os.path.join(BASE,"research","kline_cache_long.json"), encoding="utf-8"))
ind_long = json.load(open(os.path.join(BASE,"research","ind_idx_long.json"), encoding="utf-8"))
mkt_long = json.load(open(os.path.join(BASE,"research","mkt_long.json"), encoding="utf-8"))
pool = json.load(open(os.path.join(BASE,"highfit_pool.json"), encoding="utf-8"))
import layers, analysis as an, env_judge, scan_daily
layers._load_ind_cache = lambda: ind_long
layers.get_market_kline = lambda *a, **k: mkt_long

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
weekly_map = {c: aggregate_weekly(rows) for c, rows in klines.items()}

def hist_scan(it, asof):
    """复刻 scan_daily._scan_one v1.5.0 (历史版)"""
    code = it.get("code"); ind = it.get("ind","")
    rows = [r for r in klines.get(code, []) if r["date"] <= asof]
    if len(rows) < 70: return None
    df = pd.DataFrame(rows).tail(300)
    trend = an.analyze_trend(df, "日线")
    direction = trend.get("direction","sideways")
    last = df.iloc[-1]
    close = float(last["close"])
    chg = close/float(df.iloc[-2]["close"])-1
    hi60 = max(df["high"].tolist()[-60:])
    dd60 = close/hi60 - 1
    # 大盘门卫(历史)
    mrr = [r for r in mkt_long if r["date"] <= asof]
    mcl = [r["close"] for r in mrr]
    mkt_ok = True
    if len(mcl) >= 65:
        if layers._direction(mcl) == "down": mkt_ok = False
        elif mcl[-1] < mcl[-6]*0.985: mkt_ok = False
    if not mkt_ok: return None
    pats = scan_daily.detect_bullish(df)
    vol_hit = any(p[2] for p in pats)
    # ===== 通道A 超跌 =====
    irows = [r for r in ind_long.get(ind, []) if r["date"] <= asof]
    icl = [r["close"] for r in irows]
    ind_ok = True
    if len(icl) >= 61 and layers._direction(icl) == "down": ind_ok = False
    stabilized = scan_daily._stabilized(df)
    if dd60 <= -0.20 and pats and stabilized and ind_ok:
        best = -99; best_grade="weak"; best_pat=""
        for pn, pi, pvc in pats:
            sc, lv, _ = scan_daily._pattern_quality(df, pi, pn, pvc)
            if sc > best: best, best_grade, best_pat = sc, lv, pn
        if best_grade == "weak": pass
        else:
            return {"type":"rebound","level":1,"name":it.get("name",""),"code":code,
                    "price":round(close,2),"chg":chg,"tags":["超跌企稳"]}
    # ===== 通道B 周线趋势 =====
    wmap = weekly_map.get(code)
    dw = [w["date"] for w in wmap] if wmap else []
    idx = bisect.bisect_right(dw, asof)
    wk = wmap[:idx] if wmap else []
    if not weekly_up(wk): return None
    # 行业周线多头
    if len(irows) < 15: return None
    iw = ind_weekly(irows)
    if not (len(iw)>=10 and sum(iw[-5:])/5 > sum(iw[-10:])/10 and iw[-1] > sum(iw[-10:])/10): return None
    # 过滤: 过热/趋势阶段
    over,_ = scan_daily._overheated(df)
    if over: return None
    tm = scan_daily._trend_metrics(df)
    if tm is None: return None
    g60,bias,dist,days = tm
    if g60>100 or bias>20 or dist<5: return None
    if g60>60 or bias>10: return None
    # 行业门卫/位置
    if len(icl) >= 61 and layers._direction(icl) == "down": return None
    if len(irows) >= 25 and icl[-1] < sum(icl[-20:])/20: return None
    ig = icl[-1]/icl[-61]-1 if len(icl)>=61 else 0
    if ig > 0.25: return None
    # 评分门槛
    score = 1
    if direction == "down": score += 1
    if pats: score += 1
    if score < 2: return None
    return {"type":"trend","level":min(score,3),"name":it.get("name",""),"code":code,
            "price":round(close,2),"chg":chg,"tags":["周线趋势"]}

# 逐日扫描7/1-8/28
dates = sorted(set(r["date"] for r in mkt_long if "2025-09-01" <= r["date"] <= "2026-08-28"))
daily_sigs = {}
import os as _os
_sigf = os.path.join(BASE,"research","daily_sigs_2025.json")
if _os.path.exists(_sigf):
    daily_sigs = json.load(open(_sigf, encoding="utf-8"))
    print("加载已有推荐缓存", len(daily_sigs))
for d in dates:
    if d in daily_sigs:
        continue
    sigs = []
    for it in pool:
        r = hist_scan(it, d)
        if r: sigs.append(r)
    daily_sigs[d] = sigs
    json.dump(daily_sigs, open(_sigf,"w",encoding="utf-8"), ensure_ascii=False)
    if sigs: print(f"{d} 推荐{len(sigs)}只: "+", ".join(f"{s['name']}({s['type']})" for s in sigs[:6]), flush=True)

# ===== 模拟交易 =====
def sim(mode):
    """mode=auto: 环境合格才买; hard: 无视环境全买"""
    cash = 100000.0; holdings = {}; closed = []; total_in = 0
    for i, d in enumerate(dates):
        sigs = daily_sigs.get(d, [])
        # 环境判定
        avail_pct = 100.0
        if mode == "auto":
            act = env_judge.env_action(asof=d)
            if act["action"] == "filter_out": continue  # 环境弱空仓
            avail_pct = act["pos_pct"]  # 动态仓位(评分4→67%)
        if not sigs:
            # 无推荐也仍需更新持仓+离场
            pass
        # 先逐日更新持仓+离场(释放资金)
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
            # 移动止损10%
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
        # 买入(离场释放资金后, 等权, 最多5只; 总仓位受avail_pct限制)
        buy_list = sigs[:5]
        alloc = 100000.0 * (avail_pct/100.0) / 5
        for s in buy_list:
            if s["code"] in holdings: continue
            if cash < alloc: continue
            qty = int(alloc / s["price"])
            if qty <= 0: continue
            cost = qty * s["price"]
            cash -= cost; total_in += cost
            holdings[s["code"]] = {"name":s["name"],"type":s["type"],"entry":s["price"],"entry_d":d,
                "qty":qty,"high":s["price"],"price":s["price"]}
    # 结算(8/28)
    final_cash = cash
    for code, h in holdings.items():
        rows = [r for r in klines.get(code, []) if r["date"] <= dates[-1]]
        px = rows[-1]["close"] if rows else h["price"]
        final_cash += px*h["qty"]
    realized = sum(c["ret"]*c["entry"]*c["qty"] for c in closed)
    unreal = sum((h["price"]/h["entry"]-1)*h["entry"]*h["qty"] for h in holdings.values())
    return {"mode":mode,"final":final_cash,"ret":final_cash/100000-1,
            "closed":closed,"holdings":list(holdings.values()),
            "realized":realized,"unreal":unreal,"total_in":total_in}
print("\n===== 模拟结果 =====")
for mode in ["auto","hard"]:
    r = sim(mode)
    print(f"\n【{mode}】最终资产: {r['final']:.0f}元 总收益: {r['ret']*100:+.1f}% 已实现: {r['realized']:.0f} 浮动: {r['unreal']:.0f}")
    print(f"  买入投入: {r['total_in']:.0f}元 已卖出{len(r['closed'])}笔 仍持有{len(r['holdings'])}只")
    if r["closed"]:
        print("  已卖出明细:")
        for c in r["closed"]:
            print(f"    {c['name']} 买{c['entry_d']}@{c['entry']:.2f} 卖{c['exit_d']}@{c['exit']:.2f} 收益{c['ret']*100:+.1f}% [{c['reason']}]")
    if r["holdings"]:
        print("  仍持有:")
        for h in r["holdings"]:
            print(f"    {h['name']} 买{h['entry_d']}@{h['entry']:.2f} 现价{h['price']:.2f} 浮动{(h['price']/h['entry']-1)*100:+.1f}%")
