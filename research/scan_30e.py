# -*- coding: utf-8 -*-
"""467只(30亿门槛)全周期扫描, 断点续扫"""
import sys, os, json, bisect, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
klines = json.load(open(os.path.join(BASE,"research","kline_market500.json"), encoding="utf-8"))
ind_long = json.load(open(os.path.join(BASE,"research","ind_idx_30e.json"), encoding="utf-8"))
mkt_long = json.load(open(os.path.join(BASE,"research","mkt_long.json"), encoding="utf-8"))
pool = json.load(open(os.path.join(BASE,"research","pool_30e.json"), encoding="utf-8"))
import layers, analysis as an, scan_daily
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
    code = it["code"]; ind = it.get("ind","")
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
    mrr = [r for r in mkt_long if r["date"] <= asof]
    mcl = [r["close"] for r in mrr]
    mkt_ok = True
    if len(mcl) >= 65:
        if layers._direction(mcl) == "down": mkt_ok = False
        elif mcl[-1] < mcl[-6]*0.985: mkt_ok = False
    if not mkt_ok: return None
    pats = scan_daily.detect_bullish(df)
    irows = [r for r in ind_long.get(ind, []) if r["date"] <= asof]
    icl = [r["close"] for r in irows]
    ind_ok = True
    if len(icl) >= 61 and layers._direction(icl) == "down": ind_ok = False
    stabilized = scan_daily._stabilized(df)
    if dd60 <= -0.20 and pats and stabilized and ind_ok:
        best = -99; best_grade="weak"
        for pn, pi, pvc in pats:
            sc, lv, _ = scan_daily._pattern_quality(df, pi, pn, pvc)
            if sc > best: best, best_grade = sc, lv
        if best_grade != "weak":
            return {"type":"rebound","level":1,"name":it.get("name",""),"code":code,
                    "price":round(close,2),"chg":chg,"tags":["超跌企稳"]}
    wmap = weekly_map.get(code)
    dw = [w["date"] for w in wmap] if wmap else []
    idx = bisect.bisect_right(dw, asof)
    wk = wmap[:idx] if wmap else []
    if not weekly_up(wk): return None
    if len(irows) < 15: return None
    iw = ind_weekly(irows)
    if not (len(iw)>=10 and sum(iw[-5:])/5 > sum(iw[-10:])/10 and iw[-1] > sum(iw[-10:])/10): return None
    over,_ = scan_daily._overheated(df)
    if over: return None
    tm = scan_daily._trend_metrics(df)
    if tm is None: return None
    g60,bias,dist,days = tm
    if g60>100 or bias>20 or dist<5: return None
    if g60>60 or bias>10: return None
    if len(icl) >= 61 and layers._direction(icl) == "down": return None
    if len(irows) >= 25 and icl[-1] < sum(icl[-20:])/20: return None
    ig = icl[-1]/icl[-61]-1 if len(icl)>=61 else 0
    if ig > 0.25: return None
    score = 1
    if direction == "down": score += 1
    if pats: score += 1
    if score < 2: return None
    return {"type":"trend","level":min(score,3),"name":it.get("name",""),"code":code,
            "price":round(close,2),"chg":chg,"tags":["周线趋势"]}

dates = sorted(set(r["date"] for r in mkt_long if "2024-09-01" <= r["date"] <= "2026-08-28"))
_sigf = os.path.join(BASE,"research","daily_sigs_2024_30e.json")
daily_sigs = {}
if os.path.exists(_sigf):
    try:
        daily_sigs = json.load(open(_sigf, encoding="utf-8"))
        print("加载已有缓存", len(daily_sigs), flush=True)
    except Exception:
        daily_sigs = {}
t0 = time.time(); nscan = 0
for d in dates:
    if d in daily_sigs: continue
    sigs = []
    for it in pool:
        nscan += 1
        r = hist_scan(it, d)
        if r: sigs.append(r)
    daily_sigs[d] = sigs
    json.dump(daily_sigs, open(_sigf,"w",encoding="utf-8"), ensure_ascii=False)
    if sigs:
        print(f"{d} 推荐{len(sigs)}只", flush=True)
print(f"完成, 覆盖到 {dates[-1]}, 总扫描{nscan}次, 耗时{time.time()-t0:.0f}s", flush=True)
