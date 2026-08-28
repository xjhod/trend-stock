# -*- coding: utf-8 -*-
"""离场规则对比回测: 破线即走(OR) vs 破线+大盘转弱双确认(AND) vs 破线+(大盘或行业)转弱
统一建仓规则: 三层共振看涨(大盘up+行业up+个股up线)
"""
import warnings; warnings.filterwarnings("ignore")
import sys, json, os, collections
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis')
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis/research')
from trendline_strict import get_index_kline, market_direction
from trend_hold import strict_trendline_line, build_ind_cache, load_stock_rows
BASE = '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis'


def run_exit_rules(stocks, all_a, idx_rows, mode):
    idx_closes = [r["close"] for r in idx_rows]; idx_date = {r["date"]: i for i, r in enumerate(idx_rows)}
    ind_idx = build_ind_cache(all_a)
    stock_rows = load_stock_rows(stocks)
    trades = []
    for code, rows in stock_rows.items():
        closes = [r["close"] for r in rows]; n = len(rows)
        s = next(x for x in stocks if x["code"] == code)
        ii = ind_idx.get(s["ind"]); ind_closes = [r["close"] for r in ii] if ii else []
        ind_date = {r["date"]: i for i, r in enumerate(ii)} if ii else {}
        holding = False; entry = 0; entry_px = 0; trend = None
        for T in range(150, n - 1):
            dstr = rows[T]["date"]; mi = idx_date.get(dstr)
            md = market_direction(idx_closes, mi) if (mi is not None and mi >= 60) else None
            wi = ind_date.get(dstr); wd = market_direction(ind_closes, wi) if (wi is not None and wi >= 60) else None
            tl = strict_trendline_line(rows, T)
            if not holding:
                if md == "up" and wd == "up" and tl and tl[0] == "up":
                    holding = True; entry = T; entry_px = closes[T]; trend = (tl[1], tl[2])
            else:
                broken = False
                if trend:
                    sl, itc = trend
                    if closes[T] < sl * T + itc:
                        broken = True
                mkt_weak = md in ("down", None)
                ind_weak = wd in ("down", None)
                if mode == "or":
                    exit_now = broken or mkt_weak or ind_weak
                elif mode == "confirm_mkt":
                    exit_now = broken and mkt_weak
                elif mode == "confirm_mkt_ind":
                    exit_now = broken and (mkt_weak or ind_weak)
                else:
                    exit_now = broken  # 仅破线
                if exit_now:
                    trades.append({"entry": entry, "exit": T, "days": T - entry, "ret": closes[T] / entry_px - 1})
                    holding = False; trend = None
        if holding:
            trades.append({"entry": entry, "exit": n - 1, "days": n - 1 - entry, "ret": closes[n - 1] / entry_px - 1})
    rets = [t["ret"] for t in trades]; days = [t["days"] for t in trades]
    if not rets:
        return {"mode": mode, "n": 0}
    wins = sum(1 for r in rets if r > 0)
    avg_ret = sum(rets) / len(rets) * 100
    avg_days = sum(days) / len(days)
    g = [r for r in rets if r > 0]; l = [r for r in rets if r <= 0]
    pl = (sum(g) / len(g)) / (abs(sum(l) / len(l)) if l else 0) if g and l else 0
    import math
    geo = math.exp(sum(math.log(1 + r) for r in rets) / len(rets)) - 1
    ann = (1 + geo) ** (250 / avg_days) - 1 if avg_days > 0 else 0
    big_win = sum(1 for r in rets if r > 0.10); big_loss = sum(1 for r in rets if r < -0.10)
    # 盈亏总额(组合视角): 假设等权
    tot = sum(rets) / len(rets) * 100
    return {
        "mode": mode, "n": len(rets), "win": wins / len(rets) * 100, "avg_ret": avg_ret,
        "avg_days": avg_days, "pl": pl, "ann": ann * 100, "big_win": big_win, "big_loss": big_loss,
        "max_win": max(rets) * 100, "max_loss": min(rets) * 100, "tot": tot,
    }


if __name__ == "__main__":
    all_a = json.load(open(f"{BASE}/bt_data/all_a.json", encoding="utf-8"))
    stocks = json.load(open(f"{BASE}/highfit_pool.json", encoding="utf-8"))
    idx = get_index_kline("sh000001")
    print(f"上证指数 {len(idx)} 根, 最新 {idx[-1]['date']}")
    modes = ["or", "confirm_mkt", "confirm_mkt_ind"]
    print(f"\n{'离场规则':<18}{'笔数':>6}{'胜率%':>8}{'均单笔%':>9}{'盈亏比':>7}{'年化%':>8}{'均持有日':>9}{'>10%盈':>8}{'<-10%亏':>9}{'最大亏%':>9}")
    results = {}
    for m in modes:
        r = run_exit_rules(stocks, all_a, idx, m)
        results[m] = r
        if r["n"]:
            print(f"{m:<18}{r['n']:>6}{r['win']:>8.1f}{r['avg_ret']:>9.2f}{r['pl']:>7.2f}{r['ann']:>8.0f}{r['avg_days']:>9.0f}{r['big_win']:>8}{r['big_loss']:>9}{r['max_loss']:>9.1f}")
    json.dump(results, open(f"{BASE}/research/trend_exit_confirm.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n已保存 research/trend_exit_confirm.json")
