# -*- coding: utf-8 -*-
"""自上而下趋势线准确率研究：大盘层 -> 行业层 -> 个股趋势线
口径：
- 大盘: 上证指数(sh000001), 方向=MA20/MA60排列
- 行业: 该行业成分股等权合成指数(市值前N), 方向=MA20/MA60排列
- 个股: 趋势线信号(复刻calcTrendLines: 低点抬高=up / 高点走低=down)
- 命中: 未来fwd日方向与信号方向一致
"""
import warnings; warnings.filterwarnings("ignore")
import sys, json, time
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis')
from data_fetcher import _kline_from_sina, _get_json

BASE = '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis'

def get_index_kline(sym, limit=1000):
    url = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
    d = _get_json(url, {"symbol": sym, "scale": 240, "ma": "no", "datalen": str(limit)}, retry=3, sleep=1.2)
    if not isinstance(d, list) or not d:
        return []
    return [{"date": it["day"], "close": float(it["close"]), "high": float(it["high"]),
             "low": float(it["low"]), "open": float(it["open"]), "volume": float(it["volume"])} for it in d]

def market_direction(closes, i, short=20, long=60):
    if i < long: return None
    ma_s = sum(closes[i-short:i]) / short
    ma_l = sum(closes[i-long:i]) / long
    c = closes[i-1]
    if c > ma_s and ma_s > ma_l: return "up"
    if c < ma_s and ma_s < ma_l: return "down"
    return "side"

def trendline_signal(rows, i, win=80):
    start = max(0, i - win)
    lows, highs = [], []
    for j in range(start+1, i):
        if rows[j]["low"] <= rows[j-1]["low"] and rows[j]["low"] <= rows[j+1]["low"]:
            lows.append((j, rows[j]["low"]))
        if rows[j]["high"] >= rows[j-1]["high"] and rows[j]["high"] >= rows[j+1]["high"]:
            highs.append((j, rows[j]["high"]))
    if len(lows) >= 2:
        last = lows[-1]; mn = min(lows, key=lambda x: x[1])
        if mn[0] < last[0] and last[1] > mn[1]: return "up"
    if len(highs) >= 2:
        last = highs[-1]; mx = max(highs, key=lambda x: x[1])
        if mx[0] < last[0] and last[1] < mx[1]: return "down"
    return None

def build_industry_index(ind, all_a, topn=15, limit=400):
    """行业成分股(市值前topn)等权合成日线指数"""
    members = [x for x in all_a if x["ind"] == ind]
    if len(members) < 3: return []
    members.sort(key=lambda x: -x["mv"])
    members = members[:topn]
    series = {}
    for m in members:
        try:
            df = _kline_from_sina(m["code"], "daily", limit)
        except Exception:
            continue
        if df.empty: continue
        for r in df.to_dict("records"):
            series.setdefault(r["date"], []).append(r["close"])
    dates = sorted(series.keys())
    if len(dates) < 100: return []
    # 等权：用每日所有成分收盘均价
    return [{"date": d, "close": sum(series[d])/len(series[d])} for d in dates]

def run(stocks, all_a, idx_rows, fwd=5):
    idx_closes = [r["close"] for r in idx_rows]
    idx_date = {r["date"]: i for i, r in enumerate(idx_rows)}
    # 预构建行业指数
    inds_needed = sorted({s["ind"] for s in stocks})
    ind_idx = {}
    print(f"构建 {len(inds_needed)} 个行业指数...")
    for k, ind in enumerate(inds_needed):
        ii = build_industry_index(ind, all_a)
        if ii:
            ind_idx[ind] = ii
        if (k+1) % 10 == 0:
            print(f"  {k+1}/{len(inds_needed)}")
        time.sleep(0.2)
    print(f"行业指数构建完成: {len(ind_idx)}/{len(inds_needed)}")
    stats = {
        "all": {"n":0,"hit":0,"up_n":0,"up_hit":0,"down_n":0,"down_hit":0},
        "mkt_up": {"n":0,"hit":0}, "mkt_down": {"n":0,"hit":0}, "mkt_side": {"n":0,"hit":0},
        "sync_up": {"n":0,"hit":0}, "sync_down": {"n":0,"hit":0}, "conflict": {"n":0,"hit":0},
        "triple_up": {"n":0,"hit":0}, "triple_down": {"n":0,"hit":0}, "triple_conflict": {"n":0,"hit":0},
        "no_layer": {"n":0,"hit":0}, "mkt_only": {"n":0,"hit":0}, "mkt_ind": {"n":0,"hit":0},
    }
    for s in stocks:
        df = _kline_from_sina(s["code"], "daily", 1000)
        if df.empty or len(df) < 200: continue
        rows = df.to_dict("records")
        closes = [r["close"] for r in rows]
        n = len(rows)
        # 行业指数
        ii = ind_idx.get(s["ind"])
        ind_closes = [r["close"] for r in ii] if ii else []
        ind_date = {r["date"]: i for i, r in enumerate(ii)} if ii else {}
        for T in range(120, n - fwd - 5, 10):
            sig = trendline_signal(rows, T)
            if not sig: continue
            c0 = closes[T]; f = closes[T+fwd]
            hit = (sig == "up" and f > c0) or (sig == "down" and f < c0)
            # 大盘方向
            dstr = rows[T]["date"]
            mi = idx_date.get(dstr)
            md = market_direction(idx_closes, mi) if (mi is not None and mi >= 60) else None
            # 行业方向
            wi = ind_date.get(dstr) if ind_date else None
            wd = market_direction(ind_closes, wi) if (wi is not None and wi >= 60) else None
            # 汇总
            a = stats["all"]; a["n"]+=1
            if hit: a["hit"]+=1
            if sig=="up": a["up_n"]+=1; a["up_hit"]+=(1 if hit else 0)
            else: a["down_n"]+=1; a["down_hit"]+=(1 if hit else 0)
            stats["no_layer"]["n"]+=1; stats["no_layer"]["hit"]+=(1 if hit else 0)
            if md == "up":
                stats["mkt_up"]["n"]+=1; stats["mkt_up"]["hit"]+=(1 if hit else 0)
                if sig == "up":
                    stats["sync_up"]["n"]+=1; stats["sync_up"]["hit"]+=(1 if hit else 0)
            elif md == "down":
                stats["mkt_down"]["n"]+=1; stats["mkt_down"]["hit"]+=(1 if hit else 0)
                if sig == "down":
                    stats["sync_down"]["n"]+=1; stats["sync_down"]["hit"]+=(1 if hit else 0)
            else:
                stats["mkt_side"]["n"]+=1; stats["mkt_side"]["hit"]+=(1 if hit else 0)
            # 大盘+行业 确认
            if md is not None:
                stats["mkt_only"]["n"]+=1; stats["mkt_only"]["hit"]+=(1 if hit else 0)
            if md is not None and wd is not None:
                stats["mkt_ind"]["n"]+=1; stats["mkt_ind"]["hit"]+=(1 if hit else 0)
            # 三层共振/冲突
            if md == "up" and wd == "up" and sig == "up":
                stats["triple_up"]["n"]+=1; stats["triple_up"]["hit"]+=(1 if hit else 0)
            elif md == "down" and wd == "down" and sig == "down":
                stats["triple_down"]["n"]+=1; stats["triple_down"]["hit"]+=(1 if hit else 0)
            # 冲突: 大盘/行业与个股信号反向(至少一层反向)
            if md is not None and wd is not None:
                agree = (md == "up" and wd == "up" and sig == "up") or (md == "down" and wd == "down" and sig == "down")
                if not agree:
                    stats["triple_conflict"]["n"]+=1; stats["triple_conflict"]["hit"]+=(1 if hit else 0)
    # 输出
    print(f"\n样本: {len(stocks)} 只, 窗口{fwd}日")
    def p(name, d):
        if d["n"]: print(f"  {name:20s}: {d['hit']/d['n']*100:5.1f}% ({d['hit']}/{d['n']})")
    p("无层级(全部趋势线)", stats["no_layer"])
    p("  上升信号", {"n":stats["all"]["up_n"],"hit":stats["all"]["up_hit"]})
    p("  下降信号", {"n":stats["all"]["down_n"],"hit":stats["all"]["down_hit"]})
    p("大盘上升时", stats["mkt_up"])
    p("大盘下降时", stats["mkt_down"])
    p("大盘震荡时", stats["mkt_side"])
    p("个股升+大盘升", stats["sync_up"])
    p("个股跌+大盘跌", stats["sync_down"])
    p("有大盘方向(仅大盘层)", stats["mkt_only"])
    p("大盘+行业方向均明确", stats["mkt_ind"])
    p("三层共振·全面看涨", stats["triple_up"])
    p("三层共振·全面看跌", stats["triple_down"])
    p("三层方向冲突", stats["triple_conflict"])

if __name__ == "__main__":
    all_a = json.load(open(f"{BASE}/bt_data/all_a.json", encoding="utf-8"))
    stocks = json.load(open(f"{BASE}/highfit_pool.json", encoding="utf-8"))
    idx = get_index_kline("sh000001")
    print(f"上证指数: {len(idx)} 根, 最新 {idx[-1]['date'] if idx else 'N/A'}")
    run(stocks, all_a, idx, fwd=5)
