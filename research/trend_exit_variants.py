# -*- coding: utf-8 -*-
"""双确认离场 + 风控变体: 时间上限 / 硬止损
对比:
  confirm_mkt         : 破线+大盘转弱才走(基准, 可能持有过久)
  cm_time120          : 上述 + 破线后最多持有120日
  cm_time60           : 上述 + 破线后最多持有60日
  cm_stop10           : 上述 + 破线后回撤建仓价10%硬止损
  cm_stop15           : 上述 + 破线后回撤建仓价15%硬止损
  cm_stop20           : 上述 + 破线后回撤建仓价20%硬止损
  cm_t10              : 破线后观察10日, 期间大盘转弱即走; 未转弱则继续(递归)
"""
import warnings; warnings.filterwarnings("ignore")
import sys, json, os, math
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis')
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis/research')
from trendline_strict import get_index_kline, market_direction
from trend_hold import strict_trendline_line, build_ind_cache, load_stock_rows
BASE = '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis'


def run(stocks, all_a, idx_rows, mode):
    idx_closes = [r["close"] for r in idx_rows]; idx_date = {r["date"]: i for i, r in enumerate(idx_rows)}
    ind_idx = build_ind_cache(all_a); stock_rows = load_stock_rows(stocks)
    trades = []
    for code, rows in stock_rows.items():
        closes = [r["close"] for r in rows]; n = len(rows)
        s = next(x for x in stocks if x["code"] == code)
        ii = ind_idx.get(s["ind"]); ind_closes = [r["close"] for r in ii] if ii else []
        ind_date = {r["date"]: i for i, r in enumerate(ii)} if ii else {}
        holding = False; entry = 0; entry_px = 0; trend = None
        broken_since = None  # 首次破线日
        for T in range(150, n - 1):
            dstr = rows[T]["date"]; mi = idx_date.get(dstr)
            md = market_direction(idx_closes, mi) if (mi is not None and mi >= 60) else None
            wi = ind_date.get(dstr); wd = market_direction(ind_closes, wi) if (wi is not None and wi >= 60) else None
            tl = strict_trendline_line(rows, T)
            if not holding:
                if md == "up" and wd == "up" and tl and tl[0] == "up":
                    holding = True; entry = T; entry_px = closes[T]; trend = (tl[1], tl[2]); broken_since = None
            else:
                broken = False
                if trend:
                    sl, itc = trend
                    if closes[T] < sl * T + itc:
                        broken = True
                mkt_weak = md in ("down", None)
                if broken and broken_since is None:
                    broken_since = T
                exit_now = False
                drawdown = closes[T] / entry_px - 1
                if mode == "confirm_mkt":
                    exit_now = broken and mkt_weak
                elif mode == "cm_time120":
                    exit_now = (broken and mkt_weak) or (broken_since is not None and T - broken_since >= 120)
                elif mode == "cm_time60":
                    exit_now = (broken and mkt_weak) or (broken_since is not None and T - broken_since >= 60)
                elif mode.startswith("cm_stop"):
                    stop = int(mode.split("stop")[1]) / 100.0
                    exit_now = (broken and mkt_weak) or (broken_since is not None and drawdown <= -stop)
                elif mode == "cm_t10":
                    # 破线后观察10日, 期间大盘转弱即走; 若10日未转弱, 重置观察并继续(仅破线仍持有)
                    exit_now = (broken_since is not None and T - broken_since >= 1 and T - broken_since <= 10 and mkt_weak) \
                               or (broken_since is not None and T - broken_since > 10 and mkt_weak)
                if exit_now:
                    trades.append({"entry": entry, "exit": T, "days": T - entry, "ret": closes[T] / entry_px - 1})
                    holding = False; trend = None; broken_since = None
        if holding:
            trades.append({"entry": entry, "exit": n - 1, "days": n - 1 - entry, "ret": closes[n - 1] / entry_px - 1})
    rets = [t["ret"] for t in trades]; days = [t["days"] for t in trades]
    if not rets:
        return {"mode": mode, "n": 0}
    wins = sum(1 for r in rets if r > 0)
    avg_ret = sum(rets) / len(rets) * 100; avg_days = sum(days) / len(days)
    g = [r for r in rets if r > 0]; l = [r for r in rets if r <= 0]
    pl = (sum(g) / len(g)) / (abs(sum(l) / len(l)) if l else 0) if g and l else 0
    geo = math.exp(sum(math.log(1 + r) for r in rets) / len(rets)) - 1
    ann = (1 + geo) ** (250 / avg_days) - 1 if avg_days > 0 else 0
    return {
        "mode": mode, "n": len(rets), "win": wins / len(rets) * 100, "avg_ret": avg_ret,
        "avg_days": avg_days, "pl": pl, "ann": ann * 100,
        "big_win": sum(1 for r in rets if r > 0.10), "big_loss": sum(1 for r in rets if r < -0.10),
        "max_win": max(rets) * 100, "max_loss": min(rets) * 100,
    }


if __name__ == "__main__":
    all_a = json.load(open(f"{BASE}/bt_data/all_a.json", encoding="utf-8"))
    stocks = json.load(open(f"{BASE}/highfit_pool.json", encoding="utf-8"))
    idx = get_index_kline("sh000001")
    modes = ["confirm_mkt", "cm_time120", "cm_time60", "cm_stop10", "cm_stop15", "cm_stop20"]
    print(f"\n{'规则':<14}{'笔数':>6}{'胜率%':>8}{'均单笔%':>9}{'盈亏比':>7}{'年化%':>8}{'持有日':>8}{'>10%盈':>7}{'<-10%亏':>8}{'最大亏%':>9}")
    results = {}
    for m in modes:
        r = run(stocks, all_a, idx, m)
        results[m] = r
        if r["n"]:
            print(f"{m:<14}{r['n']:>6}{r['win']:>8.1f}{r['avg_ret']:>9.1f}{r['pl']:>7.2f}{r['ann']:>8.0f}{r['avg_days']:>8.0f}{r['big_win']:>7}{r['big_loss']:>8}{r['max_loss']:>9.1f}")
    json.dump(results, open(f"{BASE}/research/trend_exit_variants.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n已保存 research/trend_exit_variants.json")
