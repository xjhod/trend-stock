# -*- coding: utf-8 -*-
"""形态可靠性因子验证（严格无前视偏差框架）
决策点 T：当日收盘识别形态（只用 T 及 T 之前的可见信息）
买入点   ：T+1 开盘价买入（次日买入）
评估     ：T+1+H 收盘相对买入价，方向是否与形态一致

验证的"形态前/当日"因子（全部在 T 可得，无未来函数）：
  F1 形态前量能水平  = 前5日均量 / 前60日均量
  F2 量能趋势        = 前5日均量 / 再前5日均量（前10日量能方向）
  F3 支撑位距离      = (形态日最低 - 最近支撑) / 收盘   （看涨形态：越贴近支撑应越可靠）
  F4 阻力位距离      = (最近阻力 - 形态日最高) / 收盘   （看跌形态：越贴近阻力应越可靠）
  F5 超跌程度        = 形态日前20日累计涨跌幅
"""
import json
import random
import sys
import time
import pandas as pd

import backtest as bt
import data_fetcher as df

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


def sr_at(rows, T):
    """用 T 及之前的可见数据算支撑/阻力（复用 backtest.calc_sr 口径）"""
    past = rows[:T + 1]
    if len(past) < 20:
        return None, None
    sup, res = bt.calc_sr(past)
    return (sup[0]["price"] if sup else None), (res[0]["price"] if res else None)


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
    print(f"随机抽取 {n_sample} 只（30亿池） · 严格无前视：T收盘识别→T+1开盘买入")

    # agg[因子][档][H] = {hit,total}
    from collections import defaultdict
    fac = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"hit": 0, "total": 0})))

    ok = 0
    for code in sample:
        d = fetch(code)
        if d.empty:
            continue
        rows = bt._rows_from_df(d)
        ok += 1
        for idx, ns in detect_patterns_raw(rows):
            if idx + 1 + max(HORIZONS) >= len(rows):
                continue
            # 形态方向（同一形态日可能有多个形态，逐个处理）
            c0 = rows[idx]['close']
            entry = rows[idx + 1]['open']  # T+1 开盘买入
            # 因子计算
            f_vol = None
            if idx >= 11:
                v5 = sum(rows[k]['volume'] for k in range(idx - 5, idx)) / 5
                s60 = max(0, idx - 60)
                v60 = sum(rows[k]['volume'] for k in range(s60, idx - 5)) / max(1, (idx - 5) - s60)
                if v60 > 0:
                    f_vol = v5 / v60
            f_voltr = None
            if idx >= 11:
                v5a = sum(rows[k]['volume'] for k in range(idx - 5, idx)) / 5
                v5b = sum(rows[k]['volume'] for k in range(idx - 10, idx - 5)) / 5
                if v5b > 0:
                    f_voltr = v5a / v5b
            f_ov = None
            if idx >= 21:
                f_ov = (c0 / rows[idx - 20]['close'] - 1) * 100
            # 支撑阻力（用当时可见数据）
            sp, rs = sr_at(rows, idx)
            f_spd = None
            if sp and idx >= 1:
                f_spd = (rows[idx]['low'] - sp) / c0 * 100
            f_rsd = None
            if rs and idx >= 1:
                f_rsd = (rs - rows[idx]['high']) / c0 * 100

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
                    rec = {"hit": 0, "total": 0}
                    rec["total"] += 1
                    if hit:
                        rec["hit"] += 1
                    # 记录到各因子档位
                    def put(key, band):
                        fac[key][band][H]["total"] += 1
                        if hit:
                            fac[key][band][H]["hit"] += 1
                    if f_vol is not None:
                        put("F1量能水平", "地量<0.6" if f_vol < 0.6 else ("缩量0.6-0.9" if f_vol < 0.9 else ("平量0.9-1.2" if f_vol < 1.2 else "放量≥1.2")))
                    if f_voltr is not None:
                        put("F2量能趋势", "萎缩<0.8" if f_voltr < 0.8 else ("持平0.8-1.2" if f_voltr <= 1.2 else "放大>1.2"))
                    if f_spd is not None and dirv == "bull":
                        put("F3支撑距离(看涨)", "贴近<1.5" if f_spd < 1.5 else ("中等1.5-3" if f_spd < 3 else "远离≥3"))
                    if f_rsd is not None and dirv == "bear":
                        put("F4阻力距离(看跌)", "贴近<1.5" if f_rsd < 1.5 else ("中等1.5-3" if f_rsd < 3 else "远离≥3"))
                    if f_ov is not None:
                        put("F5超跌程度", "超跌<-8" if f_ov < -8 else ("下跌-8~-3" if f_ov < -3 else ("微跌-3~0" if f_ov < 0 else "未跌≥0")))
        time.sleep(0.1)
    print(f"成功 {ok}/{n_sample} 只\n")

    # 输出
    def show(key, dirv_label=None):
        print(f"\n◆ {key}")
        print(f"{'档位':<16}{'样本':<8}{'3日胜率':<14}{'6日胜率':<14}{'10日胜率':<14}")
        for band in sorted(fac[key].keys()):
            cells = []; tot = None
            for H in HORIZONS:
                a = fac[key][band][H]
                t = a["total"]
                if t:
                    tot = t
                    cells.append(f"{a['hit']/t*100:.1f}%({a['hit']}/{t})")
                else:
                    cells.append("--")
            print(f"{band:<16}{tot if tot else '--':<8}{cells[0]:<14}{cells[1]:<14}{cells[2]:<14}")

    show("F1量能水平")
    show("F2量能趋势")
    show("F3支撑距离(看涨)")
    show("F4阻力距离(看跌)")
    show("F5超跌程度")
    print("\n框架说明：T日收盘识别形态→T+1开盘买入→持有H日评估方向一致性。所有因子在T及之前可得，无未来函数。")


if __name__ == "__main__":
    main()
