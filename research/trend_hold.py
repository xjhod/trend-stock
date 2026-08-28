# -*- coding: utf-8 -*-
"""深挖 v5: 三层共振信号的持有体验 + 沿趋势线持有策略回测
A. 信号持有体验: 三层共振看涨信号出现后持有K日 的收益/胜率/最大回撤, 对比基准
B. 沿趋势线策略: 共振建仓(大盘↑行业↑个股up线) -> 跌破up线或大盘/行业转弱平仓
"""
import warnings; warnings.filterwarnings("ignore")
import sys, json, time, os
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis')
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis/research')
from data_fetcher import _kline_from_sina
from trendline_strict import get_index_kline, market_direction
BASE = '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis'
CACHE = os.path.join(BASE, 'research', 'ind_idx_cache.json')

def strict_trendline_line(rows, i, win=120, min_pts=3, min_span=40):
    start = max(0, i - win)
    lows, highs = [], []
    for j in range(start, i):
        if j-1>=0 and j+1<i and rows[j]["low"]<=rows[j-1]["low"] and rows[j]["low"]<=rows[j+1]["low"]:
            lows.append((j, rows[j]["low"]))
        if j-1>=0 and j+1<i and rows[j]["high"]>=rows[j-1]["high"] and rows[j]["high"]>=rows[j+1]["high"]:
            highs.append((j, rows[j]["high"]))
    def fit(pts):
        n=len(pts)
        if n<2: return None
        xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
        mx=sum(xs)/n; my=sum(ys)/n
        sxy=sum((xs[k]-mx)*(ys[k]-my) for k in range(n)); sxx=sum((xs[k]-mx)**2 for k in range(n))
        if sxx==0: return None
        sl=sxy/sxx; itc=my-sl*mx
        inl=sum(1 for idx,pr in pts if abs(sl*idx+itc-pr)<=pr*0.015)
        return (sl, itc, inl, max(xs)-min(xs))
    if len(lows)>=min_pts:
        r=fit(lows)
        if r and r[0]>0 and r[2]>=min_pts and r[3]>=min_span: return ("up", r[0], r[1])
    if len(highs)>=min_pts:
        r=fit(highs)
        if r and r[0]<0 and r[2]>=min_pts and r[3]>=min_span: return ("down", r[0], r[1])
    return None

def build_ind_cache(all_a):
    if os.path.exists(CACHE):
        return json.load(open(CACHE, encoding='utf-8'))
    from trendline_strict import build_industry_index
    inds = sorted({x['ind'] for x in all_a if x['ind']})
    cache = {}
    print(f"构建行业指数缓存 {len(inds)} 个...")
    for k, ind in enumerate(inds):
        ii = build_industry_index(ind, all_a)
        if ii: cache[ind] = ii
        if (k+1)%15==0: print(f"  {k+1}/{len(inds)}")
        time.sleep(0.1)
    json.dump(cache, open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f"缓存完成: {len(cache)} 个 -> {CACHE}")
    return cache

def load_stock_rows(stocks):
    cache_file = os.path.join(BASE, 'research', 'stock_rows_cache.json')
    if os.path.exists(cache_file):
        data = json.load(open(cache_file, encoding='utf-8'))
        print(f"复用个股K线缓存 {len(data)} 只")
        return data
    rows_map = {}
    for s in stocks:
        df = _kline_from_sina(s["code"], "daily", 1000)
        if not df.empty and len(df) >= 250:
            rows_map[s["code"]] = df.to_dict("records")
    json.dump(rows_map, open(cache_file, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f"个股K线缓存 {len(rows_map)} 只")
    return rows_map

def run_hold(stocks, all_a, idx_rows, K=(10,20,40)):
    idx_closes=[r["close"] for r in idx_rows]; idx_date={r["date"]:i for i,r in enumerate(idx_rows)}
    ind_idx = build_ind_cache(all_a)
    stock_rows = load_stock_rows(stocks)
    # 基准: 全部交易日的随机持有收益(样本池平均)
    bench = {k: {"n":0,"ret_sum":0,"win":0,"maxdd_sum":0} for k in K}
    sigs = {k: {"n":0,"ret_sum":0,"win":0,"maxdd_sum":0} for k in K}
    sig_uponly = {k: {"n":0,"ret_sum":0,"win":0,"maxdd_sum":0} for k in K}
    for code, rows in stock_rows.items():
        closes=[r["close"] for r in rows]; n=len(rows)
        s = next(x for x in stocks if x["code"]==code)
        ii=ind_idx.get(s["ind"]); ind_closes=[r["close"] for r in ii] if ii else []
        ind_date={r["date"]:i for i,r in enumerate(ii)} if ii else {}
        for T in range(150, n-50, 10):
            c0=closes[T]
            for k in K:
                if T+k>=n: continue
                f=closes[T+k]
                dd = min(closes[T:T+k+1])/c0 - 1
                bench[k]["n"]+=1; bench[k]["ret_sum"]+=(f/c0-1); bench[k]["win"]+=(1 if f>c0 else 0); bench[k]["maxdd_sum"]+=dd
            # 三层共振看涨
            dstr=rows[T]["date"]; mi=idx_date.get(dstr); md=market_direction(idx_closes,mi) if (mi is not None and mi>=60) else None
            wi=ind_date.get(dstr); wd=market_direction(ind_closes,wi) if (wi is not None and wi>=60) else None
            tl=strict_trendline_line(rows, T)
            sig_up = (tl and tl[0]=="up")
            if md=="up" and wd=="up" and sig_up:
                for k in K:
                    if T+k>=n: continue
                    f=closes[T+k]; dd=min(closes[T:T+k+1])/c0-1
                    sigs[k]["n"]+=1; sigs[k]["ret_sum"]+=(f/c0-1); sigs[k]["win"]+=(1 if f>c0 else 0); sigs[k]["maxdd_sum"]+=dd
            elif sig_up:  # 仅个股up线(无层级)
                for k in K:
                    if T+k>=n: continue
                    f=closes[T+k]; dd=min(closes[T:T+k+1])/c0-1
                    sig_uponly[k]["n"]+=1; sig_uponly[k]["ret_sum"]+=(f/c0-1); sig_uponly[k]["win"]+=(1 if f>c0 else 0); sig_uponly[k]["maxdd_sum"]+=dd
    print("\n===== A. 持有体验 (买入持有K日) =====")
    for k in K:
        def line(name, d):
            if d["n"]:
                avg=d["ret_sum"]/d["n"]*100; win=d["win"]/d["n"]*100; dd=d["maxdd_sum"]/d["n"]*100
                print(f"    {name:12s} 持有{k:>2}日: 均收益 {avg:+.2f}%  胜率 {win:.1f}%  均最大回撤 {dd:.2f}%  (n={d['n']})")
        print(f"  -- 窗口 {k}日 --")
        line("基准(全部)", bench[k]); line("仅个股up线", sig_uponly[k]); line("三层共振看涨", sigs[k])

def run_strategy(stocks, all_a, idx_rows):
    """B. 沿趋势线持有策略: 共振建仓, 跌破up线或大盘/行业转弱平仓"""
    idx_closes=[r["close"] for r in idx_rows]; idx_date={r["date"]:i for i,r in enumerate(idx_rows)}
    ind_idx = build_ind_cache(all_a)
    stock_rows = load_stock_rows(stocks)
    trades = []
    for code, rows in stock_rows.items():
        closes=[r["close"] for r in rows]; n=len(rows)
        s = next(x for x in stocks if x["code"]==code)
        ii=ind_idx.get(s["ind"]); ind_closes=[r["close"] for r in ii] if ii else []
        ind_date={r["date"]:i for i,r in enumerate(ii)} if ii else {}
        holding=False; entry=0; entry_px=0; trend=None
        for T in range(150, n-1):
            dstr=rows[T]["date"]; mi=idx_date.get(dstr); md=market_direction(idx_closes,mi) if (mi is not None and mi>=60) else None
            wi=ind_date.get(dstr); wd=market_direction(ind_closes,wi) if (wi is not None and wi>=60) else None
            tl=strict_trendline_line(rows, T)
            if not holding:
                if md=="up" and wd=="up" and tl and tl[0]=="up":
                    holding=True; entry=T; entry_px=closes[T]; trend=(tl[1], tl[2])  # slope, inter
            else:
                # 平仓: 跌破up线 或 大盘/行业转弱
                exit_now = False
                if trend:
                    sl, itc = trend
                    linev = sl*T + itc
                    if closes[T] < linev: exit_now=True
                if md in ("down", None) or wd in ("down", None):
                    exit_now = True
                if exit_now:
                    ret = closes[T]/entry_px - 1
                    trades.append({"code":code,"entry":entry,"exit":T,"days":T-entry,"ret":ret})
                    holding=False; entry=0; trend=None
        if holding:  # 期末平仓
            ret = closes[n-1]/entry_px - 1
            trades.append({"code":code,"entry":entry,"exit":n-1,"days":n-1-entry,"ret":ret})
    if not trades:
        print("无交易"); return
    rets=[t["ret"] for t in trades]; days=[t["days"] for t in trades]
    wins=sum(1 for r in rets if r>0)
    avg_ret=sum(rets)/len(rets)*100
    avg_days=sum(days)/len(days)
    # 每笔按持有天数年化
    ann=[( (1+r)**(250/d) - 1 )*100 for r,d in zip(rets,days) if d>0]
    avg_ann=sum(ann)/len(ann)
    # 组合曲线(按时间顺序模拟等权)
    import collections
    by_day = collections.defaultdict(list)
    for t in trades:
        by_day[t["exit"]].append(t["ret"])
    # 粗略最大回撤(单笔)
    print("\n===== B. 沿趋势线持有策略 =====")
    print(f"  交易笔数: {len(trades)}")
    print(f"  胜率: {wins/len(trades)*100:.1f}%")
    print(f"  平均单笔收益: {avg_ret:+.2f}%  (年化 {avg_ann:+.1f}%)")
    print(f"  平均持有: {avg_days:.0f} 个交易日")
    worst=sorted(rets)[:3]
    best=sorted(rets)[-3:]
    print(f"  最大单笔亏损: {worst[0]*100:.1f}%  最大单笔盈利: {best[-1]*100:.1f}%")
    # 亏损/盈利分布
    big_win=sum(1 for r in rets if r>0.10); big_loss=sum(1 for r in rets if r<-0.10)
    print(f"  盈利>10%: {big_win}笔  亏损<-10%: {big_loss}笔")

if __name__=="__main__":
    all_a=json.load(open(f"{BASE}/bt_data/all_a.json",encoding="utf-8"))
    stocks=json.load(open(f"{BASE}/highfit_pool.json",encoding="utf-8"))
    idx=get_index_kline("sh000001")
    print(f"上证指数: {len(idx)} 根, 最新 {idx[-1]['date'] if idx else 'N/A'}")
    run_hold(stocks, all_a, idx, K=(10,20,40))
    run_strategy(stocks, all_a, idx)
