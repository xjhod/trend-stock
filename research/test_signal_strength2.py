# -*- coding: utf-8 -*-
"""信号强度扩展回测: 深度阈值 / 形态必要性 / 放量影响 / 大盘环境
买入: 次日开盘; 卖出: 持有H日收盘
"""
import json, os, sys, time
import warnings; warnings.filterwarnings("ignore")
BASE = '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis'
sys.path.insert(0, BASE)
from data_fetcher import _kline_from_sina

def detect_bullish_at(rows, i):
    o,h,l,c = [rows[i][k] for k in ('open','high','low','close')]
    body = abs(c-o); rng = max(h-l, 1e-9)
    upper = h - max(o,c); lower = min(o,c) - l
    pats = []
    if i>=1:
        po,pc = rows[i-1]['open'], rows[i-1]['close']
        pbody = abs(pc-po)
        if pc<po and c>o and body>pbody and c>=max(po,pc) and o<=min(po,pc) and pbody>0: pats.append('吞没')
    if body>0 and lower>=2*body and upper<=body*0.5: pats.append('锤子')
    if i>=2:
        o2,c2 = rows[i-2]['open'],rows[i-2]['close']
        o1,c1 = rows[i-1]['open'],rows[i-1]['close']
        if c2<o2 and c>o and abs(c1-o1)<=0.4*abs(c2-o2) and c>(o2+c2)/2: pats.append('启明星')
    if i>=2:
        if all(rows[i-k]['close']>rows[i-k]['open'] for k in range(3)) and c>rows[i-1]['close']>rows[i-2]['close'] and (h-c)<=body*0.6: pats.append('红三兵')
    return pats

def vol_confirm(rows, i, mult=1.3):
    if i<6: return False
    base = sum(r['volume'] for r in rows[i-5:i])/5.0
    return base>0 and rows[i]['volume']>=mult*base

def mkt_direction_at(mkt_rows, dstr, short=20, long=60):
    """大盘方向: 该日及之前 short/long 均线比较"""
    dates = [r['date'] for r in mkt_rows]
    if dstr not in dates: return None
    idx = dates.index(dstr)
    if idx < long: return None
    closes = [r['close'] for r in mkt_rows[:idx+1]]
    s = sum(closes[-short:])/short
    l = sum(closes[-long:])/long
    return "up" if s>l else ("down" if s<l else "flat")

def main():
    data = json.load(open(os.path.join(BASE,'research','stock_rows_cache.json'),encoding='utf-8'))
    # 大盘指数
    mkt = _kline_from_sina("sh000001","daily",1000)
    mkt_rows = mkt.to_dict("records") if not mkt.empty else []
    print(f"大盘K线: {len(mkt_rows)} 根")

    H = (10,20)
    # 组合定义: (名称, 深度阈值, 需要形态, 需要放量, 大盘过滤)
    combos = [
        ("超跌10%+收阳",       0.10, False, False, None),
        ("超跌15%+收阳",       0.15, False, False, None),
        ("超跌20%+收阳",       0.20, False, False, None),
        ("超跌25%+收阳",       0.25, False, False, None),
        ("超跌20%+形态",       0.20, True,  False, None),
        ("超跌25%+形态",       0.25, True,  False, None),
        ("超跌20%+形态+放量",  0.20, True,  True,  None),
        ("超跌25%+形态+放量",  0.25, True,  True,  None),
        ("超跌20%+形态+大盘up",0.20, True,  False, "up"),
        ("超跌20%+形态+大盘down",0.20,True, False, "down"),
    ]
    stats = {c[0]:{h:{"n":0,"wins":0,"ret_sum":0} for h in H} for c in combos}
    CD = 20
    for code, rows in data.items():
        n = len(rows)
        if n < 250: continue
        closes = [r['close'] for r in rows]
        dates = [r['date'] for r in rows]
        last = {}
        for i in range(150, n-25):
            hi60 = max(r['high'] for r in rows[i-59:i+1])
            dd = closes[i]/hi60 - 1
            c,o = closes[i], rows[i]['open']
            if c <= o: continue
            pats = detect_bullish_at(rows, i)
            vol = vol_confirm(rows, i)
            md = mkt_direction_at(mkt_rows, dates[i]) if mkt_rows else None
            for name, dd_th, need_pat, need_vol, mkt_f in combos:
                if dd > -dd_th: continue
                if need_pat and not pats: continue
                if need_vol and not vol: continue
                if mkt_f and md != mkt_f: continue
                # 冷却按组合分别计(简化: 全局key=code+name)
                key = (code, name)
                if key in last and i - last[key] < CD: continue
                last[key] = i
                for h in H:
                    if i+h >= n: continue
                    buy = rows[i+1]['open']
                    sell = rows[i+h]['close']
                    if buy<=0: continue
                    ret = sell/buy - 1
                    st = stats[name][h]
                    st["n"] += 1; st["ret_sum"] += ret
                    if ret>0: st["wins"] += 1
    print("="*92)
    print(f"{'组合':<24}{'持有':>4}{'样本':>6}{'胜率%':>8}{'均收益%':>9}")
    print("-"*92)
    for name, *_ in combos:
        for h in H:
            st = stats[name][h]
            if st["n"]==0:
                print(f"{name:<24}{h:>4}{0:>6}{'--':>8}"); continue
            print(f"{name:<24}{h:>4}{st['n']:>6}{st['wins']/st['n']*100:>8.1f}{st['ret_sum']/st['n']*100:>9.2f}")
        print()

if __name__ == "__main__":
    main()
