# -*- coding: utf-8 -*-
"""新通道B(周线多头主判据) 扫描最近N个交易日, 列出每日实际选出的推荐+后续表现"""
import sys, os, json, bisect
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
klines = json.load(open(os.path.join(BASE,"research","kline_cache_long.json"), encoding="utf-8"))
ind_long = json.load(open(os.path.join(BASE,"research","ind_idx_long.json"), encoding="utf-8"))
mkt_long = json.load(open(os.path.join(BASE,"research","mkt_long.json"), encoding="utf-8"))
pool = json.load(open(os.path.join(BASE,"highfit_pool.json"), encoding="utf-8"))
import layers, analysis as an
layers._load_ind_cache = lambda: ind_long
layers.get_market_kline = lambda *a, **k: mkt_long
import scan_daily
from scan_daily import _overheated, _trend_metrics, _ind_gain

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
    ma5=sum(cs[-5:])/5; ma10=sum(cs[-10:])/10; ma20=sum(cs[-20:])/20
    return bool(ma5>ma10>ma20 and cs[-1]>ma10)
def fwd_ret(rows, asof, n):
    dates=[r["date"] for r in rows]
    if asof not in dates: return None
    i=dates.index(asof)
    if i+n>=len(rows): return None
    return rows[i+n]["close"]/rows[i]["close"]-1

weekly_map = {c: aggregate_weekly(rows) for c, rows in klines.items()}
# 最近60个交易日
mkt_dates=[r["date"] for r in mkt_long if "2026-02-01"<=r["date"]<="2026-08-28"]
print("扫描交易日数:", len(mkt_dates), "范围:", mkt_dates[0], "~", mkt_dates[-1], flush=True)

def mkt_dir_simple(asof):
    rr=[r for r in mkt_long if r["date"]<=asof]
    if len(rr)<60: return "side"
    cs=[r["close"] for r in rr]
    m5=sum(cs[-5:])/5; m20=sum(cs[-20:])/20; m60=sum(cs[-60:])/60
    if m5>m20 and cs[-1]>m20 and cs[-1]>m60: return "up"
    if m5<m20 and cs[-1]<m20: return "down"
    return "side"

hits = []
for asof in mkt_dates:
    md = mkt_dir_simple(asof)
    for it in pool:
        rows = klines.get(it["code"], [])
        rr = [x for x in rows if x["date"] <= asof]
        if len(rr) < 70: continue
        df = pd.DataFrame(rr).tail(300)
        tr = an.analyze_trend(df, "日线")
        direction = tr.get("direction","sideways")
        # 新通道B主判据: 周线多头
        wmap = weekly_map.get(it["code"])
        dw = [w["date"] for w in wmap] if wmap else []
        idx = bisect.bisect_right(dw, asof)
        wk = wmap[:idx] if wmap else []
        if not weekly_up(wk): continue
        # 过滤: 过热 / 趋势阶段
        over, _ = _overheated(df)
        if over: continue
        tm = _trend_metrics(df)
        if tm is None: continue
        gain60, bias, dist_hi, days = tm
        if gain60 > 100 or bias > 20 or dist_hi < 5: continue
        if gain60 > 60 or bias > 10: continue
        # 行业过滤: 行业高位排除 + 行业周线多头确认(当前行业趋势向上)
        ig = _ind_gain(it.get("ind",""))
        if ig is not None and ig > 25: continue
        if scan_daily._ind_weekly_up(it.get("ind","")) is False: continue
        # 门槛: 深回调 or 共振
        score = 1
        if direction == "down": score += 1
        if md == "up": score += 1
        if score < 2: continue
        hits.append({
            "asof": asof, "code": it["code"], "name": it.get("name",""), "ind": it.get("ind",""),
            "dir": direction, "score": score, "md": md,
            "fwd5": fwd_ret(rows, asof, 5), "fwd15": fwd_ret(rows, asof, 15),
        })

# 汇总
print(f"\n共选出 {len(hits)} 条趋势推荐(60个交易日)")
from collections import Counter, defaultdict
# 按日期统计
by_date = defaultdict(list)
for h in hits: by_date[h["asof"]].append(h)
dates_hit = sorted(by_date.keys())
print(f"其中有推荐的交易日: {len(dates_hit)}/{len(mkt_dates)}")
# 有推荐的股票(去重)
codes_hit = {}
for h in hits: codes_hit[h["code"]] = h["name"]
print(f"覆盖股票数: {len(codes_hit)}")
print("\n===== 每日推荐明细(最近15个有推荐的交易日) =====")
for d in dates_hit[-15:]:
    hs = by_date[d]
    print(f"\n【{d}】大盘{hs[0]['md']} 推荐{len(hs)}只:")
    for h in sorted(hs, key=lambda x:-x["score"]):
        f5 = f"{h['fwd5']*100:+.1f}%" if h['fwd5'] is not None else "--"
        f15 = f"{h['fwd15']*100:+.1f}%" if h['fwd15'] is not None else "--"
        print(f"    {h['name']:<6}({h['ind']}) 方向={h['dir']:<6} 分={h['score']} 后续5日:{f5} 15日:{f15}")
json.dump(hits, open(os.path.join(BASE,"research","recent_hits.json"),"w",encoding="utf-8"), ensure_ascii=False)
