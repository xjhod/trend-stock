# -*- coding: utf-8 -*-
"""多弱信号组合验证：逆势共振 + 形态强度 + 量能趋势放大
框架：T收盘识别形态→T+1开盘买入→T+1+H评估。全部因子在T及之前可得。
组合维度：
  L1 层级共振：G4全共振down(看涨) / G3全共振up(看跌)
  L2 形态强度：强形态(吞没实体比>1.5/锤子下影比>2.5/乌云插入>70%/穿刺插入>70%)
  L3 量能趋势：前5日均量/前10日均量>1.2(放大)
"""
import json
import random
import sys
import time
import pandas as pd
from collections import defaultdict

import backtest as bt
import data_fetcher as df
import layers

HORIZONS = (3, 6, 10)
SEED = 20260901


def detect_with_strength(rows):
    """返回 [(idx, name, dirv, strength)]，strength=1强/0普通"""
    out = []
    for i in range(len(rows)):
        r = rows[i]
        body = abs(r['close'] - r['open']); rng = r['high'] - r['low']
        if rng <= 0: continue
        upper = r['high'] - max(r['open'], r['close']); lower = min(r['open'], r['close']) - r['low']
        bull = r['close'] > r['open']; bear = r['close'] < r['open']
        t = bt.trend_at(rows, i)
        # 锤子/上吊
        if lower >= rng * 0.6 and upper <= rng * 0.15 and body >= rng * 0.05:
            strength = 1 if (lower / max(body, 0.001) >= 2.5) else 0
            if t == 'up': out.append((i, '上吊线', 'bear', strength))
            elif t == 'down': out.append((i, '锤子线', 'bull', strength))
        if i >= 1:
            p = rows[i-1]; pBody = abs(p['close'] - p['open'])
            # 看涨吞没
            if bull and p['close'] < p['open'] and r['open'] <= p['close'] and r['close'] >= p['open'] and body > pBody * 1.05 and t == 'down':
                strength = 1 if (body / max(pBody, 0.001) >= 1.5) else 0
                out.append((i, '看涨吞没', 'bull', strength))
            # 看跌吞没
            if bear and p['close'] > p['open'] and r['open'] >= p['close'] and r['close'] <= p['open'] and body > pBody * 1.05 and t == 'up':
                strength = 1 if (body / max(pBody, 0.001) >= 1.5) else 0
                out.append((i, '看跌吞没', 'bear', strength))
        # 乌云盖顶
        if i >= 1 and t == 'up':
            p = rows[i-1]; pBody = p['close'] - p['open']
            if pBody > 0 and bear and r['open'] > p['high']:
                insert = (p['close'] - r['close']) / pBody
                if insert >= 0.5:
                    strength = 1 if insert >= 0.7 else 0
                    out.append((i, '乌云盖顶', 'bear', strength))
        # 穿刺形态
        if i >= 1 and t == 'down':
            p = rows[i-1]; pBody = p['open'] - p['close']
            if pBody > 0 and bull and r['open'] < p['low']:
                insert = (r['close'] - p['close']) / pBody
                if insert >= 0.5:
                    strength = 1 if insert >= 0.7 else 0
                    out.append((i, '穿刺形态', 'bull', strength))
        # 启明星/黄昏星/红三兵/三只乌鸦（强度暂设0，后续可加）
        if i >= 2:
            a = rows[i-2]; b = rows[i-1]
            if t == 'down' and a['close'] > a['open'] and b['close'] > b['open'] and bull and b['close'] > a['close'] and r['close'] > b['close']:
                out.append((i, '红三兵', 'bull', 0))
            if t == 'up' and a['close'] < a['open'] and b['close'] < b['open'] and bear and b['close'] < a['close'] and r['close'] < b['close']:
                out.append((i, '三只乌鸦', 'bear', 0))
            bBody = abs(b['close'] - b['open']); bRng = b['high'] - b['low']
            if bRng > 0 and bBody <= bRng * 0.3:
                if t == 'down' and a['close'] < a['open'] and bull and r['close'] > (a['open'] + a['close']) / 2:
                    out.append((i, '启明星', 'bull', 0))
                if t == 'up' and a['close'] > a['open'] and bear and r['close'] < (a['open'] + a['close']) / 2:
                    out.append((i, '黄昏星', 'bear', 0))
    return out


def precompute_dirs(rows, short=20, long=60):
    n = len(rows); closes = [r["close"] for r in rows]
    pref = [0.0] * (n + 1)
    for i in range(n): pref[i + 1] = pref[i] + closes[i]
    dirs = []
    for i in range(n):
        if i < long - 1: dirs.append("unknown"); continue
        ma_s = (pref[i+1] - pref[i+1-short]) / short
        ma_l = (pref[i+1] - pref[i+1-long]) / long
        c = closes[i]
        if c > ma_s and ma_s > ma_l: dirs.append("up")
        elif c < ma_s and ma_s < ma_l: dirs.append("down")
        else: dirs.append("side")
    return dirs


def vol_trend_at(rows, idx):
    """前5日均量/前10日均量，>1.2为放大"""
    if idx < 11: return None
    v5 = sum(rows[k]['volume'] for k in range(idx-5, idx)) / 5
    v10 = sum(rows[k]['volume'] for k in range(idx-10, idx)) / 10
    if v10 <= 0: return None
    return v5 / v10


def fetch(code):
    try:
        d = df.get_kline(code, period="daily", limit=1000)
        if d is not None and len(d) >= 200: return d
    except Exception: pass
    return pd.DataFrame()


def main():
    n_sample = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    pool = json.load(open("highfit_pool.json", encoding="utf-8"))
    codes = [p["code"] if isinstance(p, dict) else str(p) for p in pool]
    ind_of = {p["code"] if isinstance(p, dict) else str(p):
              (p.get("ind", "") if isinstance(p, dict) else "") for p in pool}
    random.seed(SEED)
    sample = codes if n_sample >= len(codes) else random.sample(codes, n_sample)
    print(f"{'全量' if n_sample>=len(codes) else '随机'+str(n_sample)} {len(sample)} 只 · 多弱信号组合验证")

    mkt_rows = layers.get_market_kline(limit=1000) or []
    mkt_dirs = precompute_dirs(mkt_rows)
    mkt_date_idx = {r["date"]: i for i, r in enumerate(mkt_rows)}
    ind_cache = layers._load_ind_cache() or {}
    ind_dirs_map = {ind: precompute_dirs(irows) for ind, irows in ind_cache.items()}
    ind_date_idx_map = {ind: {r["date"]: i for i, r in enumerate(irows)} for ind, irows in ind_cache.items()}

    # 组合键: (dirv, combo_name) -> H -> {hit,total}
    combos = ["基准", "逆势共振", "逆势+强形态", "逆势+量能放大", "逆势+强形态+量能放大",
              "仅强形态", "仅量能放大", "强形态+量能放大"]
    agg = defaultdict(lambda: defaultdict(lambda: {"hit": 0, "total": 0}))
    ok = 0; t0 = time.time()
    for ci, code in enumerate(sample):
        if (ci+1) % 300 == 0: print(f"  进度 {ci+1}/{len(sample)} ({time.time()-t0:.0f}s)")
        d = fetch(code)
        if d.empty: continue
        rows = bt._rows_from_df(d); ok += 1
        ind = ind_of.get(code, "")
        stock_dirs = precompute_dirs(rows)
        ind_dirs = ind_dirs_map.get(ind); ind_date_idx = ind_date_idx_map.get(ind)
        for idx, name, dirv, strength in detect_with_strength(rows):
            if idx + 1 + max(HORIZONS) >= len(rows): continue
            dstr = rows[idx]['date']
            stock_dir = stock_dirs[idx] if idx < len(stock_dirs) else "unknown"
            mkt_i = mkt_date_idx.get(dstr)
            mkt_dir = mkt_dirs[mkt_i] if mkt_i is not None and mkt_i < len(mkt_dirs) else "unknown"
            ind_dir = "unknown"
            if ind_dirs and ind_date_idx:
                ii = ind_date_idx.get(dstr)
                if ii is not None and ii < len(ind_dirs): ind_dir = ind_dirs[ii]
            # 逆势共振：看涨需全down，看跌需全up
            if dirv == "bull":
                contra = (stock_dir == "down" and ind_dir == "down" and mkt_dir == "down")
            else:
                contra = (stock_dir == "up" and ind_dir == "up" and mkt_dir == "up")
            vt = vol_trend_at(rows, idx)
            vol_amp = (vt is not None and vt >= 1.2)
            strong = (strength == 1)
            entry = rows[idx+1]['open']
            for H in HORIZONS:
                f = rows[idx+1+H]['close']
                hit = (f > entry) if dirv == "bull" else (f < entry)
                def rec(combo):
                    agg[(dirv, combo)][H]["total"] += 1
                    if hit: agg[(dirv, combo)][H]["hit"] += 1
                rec("基准")
                if contra: rec("逆势共振")
                if contra and strong: rec("逆势+强形态")
                if contra and vol_amp: rec("逆势+量能放大")
                if contra and strong and vol_amp: rec("逆势+强形态+量能放大")
                if strong: rec("仅强形态")
                if vol_amp: rec("仅量能放大")
                if strong and vol_amp: rec("强形态+量能放大")
    print(f"成功 {ok}/{len(sample)} 只，耗时 {time.time()-t0:.0f}s\n")

    for dirv, dlab in (("bull", "看涨形态"), ("bear", "看跌形态")):
        print(f"◆ {dlab}")
        print(f"{'组合':<24}{'样本':<8}{'3日':<14}{'6日':<14}{'10日':<14}")
        for c in combos:
            cells = []; tot = None
            for H in HORIZONS:
                a = agg[(dirv, c)][H]; t = a["total"]
                if t:
                    tot = t; cells.append(f"{a['hit']/t*100:.1f}%({a['hit']}/{t})")
                else: cells.append("--")
            print(f"{c:<24}{tot if tot else '--':<8}{cells[0]:<14}{cells[1]:<14}{cells[2]:<14}")
        print()
    print("框架：T收盘识别→T+1开盘买入。逆势共振=看涨需大盘/行业/个股全down，看跌需全up。强形态=吞没实体比>1.5/锤子下影比>2.5/乌云或穿刺插入>70%。量能放大=前5日均量/前10日均量>1.2。")


if __name__ == "__main__":
    main()
