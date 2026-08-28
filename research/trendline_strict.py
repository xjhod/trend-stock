# -*- coding: utf-8 -*-
"""严格趋势线检测 + 层级研究 v2
趋势线条件: 窗口内局部低点/高点做线性拟合, 在线触点>=3个, 首末跨度>=40根, 斜率方向明确
"""
import warnings; warnings.filterwarnings("ignore")
import sys, json, time
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis')
from data_fetcher import _kline_from_sina, _get_json

BASE = '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis'

def get_index_kline(sym, limit=1000):
    url = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
    d = _get_json(url, {"symbol": sym, "scale": 240, "ma": "no", "datalen": str(limit)}, retry=3, sleep=1.2)
    if not isinstance(d, list) or not d: return []
    return [{"date": it["day"], "close": float(it["close"]), "high": float(it["high"]),
             "low": float(it["low"]), "open": float(it["open"]), "volume": float(it["volume"])} for it in d]

def market_direction(closes, i, short=20, long=60):
    if i < long: return None
    ma_s = sum(closes[i-short:i])/short; ma_l = sum(closes[i-long:i])/long
    c = closes[i-1]
    if c > ma_s and ma_s > ma_l: return "up"
    if c < ma_s and ma_s < ma_l: return "down"
    return "side"

def _fit(pts, tol_pct=0.015):
    """pts: [(idx, price)], 线性回归, 返回(斜率, 在线点数, 首末跨度)"""
    n = len(pts)
    if n < 2: return None
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    mx = sum(xs)/n; my = sum(ys)/n
    sxy = sum((xs[k]-mx)*(ys[k]-my) for k in range(n))
    sxx = sum((xs[k]-mx)**2 for k in range(n))
    if sxx == 0: return None
    slope = sxy/sxx
    inter = my - slope*mx
    inliers = 0
    span_prices = []
    for idx, pr in pts:
        fitv = slope*idx + inter
        tol = pr * tol_pct
        if abs(fitv - pr) <= tol:
            inliers += 1
    span = max(xs) - min(xs)
    return (slope, inliers, span, inter)

def strict_trendline(rows, i, win=120, min_pts=3, min_span=40):
    """返回 'up'/'down'/None: 严格趋势线"""
    start = max(0, i - win)
    lows, highs = [], []
    for j in range(start, i):
        if j-1 >= 0 and j+1 < i and rows[j]["low"] <= rows[j-1]["low"] and rows[j]["low"] <= rows[j+1]["low"]:
            lows.append((j, rows[j]["low"]))
        if j-1 >= 0 and j+1 < i and rows[j]["high"] >= rows[j-1]["high"] and rows[j]["high"] >= rows[j+1]["high"]:
            highs.append((j, rows[j]["high"]))
    best = None
    # 上升: 低点拟合斜率>0, 在线>=3, 跨度>=min_span
    if len(lows) >= min_pts:
        r = _fit(lows)
        if r and r[0] > 0 and r[1] >= min_pts and r[2] >= min_span:
            best = "up"
    # 下降: 高点拟合斜率<0
    if len(highs) >= min_pts:
        r = _fit(highs)
        if r and r[0] < 0 and r[1] >= min_pts and r[2] >= min_span:
            if best is None: best = "down"
    return best

_IND_CACHE = {}
def build_industry_index(ind, all_a, topn=15, limit=400):
    if ind in _IND_CACHE: return _IND_CACHE[ind]
    members = [x for x in all_a if x["ind"] == ind]
    if len(members) < 3: return []
    members.sort(key=lambda x: -x["mv"]); members = members[:topn]
    series = {}
    for m in members:
        try: df = _kline_from_sina(m["code"], "daily", limit)
        except Exception: continue
        if df.empty: continue
        for r in df.to_dict("records"): series.setdefault(r["date"], []).append(r["close"])
    dates = sorted(series.keys())
    if len(dates) < 100: return []
    res = [{"date": d, "close": sum(series[d])/len(series[d])} for d in dates]
    _IND_CACHE[ind] = res
    return res

def run(stocks, all_a, idx_rows, fwd=5):
    idx_closes = [r["close"] for r in idx_rows]; idx_date = {r["date"]: i for i, r in enumerate(idx_rows)}
    inds_needed = sorted({s["ind"] for s in stocks})
    ind_idx = {}
    print(f"构建 {len(inds_needed)} 个行业指数...")
    for k, ind in enumerate(inds_needed):
        ii = build_industry_index(ind, all_a)
        if ii: ind_idx[ind] = ii
        if (k+1) % 10 == 0: print(f"  {k+1}/{len(inds_needed)}")
        time.sleep(0.2)
    print(f"行业指数: {len(ind_idx)}/{len(inds_needed)}")
    S = {k: {"n":0,"hit":0} for k in ["all","up","down","mkt_up","mkt_down","mkt_side",
        "sync_up","sync_down","conflict","mkt_ind","triple_up","triple_down","triple_conflict"]}
    for s in stocks:
        df = _kline_from_sina(s["code"], "daily", 1000)
        if df.empty or len(df) < 250: continue
        rows = df.to_dict("records"); closes = [r["close"] for r in rows]; n = len(rows)
        ii = ind_idx.get(s["ind"]); ind_closes = [r["close"] for r in ii] if ii else []
        ind_date = {r["date"]: i for i, r in enumerate(ii)} if ii else {}
        for T in range(150, n - fwd - 5, 10):
            sig = strict_trendline(rows, T)
            if not sig: continue
            c0 = closes[T]; f = closes[T+fwd]
            hit = (sig=="up" and f>c0) or (sig=="down" and f<c0)
            dstr = rows[T]["date"]
            mi = idx_date.get(dstr); md = market_direction(idx_closes, mi) if (mi is not None and mi>=60) else None
            wi = ind_date.get(dstr); wd = market_direction(ind_closes, wi) if (wi is not None and wi>=60) else None
            S["all"]["n"]+=1; S["all"]["hit"]+=(1 if hit else 0)
            S[sig]["n"]+=1; S[sig]["hit"]+=(1 if hit else 0)
            if md=="up":
                S["mkt_up"]["n"]+=1; S["mkt_up"]["hit"]+=(1 if hit else 0)
                if sig=="up": S["sync_up"]["n"]+=1; S["sync_up"]["hit"]+=(1 if hit else 0)
            elif md=="down":
                S["mkt_down"]["n"]+=1; S["mkt_down"]["hit"]+=(1 if hit else 0)
                if sig=="down": S["sync_down"]["n"]+=1; S["sync_down"]["hit"]+=(1 if hit else 0)
            else: S["mkt_side"]["n"]+=1; S["mkt_side"]["hit"]+=(1 if hit else 0)
            if md is not None and wd is not None:
                S["mkt_ind"]["n"]+=1; S["mkt_ind"]["hit"]+=(1 if hit else 0)
                if md=="up" and wd=="up" and sig=="up":
                    S["triple_up"]["n"]+=1; S["triple_up"]["hit"]+=(1 if hit else 0)
                elif md=="down" and wd=="down" and sig=="down":
                    S["triple_down"]["n"]+=1; S["triple_down"]["hit"]+=(1 if hit else 0)
                else:
                    S["triple_conflict"]["n"]+=1; S["triple_conflict"]["hit"]+=(1 if hit else 0)
            if md is not None and sig is not None and ((md=="up" and sig=="down") or (md=="down" and sig=="up")):
                S["conflict"]["n"]+=1; S["conflict"]["hit"]+=(1 if hit else 0)
    print(f"\n严格趋势线 · 样本{len(stocks)}只 · 窗口{fwd}日 (min_pts=3, span>=40, tol=1.5%)")
    def p(name, d):
        if d["n"]: print(f"  {name:16s}: {d['hit']/d['n']*100:5.1f}% ({d['hit']}/{d['n']})")
    p("严格趋势线整体", S["all"]); p("  上升(低点支撑线)", S["up"]); p("  下降(高点阻力线)", S["down"])
    p("大盘上升", S["mkt_up"]); p("大盘下降", S["mkt_down"]); p("大盘震荡", S["mkt_side"])
    p("个股升+大盘升", S["sync_up"]); p("个股跌+大盘跌", S["sync_down"]); p("个股与大盘反向", S["conflict"])
    p("大盘+行业方向明确", S["mkt_ind"]); p("三层共振看涨", S["triple_up"]); p("三层共振看跌", S["triple_down"]); p("三层冲突", S["triple_conflict"])

if __name__ == "__main__":
    all_a = json.load(open(f"{BASE}/bt_data/all_a.json", encoding="utf-8"))
    stocks = json.load(open(f"{BASE}/highfit_pool.json", encoding="utf-8"))
    idx = get_index_kline("sh000001")
    print(f"上证指数: {len(idx)} 根")
    run(stocks, all_a, idx, fwd=5)
