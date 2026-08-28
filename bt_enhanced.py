# -*- coding: utf-8 -*-
"""验证加强版形态检测回测：对比基础版 vs 加强组合
E0 基础版（现状）
E1 基础 + 强趋势(带方向 |前20日涨跌|>15%)
E2 基础 + 量能(形态日≥1.3×前5日均量)
E3 基础 + 位置(看跌形态触阻力 / 看涨形态触支撑)
E4 基础 + 强趋势 + 量能 + 位置（全加强）
"""
import warnings; warnings.filterwarnings("ignore")
import json, os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import detect_patterns, calc_sr, _rows_from_df
from data_fetcher import _kline_from_sina

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bt_data")
BEAR = {"上吊线", "看跌吞没", "乌云盖顶", "黄昏星", "三只乌鸦"}
BULL = {"锤子线", "看涨吞没", "穿刺形态", "启明星", "红三兵"}

def chg_n(rows, i, n):
    if i - n < 0: return None
    c0 = rows[i]["close"]; cn = rows[i - n]["close"]
    return (c0 - cn) / cn

def vol_ratio(rows, i, win=5):
    if i - win < 0: return None
    base = sum(rows[k]["volume"] for k in range(i - win, i)) / win
    return rows[i]["volume"] / (base or 1)

def near_sr(rows, i, names):
    bear = any(nm in BEAR for nm in names)
    bull = any(nm in BULL for nm in names)
    if not (bear or bull): return None
    sup, res = calc_sr(rows[:i + 1])
    r = rows[i]
    if bear and res:
        return r["high"] >= res[0]["price"] * 0.98
    if bull and sup:
        return r["low"] <= sup[0]["price"] * 1.02
    return None

def eval_patterns(rows, mode, fwd_pat=5):
    """返回 (n, hit) 统计；mode 决定过滤条件"""
    out = {"n": 0, "hit": 0}
    n = len(rows)
    for T in range(90, n - fwd_pat - 5, 10):
        past = rows[:T + 1]
        c0 = rows[T]["close"]; f5 = rows[T + fwd_pat]["close"]
        for idx, names in detect_patterns(past):
            if idx != T: continue
            bear = any(nm in BEAR for nm in names)
            bull = any(nm in BULL for nm in names)
            if not (bear or bull): continue
            hit = (bear and f5 < c0) or (bull and f5 > c0)
            # 过滤条件
            if mode in ("E1", "E4"):
                c = chg_n(rows, T, 20)
                if c is None: continue
                if bear and not (c > 0.15): continue   # 看跌形态要强上涨后
                if bull and not (c < -0.15): continue  # 看涨形态要强下跌后
            if mode in ("E2", "E4"):
                v = vol_ratio(rows, T)
                if v is None or v < 1.3: continue
            if mode in ("E3", "E4"):
                ok = near_sr(rows, T, names)
                if not ok: continue
            out["n"] += 1
            if hit: out["hit"] += 1
    return out

def run_group(codes, fwd_pat=5):
    agg = {m: {"n": 0, "hit": 0} for m in ["E0", "E1", "E2", "E3", "E4"]}
    for x in codes:
        df = _kline_from_sina(x["code"], "daily", 1000)
        if df.empty or len(df) < 150: continue
        rows = _rows_from_df(df)
        for m in agg:
            r = eval_patterns(rows, m, fwd_pat)
            agg[m]["n"] += r["n"]; agg[m]["hit"] += r["hit"]
    return agg

def show(title, agg):
    print(f"\n===== {title} =====")
    print(f"  {'组合':<26}{'形态数':>7}{'命中率':>9}")
    for m in ["E0", "E1", "E2", "E3", "E4"]:
        a = agg[m]
        if a["n"]:
            print(f"  {m:<26}{a['n']:>7}{a['hit']/a['n']*100:>8.1f}%")
        else:
            print(f"  {m:<26}{0:>7}{'--':>9}")

if __name__ == "__main__":
    groups = json.load(open(os.path.join(DATA, "groups.json"), encoding="utf-8"))
    P1000 = groups["P1000(市值≥1000亿)"]
    VOLH = groups["VOL高(波动最大20只)"]
    lanchou = [
        {"code": "600519"}, {"code": "000001"}, {"code": "300750"},
        {"code": "601318"}, {"code": "000858"}, {"code": "002594"},
    ]
    # 额外从 ≥100亿 抽一个更大样本做普适验证
    pool = json.load(open(os.path.join(DATA, "pool_100e.json"), encoding="utf-8"))
    import random; random.seed(7)
    big = random.sample(pool, 60)
    show("P1000(市值≥1000亿, 50只)", run_group(P1000))
    show("VOL高(波动最大20只)", run_group(VOLH))
    show("蓝筹6只", run_group(lanchou))
    show("≥100亿 随机60只(普适验证)", run_group(big))
