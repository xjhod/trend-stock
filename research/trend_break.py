# -*- coding: utf-8 -*-
"""趋势线突破准确率研究 v4
口径: 严格趋势线(up低点支撑/down高点阻力)存在时,
  未来maxf内首次收盘穿越趋势线(up跌破 / down升破) -> 突破后K日按反向运行=命中
层级: 无层级 / 三层共振(大盘+行业+个股同向) 对比
"""
import warnings; warnings.filterwarnings("ignore")
import sys, json, time
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis')
sys.path.insert(0, '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis/research')
from data_fetcher import _kline_from_sina
from trendline_strict import get_index_kline, market_direction, build_industry_index
BASE = '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis'

def strict_trendline_line(rows, i, win=120, min_pts=3, min_span=40):
    """返回 (方向, slope, inter) 或 None; slope/inter 定义趋势线 line(t)=slope*t+inter"""
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
        if r and r[0]>0 and r[2]>=min_pts and r[3]>=min_span:
            return ("up", r[0], r[1])
    if len(highs)>=min_pts:
        r=fit(highs)
        if r and r[0]<0 and r[2]>=min_pts and r[3]>=min_span:
            return ("down", r[0], r[1])
    return None

def run(stocks, all_a, idx_rows, break_k=5, maxf=40):
    idx_closes=[r["close"] for r in idx_rows]; idx_date={r["date"]:i for i,r in enumerate(idx_rows)}
    inds=sorted({s["ind"] for s in stocks}); ind_idx={}
    print("构建行业指数...")
    for k,ind in enumerate(inds):
        ii=build_industry_index(ind, all_a)
        if ii: ind_idx[ind]=ii
        if (k+1)%15==0: print(f"  {k+1}/{len(inds)}")
        time.sleep(0.1)
    print(f"行业指数: {len(ind_idx)}/{len(inds)}")
    S={"all":{"n":0,"hit":0},"up_break":{"n":0,"hit":0},"down_break":{"n":0,"hit":0},
       "triple":{"n":0,"hit":0},"conflict":{"n":0,"hit":0}}
    for s in stocks:
        df=_kline_from_sina(s["code"],"daily",1000)
        if df.empty or len(df)<250: continue
        rows=df.to_dict("records"); closes=[r["close"] for r in rows]; n=len(rows)
        ii=ind_idx.get(s["ind"]); ind_closes=[r["close"] for r in ii] if ii else []
        ind_date={r["date"]:i for i,r in enumerate(ii)} if ii else {}
        for T in range(150, n-maxf-break_k-5, 10):
            tl=strict_trendline_line(rows, T)
            if not tl: continue
            sig, sl, itc = tl
            # 找首个穿越点
            brk=None
            for t in range(T+1, min(T+maxf+1, n-break_k)):
                linev = sl*t + itc
                if sig=="up" and closes[t] < linev: brk=t; break
                if sig=="down" and closes[t] > linev: brk=t; break
            if brk is None: continue
            cb=closes[brk]; fb=closes[min(brk+break_k, n-1)]
            # 命中: up线跌破后跌 / down线升破后涨
            hit = (sig=="up" and fb<cb) or (sig=="down" and fb>cb)
            S["all"]["n"]+=1; S["all"]["hit"]+=(1 if hit else 0)
            if sig=="up": S["up_break"]["n"]+=1; S["up_break"]["hit"]+=(1 if hit else 0)
            else: S["down_break"]["n"]+=1; S["down_break"]["hit"]+=(1 if hit else 0)
            dstr=rows[T]["date"]; mi=idx_date.get(dstr); md=market_direction(idx_closes,mi) if (mi is not None and mi>=60) else None
            wi=ind_date.get(dstr); wd=market_direction(ind_closes,wi) if (wi is not None and wi>=60) else None
            if md is not None and wd is not None:
                if (md=="up" and wd=="up" and sig=="up") or (md=="down" and wd=="down" and sig=="down"):
                    S["triple"]["n"]+=1; S["triple"]["hit"]+=(1 if hit else 0)
                else:
                    S["conflict"]["n"]+=1; S["conflict"]["hit"]+=(1 if hit else 0)
    print(f"\n趋势线突破准确率 · 样本{len(stocks)}只 · 突破后{break_k}日方向 (maxf={maxf})")
    def p(name,d):
        if d["n"]: print(f"  {name:16s}: {d['hit']/d['n']*100:5.1f}% ({d['hit']}/{d['n']})")
    p("突破整体", S["all"]); p("  up线跌破->跌", S["up_break"]); p("  down线升破->涨", S["down_break"])
    p("三层共振时突破", S["triple"]); p("三层冲突时突破", S["conflict"])

if __name__=="__main__":
    all_a=json.load(open(f"{BASE}/bt_data/all_a.json",encoding="utf-8"))
    stocks=json.load(open(f"{BASE}/highfit_pool.json",encoding="utf-8"))
    idx=get_index_kline("sh000001")
    print(f"上证指数: {len(idx)} 根")
    run(stocks, all_a, idx, break_k=5, maxf=40)
