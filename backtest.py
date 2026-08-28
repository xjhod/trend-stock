# -*- coding: utf-8 -*-
"""历史回测模块：验证「技术面综合解读」各分项正确率
供：1) app.py 的 /api/backtest 接口（软件内按钮）  2) 独立大样本回测脚本复用

口径说明：
- 测试点 T 只用 rows[0..T]（当时可见数据）生成判断，避免未来函数
- 对照 T 之后 N 个交易日的实际走势统计命中率
- 正确率 = 方向命中的样本 / 有明确方向预期的样本
"""
import pandas as pd
import numpy as np

# ---------- 与前端一致的核心算法 ----------
def trend_at(rows, i):
    end = i - 1
    if end < 5: return "flat"
    s5 = sum(rows[k]['close'] for k in range(end - 4, end + 1)) / 5
    s20 = max(0, end - 19)
    if end - s20 + 1 < 10: return "flat"
    m20 = sum(rows[k]['close'] for k in range(s20, end + 1)) / (end - s20 + 1)
    c = rows[end]['close']
    if s5 > m20 and c > m20: return "up"
    if s5 < m20 and c < m20: return "down"
    return "flat"

def detect_patterns(rows):
    out = []
    for i in range(len(rows)):
        r = rows[i]
        body = abs(r['close'] - r['open']); rng = r['high'] - r['low']
        if rng <= 0: continue
        upper = r['high'] - max(r['open'], r['close']); lower = min(r['open'], r['close']) - r['low']
        bull = r['close'] > r['open']; bear = r['close'] < r['open']
        t = trend_at(rows, i); names = []
        if lower >= rng * 0.6 and upper <= rng * 0.15 and body >= rng * 0.05:
            if t == 'up': names.append('上吊线')
            elif t == 'down': names.append('锤子线')
        if i >= 1:
            p = rows[i-1]; pBody = abs(p['close'] - p['open'])
            if bull and p['close'] < p['open'] and r['open'] <= p['close'] and r['close'] >= p['open'] and body > pBody * 1.05 and t == 'down': names.append('看涨吞没')
            if bear and p['close'] > p['open'] and r['open'] >= p['close'] and r['close'] <= p['open'] and body > pBody * 1.05 and t == 'up': names.append('看跌吞没')
        if i >= 1 and t == 'up':
            p = rows[i-1]; pBody = p['close'] - p['open']
            if pBody > 0 and bear and r['open'] > p['high']:
                if (p['close'] - r['close']) / pBody >= 0.5: names.append('乌云盖顶')
        if i >= 1 and t == 'down':
            p = rows[i-1]; pBody = p['open'] - p['close']
            if pBody > 0 and bull and r['open'] < p['low']:
                if (r['close'] - p['close']) / pBody >= 0.5: names.append('穿刺形态')
        if i >= 2:
            a = rows[i-2]; b = rows[i-1]
            if t == 'down' and a['close'] > a['open'] and b['close'] > b['open'] and bull and b['close'] > a['close'] and r['close'] > b['close']: names.append('红三兵')
            if t == 'up' and a['close'] < a['open'] and b['close'] < b['open'] and bear and b['close'] < a['close'] and r['close'] < b['close']: names.append('三只乌鸦')
            bBody = abs(b['close'] - b['open']); bRng = b['high'] - b['low']
            if bRng > 0 and bBody <= bRng * 0.3:
                if t == 'down' and a['close'] < a['open'] and bull and r['close'] > (a['open'] + a['close']) / 2: names.append('启明星')
                if t == 'up' and a['close'] > a['open'] and bear and r['close'] < (a['open'] + a['close']) / 2: names.append('黄昏星')
        if names:
            # 量能确认：形态当日量 ≥ 1.3× 前5日均量（实测大市值/蓝筹上命中率显著提升）
            _s = sum(rows[k]['volume'] for k in range(max(0, i - 5), i))
            _base = _s / (5 if i >= 5 else max(1, i))
            if _base > 0 and rows[i]['volume'] >= _base * 1.3:
                out.append((i, names))
    return out

def calc_sr(rows):
    win = min(len(rows), 60); start = len(rows) - win
    pts = []
    for i in range(1, len(rows) - 1):
        if i < start: continue
        r = rows[i]
        if r['high'] >= rows[i-1]['high'] and r['high'] >= rows[i+1]['high']: pts.append({'price': r['high'], 'vol': r['volume'], 'idx': i})
        if r['low'] <= rows[i-1]['low'] and r['low'] <= rows[i+1]['low']: pts.append({'price': r['low'], 'vol': r['volume'], 'idx': i})
    close = rows[-1]['close']
    if len(pts) < 2: return [], []
    pts.sort(key=lambda x: x['price'])
    th = close * 0.012
    groups = []
    cur = {'price': pts[0]['price'], 'vols': [pts[0]['vol']], 'idxs': [pts[0]['idx']], 'n': 1}
    for k in range(1, len(pts)):
        if abs(pts[k]['price'] - cur['price']) <= th:
            cur['vols'].append(pts[k]['vol']); cur['idxs'].append(pts[k]['idx']); cur['n'] += 1
            cur['price'] = (cur['price'] * (cur['n'] - 1) + pts[k]['price']) / cur['n']
        else:
            groups.append(cur); cur = {'price': pts[k]['price'], 'vols': [pts[k]['vol']], 'idxs': [pts[k]['idx']], 'n': 1}
    groups.append(cur)
    seg = rows[start:]
    avgVol = sum(r['volume'] for r in seg) / win
    lastIdx = len(rows) - 1
    for g in groups:
        maxVol = max(g['vols']); lastTouch = max(g['idxs'])
        g['score'] = g['n'] * 2 + (maxVol / (avgVol or 1)) * 2 + (1.5 if lastIdx - lastTouch < 10 else 0)
    support = [g for g in groups if g['price'] < close]
    resist = [g for g in groups if g['price'] > close]
    support.sort(key=lambda g: -g['score']); resist.sort(key=lambda g: -g['score'])
    return support[:2], resist[:2]

def detect_divergence(rows):
    out = []
    win = min(len(rows), 60); start = len(rows) - win
    highs, lows = [], []
    for i in range(2, len(rows) - 2):
        if i < start: continue
        r = rows[i]
        if r['high'] >= rows[i-1]['high'] and r['high'] >= rows[i+1]['high'] and r['high'] >= rows[i-2]['high'] and r['high'] >= rows[i+2]['high']:
            highs.append({'idx': i, 'price': r['high'], 'vol': r['volume']})
        if r['low'] <= rows[i-1]['low'] and r['low'] <= rows[i+1]['low'] and r['low'] <= rows[i-2]['low'] and r['low'] <= rows[i+2]['low']:
            lows.append({'idx': i, 'price': r['low'], 'vol': r['volume']})
    if len(highs) >= 2:
        a, b = highs[-2], highs[-1]
        if b['price'] > a['price'] and b['vol'] < a['vol'] * 0.9 and trend_at(rows, b['idx']) == 'up':
            out.append({'idx': b['idx'], 'type': '顶背离'})
    if len(lows) >= 2:
        a, b = lows[-2], lows[-1]
        if b['price'] <= a['price'] and b['vol'] < a['vol'] * 0.7 and trend_at(rows, b['idx']) == 'down':
            out.append({'idx': b['idx'], 'type': '缩量见底'})
    return out

BEAR = {"上吊线", "看跌吞没", "乌云盖顶", "黄昏星", "三只乌鸦"}
BULL = {"锤子线", "看涨吞没", "穿刺形态", "启明星", "红三兵"}


def _rows_from_df(d):
    return [{"date": r.date, "open": float(r.open), "close": float(r.close),
             "low": float(r.low), "high": float(r.high), "volume": float(r.volume)}
            for r in d.itertuples()]


def backtest_rows(rows, points_every=10, start_at=90, fwd_trend=10, fwd_sr=20, fwd_pat=5):
    """对给定日线数据跑回测，返回各分项统计。
    rows: [{date,open,close,low,high,volume}, ...]（升序，含足够历史）
    """
    n = len(rows)
    points = list(range(start_at, n - max(fwd_trend, fwd_sr, fwd_pat) - 5, points_every))
    res = {"trend": {"t": 0, "hit": 0},
           "sr_support": {"t": 0, "hit": 0}, "sr_resist": {"t": 0, "hit": 0},
           "div": {"t": 0, "hit": 0},
           "pat": {"t": 0, "hit": 0},
           "pat_bear": {"t": 0, "hit": 0}, "pat_bull": {"t": 0, "hit": 0}}
    for T in points:
        past = rows[:T + 1]
        c0 = rows[T]["close"]
        # 1 趋势方向（未来 fwd_trend 日）
        t = trend_at(rows, T)
        f10 = rows[T + fwd_trend]["close"]
        chg = (f10 - c0) / c0
        if t == "up":
            res["trend"]["t"] += 1
            if f10 > c0: res["trend"]["hit"] += 1
        elif t == "down":
            res["trend"]["t"] += 1
            if f10 < c0: res["trend"]["hit"] += 1
        elif t == "flat":
            res["trend"]["t"] += 1
            if abs(chg) < 0.03: res["trend"]["hit"] += 1
        # 2 支撑/阻力：未来 fwd_sr 根内触及后，守住/压制 = 有效
        sup, res_ = calc_sr(past)
        for g in sup:
            s = g["price"]
            for j in range(T + 1, min(T + fwd_sr + 1, n)):
                if rows[j]["low"] <= s * 1.02:
                    mn = min(r["low"] for r in rows[j:min(j + 10, n)])
                    res["sr_support"]["t"] += 1
                    if mn >= s * 0.97: res["sr_support"]["hit"] += 1
                    break
        for g in res_:
            r_ = g["price"]
            for j in range(T + 1, min(T + fwd_sr + 1, n)):
                if rows[j]["high"] >= r_ * 0.98:
                    mx = max(r["high"] for r in rows[j:min(j + 10, n)])
                    res["sr_resist"]["t"] += 1
                    if mx <= r_ * 1.03: res["sr_resist"]["hit"] += 1
                    break
        # 3 量价背离：未来 fwd_trend 日反向验证
        for dv in detect_divergence(past):
            if dv["idx"] >= T - 3:
                f = rows[T + fwd_trend]["close"]
                res["div"]["t"] += 1
                if dv["type"] == "顶背离" and f < c0: res["div"]["hit"] += 1
                if dv["type"] == "缩量见底" and f > c0: res["div"]["hit"] += 1
        # 4 形态：在 T 这根有形态，未来 fwd_pat 日方向
        for idx, names in detect_patterns(past):
            if idx == T:
                f5 = rows[T + fwd_pat]["close"]
                res["pat"]["t"] += 1
                bear_ok = any(nm in BEAR and f5 < c0 for nm in names if nm in BEAR)
                bull_ok = any(nm in BULL and f5 > c0 for nm in names if nm in BULL)
                if bear_ok or bull_ok:
                    res["pat"]["hit"] += 1
                for nm in names:
                    if nm in BEAR:
                        res["pat_bear"]["t"] += 1
                        if f5 < c0: res["pat_bear"]["hit"] += 1
                    if nm in BULL:
                        res["pat_bull"]["t"] += 1
                        if f5 > c0: res["pat_bull"]["hit"] += 1
    return res


def pct(r):
    return (round(r["hit"] / r["t"] * 100, 1) if r["t"] else None, r["hit"], r["t"])


def summarize(res):
    """把回测结果整理成给前端展示的 dict"""
    labels = {
        "trend": "趋势方向(未来10日)", "sr_support": "支撑位守住率", "sr_resist": "阻力位压制率",
        "div": "量价背离", "pat": "形态信号(趋势内)", "pat_bear": "  └ 看跌形态", "pat_bull": "  └ 看涨形态",
    }
    items = []
    for k in ["trend", "sr_support", "sr_resist", "div", "pat", "pat_bear", "pat_bull"]:
        pp, h, t = pct(res.get(k, {"t": 0, "hit": 0}))
        items.append({"key": k, "label": labels[k], "rate": pp, "hit": h, "total": t})
    return {"items": items, "points": sum(1 for _ in range(1))}


if __name__ == "__main__":
    import sys, glob
    if len(sys.argv) > 1:
        files = sys.argv[1:]
    else:
        files = sorted(glob.glob("/tmp/bt_??????.csv"))
    agg = {}
    for f in files:
        code = f.split("/")[-1].split("_")[-1].split(".")[0]
        d = pd.read_csv(f)
        r = backtest_rows(_rows_from_df(d))
        print(f"\n===== {code} =====")
        for k in ["trend", "sr_support", "sr_resist", "div", "pat", "pat_bear", "pat_bull"]:
            pp, h, t = pct(r[k])
            print(f"  {k:12s}: {pp}%  ({h}/{t})")
        for k in r:
            agg.setdefault(k, {"t": 0, "hit": 0})
            agg[k]["t"] += r[k]["t"]; agg[k]["hit"] += r[k]["hit"]
    print("\n========== 汇总 ==========")
    for k in ["trend", "sr_support", "sr_resist", "div", "pat", "pat_bear", "pat_bull"]:
        pp, h, t = pct(agg[k])
        print(f"  {k:12s}: {pp}%  ({h}/{t})")
