# -*- coding: utf-8 -*-
"""
双策略对比回测 v2：超跌双深(现策略F) vs 强势行业动量(用户思路"比大盘强的行业→行业内选股")
数据：ind_daily 池内等权行业指数 (1996-2026)
公平口径：两者都持有N个交易日；策略A=双深信号触发买入; 策略B=每持有周期调仓一次,
          按相对强度(行业动量-市场动量)取前K行业等权持有。
注意：所有收益均为小数形式(0.11=+11%)。
"""
import sqlite3
import numpy as np
import pandas as pd

conn = sqlite3.connect("bt_data/history.db")
df = pd.read_sql("SELECT ind, date, close FROM ind_daily", conn)
conn.close()

pv = df.pivot(index="date", columns="ind", values="close").sort_index()
pv = pv.ffill()
dates = list(pv.index)

def future_ret(win):
    return pv.shift(-win) / pv - 1

def momentum(w):
    return pv.pct_change(w)

def market_mom(retw):
    return retw.mean(axis=1)

def backtest_a(ret20, ret60, fwd, dates, start="2010-01-01"):
    mask = (ret20 <= -0.08) & (ret60 <= -0.25)
    rows = []
    for d in dates:
        if d < start:
            continue
        sig = mask.loc[d]
        sig = sig[sig].index.tolist()
        if not sig:
            continue
        f = fwd.loc[d, sig].dropna()
        if len(f) == 0:
            continue
        rows.append(f.mean())
    return np.array(rows)

def backtest_b(retw, market, fwd, dates, K, start="2010-01-01", step=20):
    rs = retw.sub(market, axis=0)
    rows = []
    idx = 0
    while idx < len(dates):
        d = dates[idx]
        if d >= start:
            top = rs.loc[d].nlargest(K).index.tolist()
            f = fwd.loc[d, top].dropna()
            if len(f) > 0:
                rows.append(f.mean())
        idx += step
    return np.array(rows)

def stats(name, arr):
    if len(arr) == 0:
        return (name, 0, 0, 0, 0, 0, 0)
    win = (arr > 0).mean() * 100
    cum = float(np.prod(1 + arr) - 1)
    yrs = len(arr) / 12
    ann = (1 + cum) ** (1 / yrs) - 1 if yrs > 0 and cum > -1 else np.nan
    eq = np.cumprod(1 + arr)
    peak = np.maximum.accumulate(eq)
    mdd = float((eq / peak - 1).min()) * 100
    return (name, len(arr), round(arr.mean() * 100, 2), round(np.median(arr) * 100, 2),
            round(win, 1), round(ann * 100, 1) if not np.isnan(ann) else float('nan'), round(mdd, 1))

segments = [("全样本(2010-)", "2010-01-01"),
            ("2026年至今", "2026-01-01"),
            ("2026-07下跌段", "2026-07-01")]

for seg_name, start in segments:
    print(f"\n===== {seg_name} ({start}~2026-10-14) =====")
    a20 = momentum(20); a60 = momentum(60)
    fwd20 = future_ret(20); fwd60 = future_ret(60)
    A = backtest_a(a20, a60, fwd20, dates, start)
    # B 参数网格: 动量窗口 x 持有窗口 x K
    best = None
    cands = []
    for mw, fw, stp, name in [(20, 20, 20, "RS20/持20"), (60, 20, 20, "RS60/持20"), (120, 20, 20, "RS120/持20"),
                               (60, 60, 60, "RS60/持60"), (120, 60, 60, "RS120/持60")]:
        for K in (5, 10):
            retw = momentum(mw)
            fwd = future_ret(fw)
            mkt = market_mom(retw)
            B = backtest_b(retw, mkt, fwd, dates, K, start, stp)
            s = stats(f"{name}K{K}", B)
            cands.append(s)
            if best is None or s[1] > best[1]:
                best = s
    print(f"{'策略':<22}{'样本数':>6}{'平均20日%':>10}{'中位%':>7}{'胜率%':>7}{'年化%':>8}{'最大回撤%':>10}")
    for nm, *st in [stats("A 超跌双深", A)] + cands:
        line = f"{nm:<22}{st[0]:>6}{st[1]:>10.2f}{st[2]:>7.2f}{st[3]:>7.1f}"
        line += f"{st[4]:>8.1f}" if not np.isnan(st[4]) else f"{'nan':>8}"
        line += f"{st[5]:>10.1f}"
        print(line)

# 2026年 B(RS60/K5/持20) vs A 逐期对照
print("\n===== 2026年 逐期对照: A双深信号 vs B强势RS60/K5 =====")
a20 = momentum(20); a60 = momentum(60); fwd20 = future_ret(20)
ret60 = momentum(60); fwd20b = future_ret(20); mkt60 = market_mom(ret60)
rs60 = ret60.sub(mkt60, axis=0)
idx = 0
while idx < len(dates):
    d = dates[idx]
    if "2026-01-01" <= d <= "2026-10-14":
        top = rs60.loc[d].nlargest(5).index.tolist()
        f = fwd20b.loc[d, top].dropna()
        if len(f) > 0:
            print(f"{d} B-前5强势: {[t[:6] for t in top]} -> 20日 {f.mean()*100:+.1f}%")
    idx += 20
