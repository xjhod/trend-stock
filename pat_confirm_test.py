# -*- coding: utf-8 -*-
"""形态 + 后1-2日放量方向确认 验证（无前视偏差版：T+1确认后入场）
对比四组口径下形态的 3/6/10 日胜率：
  G0 基准：所有形态（无任何后验确认）
  G1 仅方向：形态后第1日朝形态方向走（看涨: T+1收盘>T日收盘；看跌: T+1收盘<T日收盘），不看量
  G2 方向+放量：第1日朝方向走 且 放量(量≥1.3×前5日均量)
  G3 2日内任一确认：第1日 或 第2日 有"朝方向走且放量"任一
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


def vol_ratio_to(rows, i):
    """第 i 日量能倍数（相对 i 前5日均量）"""
    _s = sum(rows[k]['volume'] for k in range(max(0, i - 5), i))
    _base = _s / (5 if i >= 5 else max(1, i))
    if _base <= 0:
        return 0
    return rows[i]['volume'] / _base


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
    print(f"随机抽取 {n_sample} 只，验证'形态+后1-2日放量方向确认'")

    # agg[(group, dirv, H)] = {hit,total}
    agg = {}
    GROUPS = ["G0基准", "G1仅方向", "G2方向+放量", "G3两日内确认"]
    ok = 0
    for code in sample:
        d = fetch(code)
        if d.empty:
            continue
        rows = bt._rows_from_df(d)
        ok += 1
        for idx, ns in detect_patterns_raw(rows):
            if idx + 2 + max(HORIZONS) >= len(rows):
                continue
            for nm in ns:
                if nm in bt.BEAR:
                    dirv = "bear"
                elif nm in bt.BULL:
                    dirv = "bull"
                else:
                    continue
                c0 = rows[idx]['close']
                # G1: 第1日方向
                dir1 = rows[idx + 1]['close'] > c0  # True=朝看涨方向
                dir1_ok = dir1 if dirv == "bull" else (not dir1)
                # 放量判定（第1、2日）
                v1 = vol_ratio_to(rows, idx + 1)
                v2 = vol_ratio_to(rows, idx + 2)
                d2 = rows[idx + 2]['close'] > rows[idx + 1]['close']  # 第2日相对第1日方向
                # 第2日"朝形态方向且放量"
                d2_ok = (d2 if dirv == "bull" else (not d2)) and v2 >= 1.3
                # 各组纳入
                g1_ok = dir1_ok
                g2_ok = dir1_ok and v1 >= 1.3
                g3_ok = (dir1_ok and v1 >= 1.3) or d2_ok
                # 无前视偏差口径：确认后 T+1 收盘入场，收益从 T+1 起算
                # G0 为对照：形态日 T 收盘直接入场（无确认）
                entry0 = c0
                entry1 = rows[idx + 1]['close']
                for H in HORIZONS:
                    # G0 入场 T，看 T+H
                    f0 = rows[idx + H]['close']
                    hit0 = (f0 > entry0) if dirv == "bull" else (f0 < entry0)
                    # G1/G2/G3 入场 T+1，看 T+1+H
                    if idx + 1 + H < len(rows):
                        f1 = rows[idx + 1 + H]['close']
                        hit1 = (f1 > entry1) if dirv == "bull" else (f1 < entry1)
                    else:
                        hit1 = None
                    conds = [True, g1_ok, g2_ok, g3_ok]
                    hits = [hit0, hit1, hit1, hit1]
                    for gi, cond in enumerate(conds):
                        if not cond or hits[gi] is None:
                            continue
                        key = (GROUPS[gi], dirv, H)
                        a = agg.setdefault(key, {"hit": 0, "total": 0})
                        a["total"] += 1
                        if hits[gi]:
                            a["hit"] += 1
        time.sleep(0.12)
    print(f"成功 {ok}/{n_sample} 只\n")

    print("=" * 76)
    for dirv, dlab in (("bull", "看涨形态"), ("bear", "看跌形态")):
        print(f"\n◆ {dlab}")
        print(f"{'口径':<14}{'样本':<8}{'3日胜率':<14}{'6日胜率':<14}{'10日胜率':<14}")
        for g in GROUPS:
            cells = []
            tot = None
            for H in HORIZONS:
                a = agg.get((g, dirv, H), {"hit": 0, "total": 0})
                t = a["total"]
                if t:
                    tot = t
                    cells.append(f"{a['hit']/t*100:.1f}%({a['hit']}/{t})")
                else:
                    cells.append("--")
            print(f"{g:<14}{tot if tot else '--':<8}{cells[0]:<14}{cells[1]:<14}{cells[2]:<14}")
    print("\n" + "=" * 76)


if __name__ == "__main__":
    main()
