# -*- coding: utf-8 -*-
"""推荐正确率评估: 多时点扫描推荐, 统计买入后 3/7/15 交易日上涨率
口径: 信号日收盘 vs N交易日后收盘 (收益>0=上涨)
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util
import pandas as pd
import layers

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
klines = json.load(open(os.path.join(BASE,"research","kline_cache_long.json"), encoding="utf-8"))
ind_long = json.load(open(os.path.join(BASE,"research","ind_idx_long.json"), encoding="utf-8"))
mkt_long = json.load(open(os.path.join(BASE,"research","mkt_long.json"), encoding="utf-8"))
pool = json.load(open(os.path.join(BASE,"highfit_pool.json"), encoding="utf-8"))
layers._load_ind_cache = lambda: ind_long
layers.get_market_kline = lambda *a, **k: mkt_long
spec = importlib.util.spec_from_file_location("fb", os.path.join(BASE,"research","full_backtest.py"))
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)

TIMEPOINTS = ["2023-02-06","2023-05-10","2023-08-10","2023-11-10",
              "2024-02-05","2024-05-10","2024-08-09","2024-11-08",
              "2025-01-06","2025-04-10","2025-07-10","2025-10-10",
              "2026-02-10","2026-05-10","2026-08-10"]

def df_at(code, asof):
    rows = [r for r in klines.get(code, []) if r["date"] <= asof]
    return pd.DataFrame(rows) if len(rows) >= 60 else None

def fwd_ret(rows, asof, n):
    dates = [r["date"] for r in rows]
    if asof not in dates: return None
    i = dates.index(asof)
    if i + n >= len(rows): return None
    return rows[i+n]["close"] / rows[i]["close"] - 1

records = []
seen = set()
for tp in TIMEPOINTS:
    gate, sysweak, mkt_dir = fb.mkt_snapshot(tp)
    if not gate: continue
    for it in pool:
        df = df_at(it["code"], tp)
        if df is None: continue
        r = fb.judge(it, df, tp, mkt_dir)
        if not r: continue
        typ, lv, tags = r
        rows = klines.get(it["code"], [])
        rec = {"code": it["code"], "name": it.get("name",""), "type": typ, "level": lv,
               "tags": tags, "asof": tp, "mkt": mkt_dir,
               "fwd3": fwd_ret(rows, tp, 3), "fwd7": fwd_ret(rows, tp, 7), "fwd15": fwd_ret(rows, tp, 15)}
        # 个股特征
        closes = [x["close"] for x in rows if x["date"] <= tp]
        if len(closes) >= 60:
            rec["direction"] = layers._direction(closes)
        # dd60
        rr = [x for x in rows if x["date"] <= tp]
        if len(rr) >= 60:
            hi60 = max(x["high"] for x in rr[-60:])
            rec["dd60"] = rr[-1]["close"]/hi60 - 1
        # 形态等级(tags含"形态strong/medium")
        g = "weak"
        for t in tags:
            if "形态" in t:
                g = t.replace("形态","")
        rec["grade"] = g
        records.append(rec)
print(f"总推荐记录: {len(records)}")

def winrate(recs, fwd):
    vals = [r[fwd] for r in recs if r[fwd] is not None]
    if not vals: return None, None, 0
    wr = sum(1 for v in vals if v > 0)/len(vals)*100
    avg = sum(vals)/len(vals)*100
    return wr, avg, len(vals)

print(f"\n===== 基线 =====")
for f in ["fwd3","fwd7","fwd15"]:
    wr, avg, n = winrate(records, f)
    print(f"  {f.upper()}: 上涨率{wr:.1f}%  平均{avg:+.2f}%  (n={n})")

def group_report(title, keyfn, sort=None):
    print(f"\n===== {title} =====")
    groups = {}
    for r in records:
        k = keyfn(r)
        groups.setdefault(k, []).append(r)
    keys = sorted(groups.keys()) if sort is None else sort(groups)
    for k in keys:
        recs = groups[k]
        parts = []
        for f in ["fwd3","fwd7","fwd15"]:
            wr, avg, n = winrate(recs, f)
            parts.append(f"{wr:.0f}%({avg:+.1f}%)" if wr is not None else "--")
        print(f"  {str(k):<20} n={len(recs):<4}  " + "  ".join(parts))

group_report("按信号类型", lambda r: r["type"])
group_report("按level", lambda r: f"L{r['level']}")
group_report("按个股趋势方向", lambda r: r.get("direction","?"))
group_report("按大盘方向", lambda r: r.get("mkt","?"))
group_report("按形态等级", lambda r: r.get("grade","?"))
group_report("按回撤深度", lambda r: ("深超跌(≤-25%)" if r.get("dd60",0)<=-0.25 else "超跌(-20~-25%)" if r.get("dd60",0)<=-0.20 else "浅/无(>-20%)"))
