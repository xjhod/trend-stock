# -*- coding: utf-8 -*-
"""深挖 v5b 修复: 沿趋势线策略 vs 买入持有, 合理年化, 盈亏比"""
import warnings; warnings.filterwarnings("ignore")
import sys, json, time, os, collections
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis')
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis/research')
from data_fetcher import _kline_from_sina
from trendline_strict import get_index_kline, market_direction
from trend_hold import strict_trendline_line, build_ind_cache, load_stock_rows
BASE = '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis'

def run_strategy2(stocks, all_a, idx_rows):
    idx_closes=[r["close"] for r in idx_rows]; idx_date={r["date"]:i for i,r in enumerate(idx_rows)}
    ind_idx = build_ind_cache(all_a); stock_rows = load_stock_rows(stocks)
    trades=[]; bh_returns=[]
    for code, rows in stock_rows.items():
        closes=[r["close"] for r in rows]; n=len(rows)
        s=next(x for x in stocks if x["code"]==code)
        ii=ind_idx.get(s["ind"]); ind_closes=[r["close"] for r in ii] if ii else []
        ind_date={r["date"]:i for i,r in enumerate(ii)} if ii else {}
        holding=False; entry=0; entry_px=0; trend=None
        for T in range(150, n-1):
            dstr=rows[T]["date"]; mi=idx_date.get(dstr); md=market_direction(idx_closes,mi) if (mi is not None and mi>=60) else None
            wi=ind_date.get(dstr); wd=market_direction(ind_closes,wi) if (wi is not None and wi>=60) else None
            tl=strict_trendline_line(rows, T)
            if not holding:
                if md=="up" and wd=="up" and tl and tl[0]=="up":
                    holding=True; entry=T; entry_px=closes[T]; trend=(tl[1], tl[2])
                    # 对比: 从建仓日买入持有到样本末
                    bh_returns.append(closes[n-1]/entry_px-1)
            else:
                exit_now=False
                if trend:
                    sl,itc=trend
                    if closes[T] < sl*T+itc: exit_now=True
                if md in ("down",None) or wd in ("down",None): exit_now=True
                if exit_now:
                    trades.append({"entry":entry,"exit":T,"days":T-entry,"ret":closes[T]/entry_px-1})
                    holding=False; trend=None
        if holding:
            trades.append({"entry":entry,"exit":n-1,"days":n-1-entry,"ret":closes[n-1]/entry_px-1})
            holding=False
    rets=[t["ret"] for t in trades]; days=[t["days"] for t in trades]
    wins=sum(1 for r in rets if r>0)
    avg_ret=sum(rets)/len(rets)*100
    avg_days=sum(days)/len(days)
    # 盈亏比: 平均盈利/平均亏损
    g=[r for r in rets if r>0]; l=[r for r in rets if r<=0]
    pl = (sum(g)/len(g))/(abs(sum(l)/len(l)) if l else 0) if g and l else 0
    # 合理年化: 单笔复利上界=假设连续满仓(理论上限), 用几何平均
    import math
    geo = math.exp(sum(math.log(1+r) for r in rets)/len(rets))-1  # 每笔几何平均
    ann_per_trade = (1+geo)**(250/avg_days)-1 if avg_days>0 else 0
    # 资金曲线(等权滚动, 假设任意时刻最多持有多只)
    # 按退出日分组, 构建每笔收益序列, 用"平均持仓数"折算
    print("\n===== B. 沿趋势线持有策略 (修复) =====")
    print(f"  交易笔数: {len(trades)}   胜率: {wins/len(trades)*100:.1f}%")
    print(f"  平均单笔收益: {avg_ret:+.2f}%   平均持有: {avg_days:.0f}交易日")
    print(f"  盈亏比(均盈/均亏): {pl:.2f}")
    print(f"  单笔几何平均收益: {geo*100:+.2f}%  -> 若单票连续操作, 年化约 {ann_per_trade*100:+.0f}%")
    print(f"  盈利>10%: {sum(1 for r in rets if r>0.10)}笔  亏损<-10%: {sum(1 for r in rets if r<-0.10)}笔")
    print(f"  最大盈利: {max(rets)*100:.1f}%  最大亏损: {min(rets)*100:.1f}%")
    # 建仓日持有到期末 vs 策略
    bh_avg=sum(bh_returns)/len(bh_returns)*100
    bh_win=sum(1 for r in bh_returns if r>0)/len(bh_returns)*100
    print(f"\n  [对比] 同批建仓日买入持有到样本末: 均收益 {bh_avg:+.2f}% 胜率 {bh_win:.1f}%  (n={len(bh_returns)})")
    print(f"         策略(破线/转弱即走)平均单笔: {avg_ret:+.2f}%  -> 策略在信号后走弱时主动离场")
    # 年份分布
    print("\n  各年份交易分布(大致):", dict(collections.Counter(rows[int(t['entry'])]['date'][:4] for t in trades[:10000] for rows in [stock_rows[stocks[0]['code']]]) ))

if __name__=="__main__":
    all_a=json.load(open(f"{BASE}/bt_data/all_a.json",encoding="utf-8"))
    stocks=json.load(open(f"{BASE}/highfit_pool.json",encoding="utf-8"))
    idx=get_index_kline("sh000001")
    run_strategy2(stocks, all_a, idx)
