# -*- coding: utf-8 -*-
"""多时点验证: 2023/2024 各4个时点, 每时点扫描推荐→买入→跟踪120交易日, 对比5种止损方案"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util
import pandas as pd
import layers

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
klines = json.load(open(os.path.join(BASE, "research", "kline_cache_long.json"), encoding="utf-8"))
ind_long = json.load(open(os.path.join(BASE, "research", "ind_idx_long.json"), encoding="utf-8"))
mkt_long = json.load(open(os.path.join(BASE, "research", "mkt_long.json"), encoding="utf-8"))
pool = json.load(open(os.path.join(BASE, "highfit_pool.json"), encoding="utf-8"))
layers._load_ind_cache = lambda: ind_long
layers.get_market_kline = lambda *a, **k: mkt_long

spec = importlib.util.spec_from_file_location("fb", os.path.join(BASE, "research", "full_backtest.py"))
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)

TIMEPOINTS = ["2023-02-06","2023-05-10","2023-08-10","2023-11-10",
              "2024-02-05","2024-05-10","2024-08-09","2024-11-08"]
HORIZON = 120
PER = 20000
STOP_MODES = ["pct5","pct7","pct8","pct10","breakeven"]
MODE_LABEL = {"pct5":"止损-5%","pct7":"止损-7%","pct8":"止损-8%",
              "pct10":"止损-10%","breakeven":"保本追踪"}

def df_at(code, asof):
    rows = [r for r in klines.get(code, []) if r["date"] <= asof]
    if len(rows) < 60: return None
    return pd.DataFrame(rows)

def scan_at(asof):
    gate, sysweak, mkt_dir = fb.mkt_snapshot(asof)
    recs = []
    if gate:
        for it in pool:
            df = df_at(it["code"], asof)
            if df is None: continue
            r = fb.judge(it, df, asof, mkt_dir)
            if r: recs.append((it, r[0], r[1], r[2]))
        recs.sort(key=lambda x: -x[2])
    return recs, gate, mkt_dir

def mkt_state_at(t):
    rr = [x for x in mkt_long if x["date"] <= t]
    closes = [x["close"] for x in rr]
    if len(closes) < 65: return "unknown", False
    mkt_dir = layers._direction(closes)
    ma20 = sum(closes[-20:])/20; ma60 = sum(closes[-60:])/60
    c = closes[-1]; c5 = closes[-6]
    sysweak = (ma20 < ma60) and (c < c5*0.98)
    return mkt_dir, sysweak

def run_batch(recs, asof, stop_mode):
    """买入后跟踪HORIZON交易日, 返回每笔(ret_pct, reason)列表"""
    mkt_dates = [r["date"] for r in mkt_long if r["date"] > asof][:HORIZON]
    if not mkt_dates: return []
    # 买入(次日开盘): 记录每只 entry
    entries = {}
    for it, typ, lv, tags in recs:
        rows = klines.get(it["code"], [])
        dates = [r["date"] for r in rows]
        idx = next((k for k, d in enumerate(dates) if d > asof), None)
        if idx is None: continue
        opx = rows[idx]["open"]
        if opx <= 0: continue
        entries[it["code"]] = {"entry": opx, "hi": opx, "name": it.get("name","")}
    if not entries: return []
    exits = {}   # code -> (px, reason)
    for t in mkt_dates:
        mkt_dir, sysweak = mkt_state_at(t)
        for code, p in list(entries.items()):
            if code in exits: continue
            rows = klines.get(code, [])
            rr = [x for x in rows if x["date"] <= t]
            if not rr: continue
            px = rr[-1]["close"]
            p["hi"] = max(p["hi"], px)
            reason = None
            if stop_mode == "pct5": th = 0.95
            elif stop_mode == "pct7": th = 0.93
            elif stop_mode == "pct8": th = 0.92
            elif stop_mode == "pct10": th = 0.90
            else: th = None
            if stop_mode == "breakeven":
                if p["hi"] >= p["entry"]*1.10:
                    if px <= p["entry"]*1.00: reason = "保本"
                else:
                    if px <= p["hi"]*0.90: reason = "止损"
            elif th is not None:
                if px <= p["hi"]*th: reason = "止损"
            if reason is None:
                c20 = sum(x["close"] for x in rr[-21:-1])/20 if len(rr) >= 21 else px
                if sysweak:
                    if px <= p["entry"] or px < c20: reason = "清弱势"
                else:
                    if px < c20 and mkt_dir == "down": reason = "双确认"
            if reason:
                exits[code] = (px, reason)
    # 结算
    results = []
    for code, p in entries.items():
        if code in exits:
            px, reason = exits[code]
        else:
            px = klines.get(code, [])[-1]["close"]; reason = "期末持有"
        results.append((px / p["entry"] - 1, reason))
    return results

def main():
    print("="*80)
    print("多时点验证: 每时点扫描推荐→次日开盘买入→跟踪120交易日")
    print("时点: " + ", ".join(TIMEPOINTS))
    print("="*80)
    # 每时点推荐统计
    print("\n[各时点推荐量]")
    scan_recs = {}
    for tp in TIMEPOINTS:
        recs, gate, mkt_dir = scan_at(tp)
        scan_recs[tp] = recs
        rb = sum(1 for r in recs if r[1]=="rebound")
        tr = sum(1 for r in recs if r[1]=="trend")
        print(f"  {tp}: 推荐{len(recs)}只(抄底{rb}/趋势{tr}) 门卫={'开' if gate else '关'} 大盘={mkt_dir}")
    # 各止损方案汇总
    print("\n[各止损方案 · 8个时点汇总]")
    print(f"{'方案':<10}{'均收益%':>10}{'胜率%':>8}{'盈亏比':>8}{'最优时点':>8}")
    agg = {}
    for mode in STOP_MODES:
        rets_all, wins_all = [], []
        best_cnt = 0
        per_tp = []
        for tp in TIMEPOINTS:
            res = run_batch(scan_recs[tp], tp, mode)
            if not res: per_tp.append(None); continue
            rets = [r[0] for r in res]
            wins = sum(1 for r in rets if r > 0)
            rets_all += rets
            per_tp.append(sum(rets)/len(rets))
        agg[mode] = {"rets": rets_all, "per_tp": per_tp}
    # 统计最优
    for mode in STOP_MODES:
        per_tp = agg[mode]["per_tp"]
        best_cnt = sum(1 for m in STOP_MODES
                       if all((per_tp[i] is not None and agg[m]["per_tp"][i] is not None and per_tp[i] >= agg[m]["per_tp"][i])
                              for i in range(len(TIMEPOINTS)) if per_tp[i] is not None))
        # 简化: 计算每时点谁最高
    # 每时点最优方案统计
    best_per_tp = []
    for i in range(len(TIMEPOINTS)):
        vals = {m: agg[m]["per_tp"][i] for m in STOP_MODES if agg[m]["per_tp"][i] is not None}
        if vals:
            best_per_tp.append(max(vals, key=vals.get))
    from collections import Counter
    best_counter = Counter(best_per_tp)
    for mode in STOP_MODES:
        rets = agg[mode]["rets"]
        if not rets: continue
        avg = sum(rets)/len(rets)*100
        wins = sum(1 for r in rets if r > 0)
        wr = wins/len(rets)*100
        losses = [r for r in rets if r <= 0]
        aw = sum(r for r in rets if r>0)/max(1,wins)
        al = sum(r for r in losses)/max(1,len(losses))
        pl = abs(aw/al) if al else 0
        nb = best_counter.get(mode, 0)
        print(f"{MODE_LABEL[mode]:<10}{avg:>9.1f}%{wr:>7.0f}%{pl:>7.1f}x{nb:>7}个时点最优")
    print(f"\n每时点最优方案分布: {dict(best_counter)}")
    # 明细
    print("\n[各时点收益明细]")
    hdr = f"{'时点':<12}" + "".join(f"{MODE_LABEL[m]:>10}" for m in STOP_MODES)
    print(hdr)
    for i, tp in enumerate(TIMEPOINTS):
        row = f"{tp:<12}"
        for m in STOP_MODES:
            v = agg[m]["per_tp"][i]
            row += f"{('--' if v is None else f'{v*100:.0f}%'):>10}"
        print(row)
    print("="*80)

if __name__ == "__main__":
    main()
