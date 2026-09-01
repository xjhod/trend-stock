# -*- coding: utf-8 -*-
"""层级趋势(大盘→行业→个股) + 形态 组合验证（优化版：预计算方向序列，O(1)查表）
框架（严格无前视）：
  T 收盘识别形态 + 用 T 及之前的可见数据判断大盘/行业/个股方向
  T+1 开盘买入 → T+1+H 收盘评估方向一致性
"""
import json
import random
import sys
import time
import pandas as pd

import backtest as bt
import data_fetcher as df
import layers

HORIZONS = (3, 6, 10)
SEED = 20260901


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


def precompute_dirs(rows, short=20, long=60):
    """预计算每个位置的方向(up/down/side/unknown)，用前缀和O(n)。"""
    n = len(rows)
    closes = [r["close"] for r in rows]
    pref = [0.0] * (n + 1)
    for i in range(n):
        pref[i + 1] = pref[i] + closes[i]
    dirs = []
    for i in range(n):
        if i < long - 1:
            dirs.append("unknown")
            continue
        ma_s = (pref[i + 1] - pref[i + 1 - short]) / short
        ma_l = (pref[i + 1] - pref[i + 1 - long]) / long
        c = closes[i]
        if c > ma_s and ma_s > ma_l:
            dirs.append("up")
        elif c < ma_s and ma_s < ma_l:
            dirs.append("down")
        else:
            dirs.append("side")
    return dirs


def fetch(code):
    try:
        d = df.get_kline(code, period="daily", limit=1000)
        if d is not None and len(d) >= 200:
            return d
    except Exception:
        pass
    return pd.DataFrame()


def main():
    n_sample = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    pool = json.load(open("highfit_pool.json", encoding="utf-8"))
    codes = [p["code"] if isinstance(p, dict) else str(p) for p in pool]
    ind_of = {p["code"] if isinstance(p, dict) else str(p):
              (p.get("ind", "") if isinstance(p, dict) else "") for p in pool}
    random.seed(SEED)
    if n_sample >= len(codes):
        sample = codes
        print(f"全量 {len(sample)} 只（30亿池，4年历史）· 层级趋势+形态 组合验证")
    else:
        sample = random.sample(codes, n_sample)
        print(f"随机抽取 {n_sample} 只（30亿池，4年历史）· 层级趋势+形态 组合验证")

    # 预计算大盘方向序列
    mkt_rows = layers.get_market_kline(limit=1000) or []
    mkt_dirs = precompute_dirs(mkt_rows)
    mkt_date_idx = {r["date"]: i for i, r in enumerate(mkt_rows)}

    # 预计算各行业方向序列
    ind_cache = layers._load_ind_cache() or {}
    ind_dirs_map = {}
    ind_date_idx_map = {}
    for ind, irows in ind_cache.items():
        ind_dirs_map[ind] = precompute_dirs(irows)
        ind_date_idx_map[ind] = {r["date"]: i for i, r in enumerate(irows)}

    from collections import defaultdict
    agg = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"hit": 0, "total": 0})))
    GROUPS = ["G0基准", "G1个股up", "G2个股+行业up", "G3全共振up", "G4全共振down", "G5大盘down"]
    ok = 0
    t0 = time.time()
    for ci, code in enumerate(sample):
        if (ci + 1) % 200 == 0:
            print(f"  进度 {ci+1}/{len(sample)} ({time.time()-t0:.0f}s)")
        d = fetch(code)
        if d.empty:
            continue
        rows = bt._rows_from_df(d)
        ok += 1
        ind = ind_of.get(code, "")
        stock_dirs = precompute_dirs(rows)
        ind_dirs = ind_dirs_map.get(ind)
        ind_date_idx = ind_date_idx_map.get(ind)
        for idx, ns in detect_patterns_raw(rows):
            if idx + 1 + max(HORIZONS) >= len(rows):
                continue
            dstr = rows[idx]['date']
            stock_dir = stock_dirs[idx] if idx < len(stock_dirs) else "unknown"
            mkt_i = mkt_date_idx.get(dstr)
            mkt_dir = mkt_dirs[mkt_i] if mkt_i is not None and mkt_i < len(mkt_dirs) else "unknown"
            ind_dir = "unknown"
            if ind_dirs and ind_date_idx:
                ind_i = ind_date_idx.get(dstr)
                if ind_i is not None and ind_i < len(ind_dirs):
                    ind_dir = ind_dirs[ind_i]
            stock_up = stock_dir == "up"; ind_up = ind_dir == "up"; mkt_up = mkt_dir == "up"
            stock_dn = stock_dir == "down"; ind_dn = ind_dir == "down"; mkt_dn = mkt_dir == "down"
            groups = [True, stock_up, stock_up and ind_up,
                      stock_up and ind_up and mkt_up,
                      stock_dn and ind_dn and mkt_dn, mkt_dn]
            entry = rows[idx + 1]['open']
            for nm in ns:
                if nm in bt.BEAR:
                    dirv = "bear"
                elif nm in bt.BULL:
                    dirv = "bull"
                else:
                    continue
                for H in HORIZONS:
                    f = rows[idx + 1 + H]['close']
                    hit = (f > entry) if dirv == "bull" else (f < entry)
                    for gi, cond in enumerate(groups):
                        if not cond:
                            continue
                        gname = GROUPS[gi]
                        agg[gname][dirv][H]["total"] += 1
                        if hit:
                            agg[gname][dirv][H]["hit"] += 1
    print(f"成功 {ok}/{len(sample)} 只，总耗时 {time.time()-t0:.0f}s\n")

    print("=" * 76)
    for dirv, dlab in (("bull", "看涨形态（T+1开盘买入，看涨=命中）"),
                       ("bear", "看跌形态（T+1开盘买入，看跌=命中）")):
        print(f"\n◆ {dlab}")
        print(f"{'组合':<18}{'样本':<8}{'3日胜率':<14}{'6日胜率':<14}{'10日胜率':<14}")
        for g in GROUPS:
            cells = []; tot = None
            for H in HORIZONS:
                a = agg[g][dirv][H]
                t = a["total"]
                if t:
                    tot = t
                    cells.append(f"{a['hit']/t*100:.1f}%({a['hit']}/{t})")
                else:
                    cells.append("--")
            print(f"{g:<18}{tot if tot else '--':<8}{cells[0]:<14}{cells[1]:<14}{cells[2]:<14}")
    print("\n框架：T收盘识别形态+层级方向→T+1开盘买入。方向用20/60均线，全部取T时刻可见数据，无未来函数。4年历史(2022-07至今)。")


if __name__ == "__main__":
    main()
