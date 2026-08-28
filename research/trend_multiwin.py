# -*- coding: utf-8 -*-
"""多窗口趋势线研究 v3: 5/10/20/40日, 复用严格趋势线, 复用行业指数缓存"""
import warnings; warnings.filterwarnings("ignore")
import sys, json, time
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis')
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis/research')
from data_fetcher import _kline_from_sina, _get_json
from trendline_strict import get_index_kline, market_direction, strict_trendline, build_industry_index
BASE = '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis'

def run(stocks, all_a, idx_rows, fwds=(5,10,20,40)):
    idx_closes = [r["close"] for r in idx_rows]; idx_date = {r["date"]: i for i, r in enumerate(idx_rows)}
    inds_needed = sorted({s["ind"] for s in stocks})
    ind_idx = {}
    print(f"构建/复用 {len(inds_needed)} 个行业指数...")
    for k, ind in enumerate(inds_needed):
        ii = build_industry_index(ind, all_a)
        if ii: ind_idx[ind] = ii
        if (k+1) % 15 == 0: print(f"  {k+1}/{len(inds_needed)}")
        time.sleep(0.1)
    print(f"行业指数: {len(ind_idx)}/{len(inds_needed)}")
    # 预加载个股K线（缓存到内存）
    stock_rows = {}
    for s in stocks:
        df = _kline_from_sina(s["code"], "daily", 1000)
        if not df.empty and len(df) >= 250:
            stock_rows[s["code"]] = df.to_dict("records")
    print(f"个股K线: {len(stock_rows)} 只")
    maxf = max(fwds)
    results = {f: {"all":{"n":0,"hit":0},"up":{"n":0,"hit":0},"down":{"n":0,"hit":0},
                   "triple_up":{"n":0,"hit":0},"triple_down":{"n":0,"hit":0},"triple_conflict":{"n":0,"hit":0},
                   "mkt_ind":{"n":0,"hit":0}} for f in fwds}
    for code, rows in stock_rows.items():
        closes = [r["close"] for r in rows]; n = len(rows)
        s = next(x for x in stocks if x["code"] == code)
        ii = ind_idx.get(s["ind"]); ind_closes = [r["close"] for r in ii] if ii else []
        ind_date = {r["date"]: i for i, r in enumerate(ii)} if ii else {}
        for T in range(150, n - maxf - 5, 10):
            sig = strict_trendline(rows, T)
            if not sig: continue
            dstr = rows[T]["date"]
            mi = idx_date.get(dstr); md = market_direction(idx_closes, mi) if (mi is not None and mi>=60) else None
            wi = ind_date.get(dstr); wd = market_direction(ind_closes, wi) if (wi is not None and wi>=60) else None
            c0 = closes[T]
            for fwd in fwds:
                f = closes[T+fwd]
                hit = (sig=="up" and f>c0) or (sig=="down" and f<c0)
                R = results[fwd]
                R["all"]["n"]+=1; R["all"]["hit"]+=(1 if hit else 0)
                R[sig]["n"]+=1; R[sig]["hit"]+=(1 if hit else 0)
                if md is not None and wd is not None:
                    R["mkt_ind"]["n"]+=1; R["mkt_ind"]["hit"]+=(1 if hit else 0)
                    if md=="up" and wd=="up" and sig=="up":
                        R["triple_up"]["n"]+=1; R["triple_up"]["hit"]+=(1 if hit else 0)
                    elif md=="down" and wd=="down" and sig=="down":
                        R["triple_down"]["n"]+=1; R["triple_down"]["hit"]+=(1 if hit else 0)
                    else:
                        R["triple_conflict"]["n"]+=1; R["triple_conflict"]["hit"]+=(1 if hit else 0)
    for fwd in fwds:
        R = results[fwd]
        print(f"\n===== 窗口 {fwd} 日 =====")
        def p(name, d):
            if d["n"]: print(f"  {name:18s}: {d['hit']/d['n']*100:5.1f}% ({d['hit']}/{d['n']})")
        p("趋势线整体", R["all"]); p("  上升支撑线", R["up"]); p("  下降阻力线", R["down"])
        p("大盘+行业方向明确", R["mkt_ind"])
        p("三层共振看涨", R["triple_up"]); p("三层共振看跌", R["triple_down"]); p("三层冲突", R["triple_conflict"])

if __name__ == "__main__":
    all_a = json.load(open(f"{BASE}/bt_data/all_a.json", encoding="utf-8"))
    stocks = json.load(open(f"{BASE}/highfit_pool.json", encoding="utf-8"))
    idx = get_index_kline("sh000001")
    print(f"上证指数: {len(idx)} 根")
    run(stocks, all_a, idx, fwds=(5,10,20,40))
