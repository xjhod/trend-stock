# -*- coding: utf-8 -*-
"""超跌反弹机会: 信号强度 vs 胜率 回测
问题: 超跌反弹的股票, 哪个信号强度门槛的"明天买入"胜率更高?
强度维度:
  L0 超跌+收阳(无形态)         —— 最弱
  L1 超跌+看涨形态              —— +形态
  L2 超跌+形态+放量             —— +量能确认
  L3 深度超跌+形态+放量         —— +更深回撤
  L4 超跌+形态+放量+趋势转好     —— +日线转好(MA5>MA20)
  L5 深度超跌+形态+放量+趋势转好  —— 最强
买入: 信号次日开盘; 卖出: 持有H日收盘
"""
import json, os, sys, math
import warnings; warnings.filterwarnings("ignore")
BASE = '/home/user/.super_doubao/super-doubao-runtime/workspace/stock-analysis'
sys.path.insert(0, BASE)

def detect_bullish_at(rows, i):
    """i日是否出现看涨形态"""
    o,h,l,c = [rows[i][k] for k in ('open','high','low','close')]
    body = abs(c-o); rng = max(h-l, 1e-9)
    upper = h - max(o,c); lower = min(o,c) - l
    pats = []
    # 看涨吞没
    if i>=1:
        po,pc = rows[i-1]['open'], rows[i-1]['close']
        pbody = abs(pc-po)
        if pc<po and c>o and body>pbody and c>=max(po,pc) and o<=min(po,pc) and pbody>0:
            pats.append('吞没')
    # 锤子线
    if body>0 and lower>=2*body and upper<=body*0.5:
        pats.append('锤子')
    # 启明星
    if i>=2:
        o2,h2,l2,c2 = rows[i-2]['open'],rows[i-2]['high'],rows[i-2]['low'],rows[i-2]['close']
        o1,c1 = rows[i-1]['open'],rows[i-1]['close']
        if c2<o2 and c>o and abs(c1-o1)<=0.4*abs(c2-o2) and c>(o2+c2)/2:
            pats.append('启明星')
    # 红三兵
    if i>=2:
        ok = all(rows[i-k]['close']>rows[i-k]['open'] for k in range(3))
        if ok and c>rows[i-1]['close']>rows[i-2]['close'] and (h-c)<=body*0.6:
            pats.append('红三兵')
    return pats

def vol_confirm(rows, i, mult=1.3):
    if i<6: return False
    base = sum(r['volume'] for r in rows[i-5:i])/5.0
    return base>0 and rows[i]['volume']>=mult*base

def trend_up(rows, i):
    """日线转好: MA5>MA20 (用 i 之前, 不含当日)"""
    if i<20: return False
    s5 = sum(r['close'] for r in rows[i-5:i])/5.0
    s20 = sum(r['close'] for r in rows[i-20:i])/20.0
    return s5>s20

def main():
    data = json.load(open(os.path.join(BASE,'research','stock_rows_cache.json'),encoding='utf-8'))
    H_list = (5,10,20)
    DD = 0.10; DD_DEEP = 0.20
    # 各强度各持有期统计
    stats = {L:{h:{"n":0,"wins":0,"ret_sum":0,"g":[0,0],"l":[0,0]} for h in H_list}
             for L in ("L0","L1","L2","L3","L4","L5")}
    # 信号事件去重冷却(日)
    CD = 20
    total_events = 0
    for code, rows in data.items():
        n = len(rows)
        if n < 250: continue
        closes = [r['close'] for r in rows]
        last_sig = {}
        for i in range(150, n-25):
            # 超跌: 近60日高点回撤
            hi60 = max(r['high'] for r in rows[i-59:i+1])
            dd = closes[i]/hi60 - 1
            if dd > -DD: continue
            c,o = closes[i], rows[i]['open']
            if c <= o: continue  # 需要收阳
            pats = detect_bullish_at(rows, i)
            vol = vol_confirm(rows, i)
            tu = trend_up(rows, i)
            L = None
            if pats:
                if vol and tu:
                    L = "L5" if dd<=-DD_DEEP else "L4"
                elif vol:
                    L = "L3" if dd<=-DD_DEEP else "L2"
                else:
                    L = "L1"
            else:
                L = "L0"
            # 冷却: 同股票CD日内, 更弱/同级信号不计; 更强信号允许计入
            lv = int(L[1])
            if code in last_sig:
                prev_lv, prev_i = last_sig[code]
                if i - prev_i < CD and lv <= prev_lv:
                    continue
            last_sig[code] = (lv, i)
            total_events += 1
            for h in H_list:
                if i+h >= n: continue
                # 次日开盘买入, 持有h日收盘
                buy = rows[i+1]['open']
                sell = rows[i+h]['close']
                if buy<=0: continue
                ret = sell/buy - 1
                st = stats[L][h]
                st["n"] += 1
                st["ret_sum"] += ret
                if ret>0: st["wins"] += 1
                if ret>0: st["g"][0]+=ret; st["g"][1]+=1
                else: st["l"][0]+=ret; st["l"][1]+=1
    # 输出
    names = {"L0":"超跌+收阳(无形态)","L1":"+看涨形态","L2":"+形态+放量",
             "L3":"深度超跌+形态+放量","L4":"+形态+放量+趋势转好","L5":"深度+形态+放量+趋势转好"}
    print(f"样本股票: {len(data)} 只 | 超跌事件(冷却20日): {total_events}")
    print("="*100)
    print(f"{'强度':<26}{'持有':>4}{'样本':>6}{'胜率%':>8}{'均收益%':>9}{'盈亏比':>7}")
    print("-"*100)
    for L in ("L0","L1","L2","L3","L4","L5"):
        for h in H_list:
            st = stats[L][h]
            if st["n"]==0:
                print(f"{names[L]:<26}{h:>4}{0:>6}{'--':>8}"); continue
            wr = st["wins"]/st["n"]*100
            avg = st["ret_sum"]/st["n"]*100
            g = st["g"][0]/st["g"][1] if st["g"][1] else 0
            l = st["l"][0]/st["l"][1] if st["l"][1] else 0
            pl = g/abs(l) if l else 0
            print(f"{names[L]:<26}{h:>4}{st['n']:>6}{wr:>8.1f}{avg:>9.2f}{pl:>7.2f}")
        print()

if __name__ == "__main__":
    main()
