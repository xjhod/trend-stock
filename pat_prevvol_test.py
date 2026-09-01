# -*- coding: utf-8 -*-
"""形态前量能状态 → 形态可靠性 验证
核心假设：形态是之前运动的产物，形态前的量能积累携带信息。
  - 底部看涨形态（下跌趋势中出现）：形态前缩量/地量（卖压衰竭）→ 更可靠
  - 顶部看跌形态（上升趋势中出现）：形态前放量（天量滞涨）→ 更可靠
量能水平 = 形态前5日均量 / 前60日均量（相对自身历史水平，消除个股差异）
入场：形态日 T 收盘（形态前信息在 T 已知，无前视偏差）
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


def prev_vol_level(rows, i):
    """形态前量能水平 = 前5日均量 / 前60日均量（T-5~T-1 相对 T-60~T-6）"""
    if i < 11:
        return None
    v5 = sum(rows[k]['volume'] for k in range(i - 5, i)) / 5
    s60 = max(0, i - 60)
    v60 = sum(rows[k]['volume'] for k in range(s60, i - 5)) / max(1, (i - 5) - s60)
    if v60 <= 0:
        return None
    return v5 / v60


def band_name(level):
    if level < 0.6: return "A地量(<0.6)"
    if level < 0.9: return "B缩量(0.6-0.9)"
    if level < 1.2: return "C平量(0.9-1.2)"
    return "D放量(≥1.2)"


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
    random.seed(SEED)
    sample = random.sample(codes, n_sample)
    print(f"随机抽取 {n_sample} 只，验证'形态前量能水平'对形态可靠性的影响")

    agg = {}
    bands = ["A地量(<0.6)", "B缩量(0.6-0.9)", "C平量(0.9-1.2)", "D放量(≥1.2)"]
    ok = 0
    for code in sample:
        d = fetch(code)
        if d.empty:
            continue
        rows = bt._rows_from_df(d)
        ok += 1
        for idx, ns in detect_patterns_raw(rows):
            if idx + max(HORIZONS) >= len(rows):
                continue
            lvl = prev_vol_level(rows, idx)
            if lvl is None:
                continue
            bnd = band_name(lvl)
            for nm in ns:
                if nm in bt.BEAR:
                    dirv = "bear"
                elif nm in bt.BULL:
                    dirv = "bull"
                else:
                    continue
                c0 = rows[idx]['close']
                for H in HORIZONS:
                    f = rows[idx + H]['close']
                    hit = (f > c0) if dirv == "bull" else (f < c0)
                    key = (dirv, bnd, H)
                    a = agg.setdefault(key, {"hit": 0, "total": 0})
                    a["total"] += 1
                    if hit:
                        a["hit"] += 1
        time.sleep(0.12)
    print(f"成功 {ok}/{n_sample} 只\n")

    print("=" * 76)
    for dirv, dlab in (("bull", "看涨形态（下跌趋势中出现，前缩量应更可靠）"),
                       ("bear", "看跌形态（上升趋势中出现，前放量应更可靠）")):
        print(f"\n◆ {dlab}")
        print(f"{'前量能档':<14}{'样本':<8}{'3日胜率':<14}{'6日胜率':<14}{'10日胜率':<14}")
        for bnd in bands:
            cells = []
            tot = None
            for H in HORIZONS:
                a = agg.get((dirv, bnd, H), {"hit": 0, "total": 0})
                t = a["total"]
                if t:
                    tot = t
                    cells.append(f"{a['hit']/t*100:.1f}%({a['hit']}/{t})")
                else:
                    cells.append("--")
            print(f"{bnd:<14}{tot if tot else '--':<8}{cells[0]:<14}{cells[1]:<14}{cells[2]:<14}")
    print("\n" + "=" * 76)


if __name__ == "__main__":
    main()
