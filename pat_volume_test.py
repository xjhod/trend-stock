# -*- coding: utf-8 -*-
"""形态 × 成交量 分级验证
对同一形态，按形态日量能倍数（当日量/前5日均量）分档，
对比各档的 3/6/10 日胜率，验证"放量程度对形态可靠性的影响"。
复用 backtest.detect_patterns 的形态识别逻辑（但去掉量能确认，改为按档分组统计）。
"""
import json
import random
import sys
import time
import pandas as pd

import backtest as bt
import data_fetcher as df

HORIZONS = (3, 6, 10)
SEED = 20260831


def detect_patterns_raw(rows):
    """同 bt.detect_patterns，但不做量能确认（返回所有形态位置）"""
    out = []
    for i in range(len(rows)):
        r = rows[i]
        body = abs(r['close'] - r['open']); rng = r['high'] - r['low']
        if rng <= 0: continue
        upper = r['high'] - max(r['open'], r['close']); lower = min(r['open'], r['close']) - r['low']
        bull = r['close'] > r['open']; bear = r['close'] < r['open']
        t = bt.trend_at(rows, i); names = []
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
            out.append((i, names))
    return out


def vol_ratio(rows, i):
    """形态日量能倍数 = 当日量 / 前5日均量"""
    _s = sum(rows[k]['volume'] for k in range(max(0, i - 5), i))
    _base = _s / (5 if i >= 5 else max(1, i))
    if _base <= 0:
        return 0
    return rows[i]['volume'] / _base


def band_name(ratio):
    if ratio < 0.8: return "A缩量(<0.8x)"
    if ratio < 1.3: return "B平量(0.8-1.3x)"
    if ratio < 2.0: return "C放量(1.3-2x)"
    return "D强放量(≥2x)"


def fetch(code):
    try:
        d = df.get_kline(code, period="daily", limit=1200)
        if d is not None and len(d) >= 200:
            return d
    except Exception as e:
        print(f"  [!] {code} 拉取失败: {e}")
    return pd.DataFrame()


def main():
    n_sample = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    pool = json.load(open("highfit_pool.json", encoding="utf-8"))
    codes = [p["code"] if isinstance(p, dict) else str(p) for p in pool]
    names = {p["code"]: p.get("name", "") for p in pool if isinstance(p, dict)}
    random.seed(SEED)
    sample = random.sample(codes, n_sample)
    print(f"随机抽取 {n_sample} 只，按量能分档统计形态胜率")

    # agg[(dir, band, H)] = {hit, total}
    agg = {}
    bands = ["A缩量(<0.8x)", "B平量(0.8-1.3x)", "C放量(1.3-2x)", "D强放量(≥2x)"]
    ok = 0
    for code in sample:
        d = fetch(code)
        if d.empty:
            continue
        rows = bt._rows_from_df(d)
        ok += 1
        for idx, ns in detect_patterns_raw(rows):
            ratio = vol_ratio(rows, idx)
            bnd = band_name(ratio)
            for nm in ns:
                if nm in bt.BEAR:
                    dirv = "bear"
                elif nm in bt.BULL:
                    dirv = "bull"
                else:
                    continue
                for H in HORIZONS:
                    if idx + H >= len(rows):
                        continue
                    f = rows[idx + H]['close']
                    c0 = rows[idx]['close']
                    key = (dirv, bnd, H)
                    a = agg.setdefault(key, {"hit": 0, "total": 0})
                    a["total"] += 1
                    if dirv == "bull" and f > c0:
                        a["hit"] += 1
                    elif dirv == "bear" and f < c0:
                        a["hit"] += 1
        time.sleep(0.15)
    print(f"成功 {ok}/{n_sample} 只\n")

    print("=" * 72)
    for dirv, dlab in (("bull", "看涨形态"), ("bear", "看跌形态")):
        print(f"\n◆ {dlab}")
        print(f"{'量能档':<16}{'样本':<7}{'3日胜率':<10}{'6日胜率':<10}{'10日胜率':<10}")
        for bnd in bands:
            cells = []
            tot_show = None
            for H in HORIZONS:
                a = agg.get((dirv, bnd, H), {"hit": 0, "total": 0})
                t = a["total"]
                if t:
                    tot_show = t
                    cells.append(f"{a['hit']/t*100:.1f}%({a['hit']}/{t})")
                else:
                    cells.append("--")
            print(f"{bnd:<16}{tot_show if tot_show else '--':<7}{cells[0]:<10}{cells[1]:<10}{cells[2]:<10}")
    print("\n" + "=" * 72)
    print("注：样本数 = 各档位形态出现的总次数（同一形态日可算多档之外只计一档）")


if __name__ == "__main__":
    main()
