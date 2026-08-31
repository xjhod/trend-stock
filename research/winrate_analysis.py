# -*- coding: utf-8 -*-
"""分析取消20日后胜率从41%跌到27%的原因"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import importlib.util
spec = importlib.util.spec_from_file_location("fb", "research/full_backtest.py")
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)
spec2 = importlib.util.spec_from_file_location("fb20", "research/full_backtest_20d.py")
fb20 = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(fb20)

r = fb.run_backtest()       # 无20日
r2 = fb20.run_backtest()    # 有20日

# 无20日: 每笔交易回放K线, 算最大浮盈
klines = fb.preload_kline(fb.load_pool())  # 复用缓存
def max_gain(rows, buy_date, exit_date):
    """买入到卖出之间的最高价相对买入价的浮盈%"""
    vals = [x for x in rows if buy_date <= x["date"] <= exit_date]
    if not vals: return 0
    entry = vals[0]["open"] if vals[0]["date"]==buy_date else vals[0]["open"]
    hi = max(x["high"] for x in vals)
    return hi/entry - 1

print("="*60)
print("无20日规则: 亏损单中'曾经浮盈过'的比例")
ever_profit = 0
for t in r["trades"]:
    if t["ret_pct"] <= 0:
        rows = klines.get(t["code"])
        if rows:
            mg = max_gain(rows, t["entry_date"], t["exit_date"])
            if mg > 0.02:  # 曾浮盈>2%
                ever_profit += 1
print(f"  亏损单 {sum(1 for t in r['trades'] if t['ret_pct']<=0)} 笔中, 曾浮盈超2%后亏回的: {ever_profit} 笔")
print(f"  占比 {ever_profit/max(1,sum(1 for t in r['trades'] if t['ret_pct']<=0))*100:.0f}%")

# 平均持仓天数对比
def avg_days(trades, klines):
    ds = []
    for t in trades:
        rows = klines.get(t["code"])
        if not rows: continue
        dates = [x["date"] for x in rows]
        if t["entry_date"] in dates and t["exit_date"] in dates:
            ds.append(dates.index(t["exit_date"]) - dates.index(t["entry_date"]) + 1)
    return sum(ds)/len(ds) if ds else 0
print(f"\n平均持仓天数: 有20日={avg_days(r2['trades'], klines):.0f}天  无20日={avg_days(r['trades'], klines):.0f}天")

# 有20日: 44笔20日到期单中盈利占比
d20 = [t for t in r2["trades"] if t["reason"]=="20日到期"]
d20_w = sum(1 for t in d20 if t["ret_pct"]>0)
print(f"\n有20日规则: '20日到期'离场 {len(d20)} 笔, 其中盈利 {d20_w} 笔 ({d20_w/len(d20)*100:.0f}%)")
print(f"  这44笔贡献了盈利单的主要来源 → 胜率虚高")

# 两种规则盈利/亏损单数对比
for label, rr in [("有20日", r2), ("无20日", r)]:
    tds = rr["trades"]
    w = sum(1 for x in tds if x["ret_pct"]>0)
    print(f"\n{label}: 总{len(tds)}笔 盈利{w} 亏损{len(tds)-w}")
