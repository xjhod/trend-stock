# -*- coding: utf-8 -*-
"""周线真趋势验证: 周线多头排列 → 未来交易日收益"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
klines = json.load(open(os.path.join(BASE,"research","kline_cache_long.json"), encoding="utf-8"))
pool = json.load(open(os.path.join(BASE,"highfit_pool.json"), encoding="utf-8"))

# 预聚合周线: code -> [ {date(周末交易日), open, high, low, close, volume} ]
def aggregate_weekly(rows):
    weeks = {}
    for r in rows:
        # 用 ISO 周作 key
        try:
            wk = pd.Timestamp(r["date"]).to_period("W").start_time.strftime("%Y-%m-%d")
        except Exception:
            wk = r["date"][:7]
        if wk not in weeks:
            weeks[wk] = {"open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r["volume"], "date": r["date"]}
        else:
            w = weeks[wk]
            w["high"] = max(w["high"], r["high"]); w["low"] = min(w["low"], r["low"])
            w["close"] = r["close"]; w["volume"] += r["volume"]; w["date"] = r["date"]
    return [weeks[k] for k in sorted(weeks)]

print("预聚合周线...", flush=True)
weekly_map = {}
for c, rows in klines.items():
    weekly_map[c] = aggregate_weekly(rows)
print("完成。抽样检查周线数量:", {c: len(weekly_map[c]) for c in list(weekly_map)[:3]}, flush=True)

def weekly_ma(closes, n):
    if len(closes) < n: return None
    return sum(closes[-n:]) / n

def weekly_trend_up(weekly_rows):
    """周线多头排列: 周MA5>周MA10>周MA20 且 收盘>周MA10 (周线级别真趋势)"""
    if len(weekly_rows) < 21: return None
    closes = [w["close"] for w in weekly_rows]
    ma5 = weekly_ma(closes, 5); ma10 = weekly_ma(closes, 10); ma20 = weekly_ma(closes, 20)
    if ma5 is None or ma10 is None or ma20 is None: return None
    c = closes[-1]
    return (ma5 > ma10 > ma20) and (c > ma10)

def weekly_up_retrace(weekly_rows):
    """周线多头 + 周线内回调(收盘距20周高回撤5~15%, 回调低吸)"""
    if len(weekly_rows) < 21: return None
    closes = [w["close"] for w in weekly_rows]
    ma5 = weekly_ma(closes, 5); ma10 = weekly_ma(closes, 10); ma20 = weekly_ma(closes, 20)
    if ma5 is None or ma10 is None or ma20 is None: return None
    c = closes[-1]
    if not (ma5 > ma10 > ma20 and c > ma10): return None
    hi20 = max(w["high"] for w in weekly_rows[-20:])
    dd = (1 - c/hi20) * 100 if hi20 else 99
    if dd < 3 or dd > 20: return None
    return True

# 扫描时点
mkt_dates_file = "research/mkt_long.json"
mkt_dates = [r["date"] for r in json.load(open(os.path.join(BASE, mkt_dates_file), encoding="utf-8")) if "2023-01-01" <= r["date"] <= "2026-08-28"]
SCAN = mkt_dates[::5]
print(f"扫描时点: {len(SCAN)}", flush=True)

def fwd_ret(rows, asof, n):
    dates=[r["date"] for r in rows]
    if asof not in dates: return None
    i=dates.index(asof)
    if i+n>=len(rows): return None
    return rows[i+n]["close"]/rows[i]["close"]-1

records = {"W1":[], "W2":[]}
for k, asof in enumerate(SCAN):
    for it in pool:
        rows = klines.get(it["code"], [])
        rr = [x for x in rows if x["date"] <= asof]
        if len(rr) < 21: continue
        # 周线(截至asof)
        wk = aggregate_weekly(rr)
        if len(wk) < 21: continue
        r1 = weekly_trend_up(wk)
        r2 = weekly_up_retrace(wk)
        base = {"code":it["code"],"name":it.get("name",""),"asof":asof,
                "fwd3":fwd_ret(rows,asof,3),"fwd7":fwd_ret(rows,asof,7),
                "fwd15":fwd_ret(rows,asof,15),"fwd30":fwd_ret(rows,asof,30),"fwd60":fwd_ret(rows,asof,60)}
        if r1: records["W1"].append(dict(base))
        if r2: records["W2"].append(dict(base))
    if (k+1) % 40 == 0: print(f"  {k+1}/{len(SCAN)} W1={len(records['W1'])} W2={len(records['W2'])}", flush=True)

def wr(rs, f):
    vals=[r[f] for r in rs if r[f] is not None]
    if not vals: return None,None,0
    return sum(1 for v in vals if v>0)/len(vals)*100, sum(vals)/len(vals)*100, len(vals)
def rep(t, rs):
    parts=[]
    for f in ["fwd3","fwd7","fwd15","fwd30","fwd60"]:
        w,a,n=wr(rs,f)
        parts.append(f"{f[3:]}:{w:.0f}%({a:+.1f}%)" if w is not None else f"{f[3:]}:--")
    print(f"  {t:<28} n={len(rs):<4} "+"  ".join(parts))

print("\n===== 周线真趋势结果 =====")
rep("W1 周线多头排列", records["W1"])
rep("W2 周线多头+回调5-20%", records["W2"])
json.dump(records, open(os.path.join(BASE,"research","weekly_records.json"),"w",encoding="utf-8"), ensure_ascii=False)
