# -*- coding: utf-8 -*-
"""层级趋势模块: 大盘(sh000001) -> 行业(成分股等权指数) -> 个股(上升趋势线)
用于"自上而下共振"判断: 三层同向看涨 = 共振看涨信号(研究实证: 持有收益2倍于基准)
"""
import json, os, time
import pandas as pd
from data_fetcher import _get_json
import analysis as an

BASE = os.path.dirname(os.path.abspath(__file__))
ALL_A_FILE = os.path.join(BASE, "bt_data", "all_a.json")
IND_CACHE_FILE = os.path.join(BASE, "bt_data", "ind_idx_cache.json")

_market_cache = {"ts": 0, "rows": []}
_ind_cache = None
_all_a = None


def _load_all_a():
    global _all_a
    if _all_a is None:
        try:
            _all_a = json.load(open(ALL_A_FILE, encoding="utf-8"))
        except Exception:
            _all_a = []
    return _all_a


def _clean_ind_series(rows, max_chg=0.25):
    """清洗行业指数异常跳变点：单日涨跌幅超过 25% 判定为数据异常
    （行业等权指数由多只成分股合成，单日不可能波动超过 25%），用前一日值替代。
    防止数据源异常导致行业指数虚高/虚低，进而让个股对比失真。"""
    if not rows:
        return rows
    out = []
    prev = None
    for r in rows:
        try:
            v = float(r["close"])
        except Exception:
            continue
        if prev is not None and prev != 0 and abs(v - prev) / prev > max_chg:
            v = prev  # 异常跳变 → 用前一日值替代
        out.append({"date": r["date"], "close": round(v, 4)})
        prev = v
    return out


def _load_ind_cache():
    global _ind_cache
    if _ind_cache is None:
        try:
            raw = json.load(open(IND_CACHE_FILE, encoding="utf-8"))
        except Exception:
            raw = {}
        # 读取即清洗异常跳变（历史坏缓存自动修复）
        _ind_cache = {k: _clean_ind_series(v) for k, v in raw.items()}
    return _ind_cache


def get_market_kline(limit=300):
    """上证指数K线, 缓存30分钟"""
    now = time.time()
    if _market_cache["rows"] and now - _market_cache["ts"] < 1800:
        return _market_cache["rows"]
    url = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
    d = _get_json(url, {"symbol": "sh000001", "scale": 240, "ma": "no", "datalen": str(limit)}, retry=3, sleep=1)
    if isinstance(d, list) and d:
        rows = [{"date": it["day"], "close": float(it["close"]), "high": float(it["high"]),
                 "low": float(it["low"]), "open": float(it["open"])} for it in d]
        _market_cache["rows"] = rows
        _market_cache["ts"] = now
        return rows
    return _market_cache["rows"]


def _direction(closes, short=20, long=60):
    if len(closes) < long:
        return "unknown"
    ma_s = sum(closes[-short:]) / short
    ma_l = sum(closes[-long:]) / long
    c = closes[-1]
    if c > ma_s and ma_s > ma_l:
        return "up"
    if c < ma_s and ma_s < ma_l:
        return "down"
    return "side"


def find_ind(code):
    """查个股所属行业"""
    for x in _load_all_a():
        if x["code"] == code:
            return x.get("ind", "")
    return ""


def _trendline_params(daily_df):
    """严格上升趋势线参数: 返回 (slope, intercept, n) 或 None（n=数据长度，坐标系为全局索引）"""
    if daily_df is None or len(daily_df) < 120:
        return None
    rows = daily_df.to_dict("records")
    n = len(rows)
    start = max(0, n - 120)
    lows = []
    for j in range(start, n - 1):
        if j - 1 >= start and rows[j]["low"] <= rows[j - 1]["low"] and rows[j]["low"] <= rows[j + 1]["low"]:
            lows.append((j, rows[j]["low"]))
    if len(lows) < 3:
        return None
    xs = [p[0] for p in lows]; ys = [p[1] for p in lows]
    m = len(lows)
    mx = sum(xs) / m; my = sum(ys) / m
    sxy = sum((xs[k] - mx) * (ys[k] - my) for k in range(m))
    sxx = sum((xs[k] - mx) ** 2 for k in range(m))
    if sxx == 0:
        return None
    sl = sxy / sxx
    itc = my - sl * mx
    if sl <= 0:
        return None
    inl = sum(1 for idx, pr in lows if abs(sl * idx + itc - pr) <= pr * 0.015)
    span = max(xs) - min(xs)
    if inl >= 3 and span >= 40:
        return (sl, itc, n)
    return None


def _stock_trendline(daily_df):
    """严格上升趋势线: 有有效上升线返回 'up'，否则 'none'"""
    return "up" if _trendline_params(daily_df) else "none"


def _dir_at(rows, dstr, short=20, long=60):
    """某交易日的大盘/行业方向（沿用研究 market_direction 口径：取该日及之前收盘）"""
    if not rows:
        return None
    date_idx = {r["date"]: i for i, r in enumerate(rows)}
    i = date_idx.get(dstr)
    if i is None or i < long:
        return None
    closes = [r["close"] for r in rows]
    return _direction(closes[:i + 1], short, long)


def analyze_layers(code, daily_df):
    """返回层级状态: 大盘/行业/个股 + 共振"""
    ind = find_ind(code)
    mkt_rows = get_market_kline()
    mkt_dir = _direction([r["close"] for r in mkt_rows]) if mkt_rows else "unknown"
    ind_dir = "unknown"
    ind_rows = _load_ind_cache().get(ind) if ind else None
    if ind_rows and len(ind_rows) >= 60:
        ind_dir = _direction([r["close"] for r in ind_rows])
    # 个股趋势：与单股页/机会扫描统一，用严格趋势判断(analysis.analyze_trend)
    _tr = an.analyze_trend(daily_df, "日线")
    _dir = _tr.get("direction", "sideways")
    stl = _dir if _dir != "sideways" else "none"
    resonance_bull = (mkt_dir == "up" and ind_dir == "up" and stl == "up")
    resonance_bear = (mkt_dir == "down" and ind_dir == "down" and stl == "down")
    return {
        "market": {"name": "上证指数", "direction": mkt_dir},
        "industry": {"name": ind or "未知", "direction": ind_dir},
        "stock": {"trendline": stl},
        "resonance_bull": resonance_bull,
        "resonance_bear": resonance_bear,
    }


def _trendline_params_rows(rows, T):
    """用 rows[:T] 拟合上升趋势线, 返回 (sl, itc) 或 None（list 版，避免 DataFrame 切片开销）"""
    start = max(0, T - 120)
    lows = []
    for j in range(start, T - 1):
        if j - 1 >= start and rows[j]["low"] <= rows[j - 1]["low"] and rows[j]["low"] <= rows[j + 1]["low"]:
            lows.append((j, rows[j]["low"]))
    m = len(lows)
    if m < 3:
        return None
    xs = [p[0] for p in lows]; ys = [p[1] for p in lows]
    mx = sum(xs) / m; my = sum(ys) / m
    sxy = sum((xs[k] - mx) * (ys[k] - my) for k in range(m))
    sxx = sum((xs[k] - mx) ** 2 for k in range(m))
    if sxx == 0:
        return None
    sl = sxy / sxx
    itc = my - sl * mx
    if sl <= 0:
        return None
    inl = sum(1 for idx, pr in lows if abs(sl * idx + itc - pr) <= pr * 0.015)
    span = max(xs) - min(xs)
    if inl >= 3 and span >= 40:
        return (sl, itc)
    return None


def analyze_exit(code, daily_df):
    """离场状态机（基于回测: 破线+大盘转弱双确认可避免卖飞, 破线后回撤10%止损控风险）
    no_position: 近200日无三层共振建仓 -> 无持仓
    hold       : 趋势完好, 持有
    watch      : 已破线但大盘未转弱 -> 双确认中, 继续持有(避免卖飞)
    exit_signal: 破线 + 大盘转弱 双确认 -> 离场
    stop       : 破线后回撤建仓价>=10% -> 止损
    """
    empty = {"holding": False, "state": "no_position", "desc": "近200日无三层共振建仓点，无持仓", "entry": None}
    if daily_df is None or len(daily_df) < 150:
        return empty
    rows = daily_df.to_dict("records")
    n = len(rows)
    closes = [r["close"] for r in rows]

    ind = find_ind(code)
    mkt_rows = get_market_kline()
    ind_rows = _load_ind_cache().get(ind) if ind else None
    mkt_dir = _direction([r["close"] for r in mkt_rows]) if mkt_rows else "unknown"
    # 预构建日期索引
    mkt_idx = {r["date"]: i for i, r in enumerate(mkt_rows)} if mkt_rows else {}
    ind_idx_map = {r["date"]: i for i, r in enumerate(ind_rows)} if ind_rows else {}
    mkt_closes = [r["close"] for r in mkt_rows] if mkt_rows else []
    ind_closes = [r["close"] for r in ind_rows] if ind_rows else []

    def _dir_at_idx(idx_map, clos, dstr, short=20, long=60):
        i = idx_map.get(dstr)
        if i is None or i < long:
            return None
        return _direction(clos[:i + 1], short, long)

    # 从后往前找最近一次三层共振建仓日
    entry = None
    for T in range(n - 1, 149, -1):
        dstr = rows[T]["date"]
        md_T = _dir_at_idx(mkt_idx, mkt_closes, dstr) if mkt_rows else None
        if md_T != "up":
            continue  # 便宜过滤: 大盘非up直接跳过
        wd_T = _dir_at_idx(ind_idx_map, ind_closes, dstr) if ind_rows else None
        if wd_T != "up":
            continue
        tl = _trendline_params_rows(rows, T)  # 贵操作放最后
        if not tl:
            continue
        entry = {"T": T, "px": closes[T], "sl": tl[0], "itc": tl[1]}
        break
    if not entry:
        return empty
    if n - 1 - entry["T"] > 200:
        return empty

    cur = n - 1
    line_v = entry["sl"] * cur + entry["itc"]
    broken = closes[cur] < line_v
    dd = closes[cur] / entry["px"] - 1
    mkt_weak = (mkt_dir == "down")
    days = cur - entry["T"]
    # 牛熊离场切换：牛市双确认 / 熊市破线即走
    try:
        import env_judge
        style, _ = env_judge.exit_style()
    except Exception:
        style = "hold"

    if not broken:
        state, label, reason = "hold", "持有", "未跌破上升趋势线，趋势完好"
    elif dd <= -0.10:
        state, label, reason = "stop", "止损", f"破线后回撤已达 {dd*100:.1f}%（≤-10%），触发止损"
    elif style == "exit":
        state, label, reason = "exit_signal", "破线离场", "跌破上升趋势线，破线即走（当前离场风格）"
    elif mkt_weak:
        state, label, reason = "exit_signal", "双确认离场", "跌破上升趋势线 + 大盘转弱，双确认离场信号"
    else:
        state, label, reason = "watch", "待确认", "已破线但大盘未转弱，继续持有（避免卖飞，等大盘确认）"

    return {
        "holding": True,
        "state": state,
        "label": label,
        "desc": reason,
        "entry": {
            "date": rows[entry["T"]]["date"],
            "price": round(entry["px"], 2),
            "days_ago": days,
        },
        "broken": broken,
        "mkt_weak": mkt_weak,
        "drawdown": round(dd * 100, 1),
        "market_dir": mkt_dir,
        "exit_style": style,
    }
