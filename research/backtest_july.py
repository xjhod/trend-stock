# -*- coding: utf-8 -*-
"""7月下跌行情切片回测: 超跌反弹(双深)信号在"7月下跌行情"中的实际表现
用户问题: 这套逻辑在7月份的下跌行情有回测过吗?
2026-07 上证 -6.81% (月内最低-8.5%)。本脚本:
1) 全样本双深信号(20日跌>8% & 60日跌>25%)按触发月份切片
2) 聚焦历年7月且当月大盘下跌的时段
3) 单独列出 2026-07 全部触发行业与信号后实际收益
"""
import sqlite3
import numpy as np, pandas as pd

conn = sqlite3.connect("bt_data/history.db")
ind = pd.read_sql("SELECT ind, date, close FROM ind_daily", conn)
mkt = pd.read_sql("SELECT date, close FROM mkt_daily WHERE sym='sh000001'", conn)
conn.close()
ind["date"] = pd.to_datetime(ind["date"]); mkt["date"] = pd.to_datetime(mkt["date"])

# 大盘五态
mkt = mkt.sort_values("date").reset_index(drop=True)
mkt["ma250"] = mkt["close"].rolling(250).mean()
mkt["ma250_dir"] = mkt["ma250"] > mkt["ma250"].shift(1)
mkt["d_above"] = (mkt["close"] > mkt["ma250"]).astype(int).rolling(60).sum()

def regime(dir_, nd):
    if pd.isna(dir_) or pd.isna(nd):
        return "warmup"
    if nd >= 45 and dir_: return "健康牛"
    if nd >= 45 and not dir_: return "熊市反弹"
    if nd <= 15 and dir_: return "牛深回调"
    if nd <= 15 and not dir_: return "深熊底"
    return "方向不明"

mkt["regime"] = [regime(d, n) for d, n in zip(mkt["ma250_dir"], mkt["d_above"])]

# 月度大盘收益（识别下跌月份）
mkt["ym"] = mkt["date"].dt.to_period("M")
mth = mkt.groupby("ym")["close"].agg(["first", "last"])
mth["ret"] = mth["last"] / mth["first"] - 1
down_julys = [ym for ym, r in mth["ret"].items() if ym.month == 7 and r < 0]
print("历史全部下跌的7月:", [str(x) for x in down_julys], "\n")

# 行业信号
days = np.sort(ind["date"].unique()); sample = days[::5]
ind = ind[ind["date"].isin(sample)].copy()
ind = ind.merge(mkt[["date", "regime"]], on="date", how="left")
g = ind.groupby("ind", sort=False)
ind["mom20"] = g["close"].transform(lambda s: s / s.shift(20) - 1)
ind["mom60"] = g["close"].transform(lambda s: s / s.shift(60) - 1)
ind["fwd20"] = g["close"].transform(lambda s: s.shift(-20) / s - 1)
ind["fwd40"] = g["close"].transform(lambda s: s.shift(-40) / s - 1)
ind = ind[(ind["date"] >= "2002-01-01") & (ind["date"] <= "2026-08-31")].dropna(subset=["mom20", "fwd20"])

sig = ind[(ind["mom20"] < -0.08) & (ind["mom60"] < -0.25)].copy()
sig["ym"] = sig["date"].dt.to_period("M")

print("=" * 100)
print("A) 全样本双深信号 vs 历年7月下跌行情内触发的信号")
print("=" * 100)
all_n = len(sig)
print(f"全样本: 信号 {all_n:,} 个, 后20日 {sig['fwd20'].mean()*100:+.2f}%, 后40日 {sig['fwd40'].mean()*100:+.2f}%")
july = sig[sig["ym"].isin(down_julys)]
print(f"下跌7月内触发: 信号 {len(july):,} 个, 后20日 {july['fwd20'].mean()*100:+.2f}%, 后40日 {july['fwd40'].mean()*100:+.2f}%")
for ym in down_julys:
    sub = sig[sig["ym"] == ym]
    if len(sub):
        print(f"  {ym}: {len(sub)} 个, 后20日 {sub['fwd20'].mean()*100:+.2f}%, 后40日 {sub['fwd40'].mean()*100:+.2f}%  (大盘当月 {mth['ret'][ym]*100:+.1f}%)")

print("\n" + "=" * 100)
print("B) 2026-07 下跌行情内触发的全部双深信号明细")
print("=" * 100)
j26 = sig[(sig["ym"] == pd.Period("2026-07"))]
if len(j26):
    for _, r in j26.iterrows():
        print(f"  {str(r['date'])[:10]}  {r['ind']:10s} 20日{r['mom20']:+.1f}% 60日{r['mom60']:+.1f}%  [{r['regime']}]  后20日 {r['fwd20']*100:+.2f}%  后40日 {r['fwd40']*100:+.2f}%")
else:
    print("  2026-07 采样日无信号（注: 采样每5日一次）")

print("\n" + "=" * 100)
print("C) 2026-07 全月逐交易日检查: 按日连续口径的信号")
print("=" * 100)
# 用原始日频(不采样)重算一次 2026-07
ind2 = pd.read_sql("SELECT ind, date, close FROM ind_daily", conn) if False else ind
conn2 = sqlite3.connect("bt_data/history.db")
indf = pd.read_sql("SELECT ind, date, close FROM ind_daily", conn2)
conn2.close()
indf["date"] = pd.to_datetime(indf["date"])
gf = indf.groupby("ind", sort=False)
indf["mom20f"] = gf["close"].transform(lambda s: s / s.shift(20) - 1)
indf["mom60f"] = gf["close"].transform(lambda s: s / s.shift(60) - 1)
indf["fwd20f"] = gf["close"].transform(lambda s: s.shift(-20) / s - 1)
indf["fwd40f"] = gf["close"].transform(lambda s: s.shift(-40) / s - 1)
j26f = indf[(indf["date"] >= "2026-07-01") & (indf["date"] <= "2026-07-31") &
            (indf["mom20f"] < -0.08) & (indf["mom60f"] < -0.25)]
print(f"2026-07 日频口径信号: {len(j26f)} 条, 涉及行业 {j26f['ind'].nunique()} 个")
r20 = j26f["fwd20f"].mean() * 100
r40 = j26f["fwd40f"].mean() * 100
print(f"信号后20日平均 {r20:+.2f}%, 40日 {r40:+.2f}%")
# 明细按行业列出（去重取首次触发）
first = j26f.sort_values("date").groupby("ind").first().reset_index()
for _, r in first.iterrows():
    print(f"  {str(r['date'])[:10]}  {r['ind']:12s} 20日{r['mom20f']:+.1f}% 60日{r['mom60f']:+.1f}%  后20日 {r['fwd20f']*100:+.2f}%  后40日 {r['fwd40f']*100:+.2f}%")
