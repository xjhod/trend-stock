# -*- coding: utf-8 -*-
"""市场环境评分模型: 综合 趋势/位置/广度/动量, 测试能否过滤弱市"""
import json, os, sys
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import layers

mkt = json.load(open(os.path.join(BASE,"research","mkt_long.json"), encoding="utf-8"))
bk = json.load(open(os.path.join(BASE,"research","breadth_klines.json"), encoding="utf-8"))

def env_score(asof):
    """返回 (score, 明细dict) 0-6分"""
    rr = [r for r in mkt if r["date"] <= asof]
    closes = [r["close"] for r in rr]
    if len(closes) < 260: return None, None
    c = closes[-1]
    # 1. 趋势
    d = layers._direction(closes)
    s_trend = {"up":2, "side":1, "down":0}[d]
    # 2. 位置(距250日高)
    hi250 = max(closes[-250:])
    dd250 = (c/hi250-1)*100
    s_pos = 1 if -15 <= dd250 <= -1.5 else 0
    # 3. 广度
    up20=total=0; newhi=0
    for code, rows in bk.items():
        rr2 = [r for r in rows if r["date"] <= asof]
        if len(rr2) < 65: continue
        total += 1
        if c_ := rr2[-1]["close"] > rr2[-21]["close"]: up20 += 1
        if rr2[-1]["close"] >= max(r["close"] for r in rr2[-60:]): newhi += 1
    if total == 0: return None, None
    b_up = up20/total*100; b_new = newhi/total*100
    s_breadth = 1 if (b_up > 55 and b_new > 6) else 0
    # 4. 动量(20日涨幅)
    mom20 = (c/closes[-21]-1)*100
    s_mom = 1 if 0 <= mom20 <= 6 else 0
    score = s_trend + s_pos + s_breadth + s_mom
    det = {"trend": d, "s_trend": s_trend, "dd250": round(dd250,1), "s_pos": s_pos,
           "b_up": round(b_up), "b_new": round(b_new,1), "s_br": s_breadth,
           "mom20": round(mom20,1), "s_mom": s_mom, "score": score}
    return score, det

if __name__ == "__main__":
    TPS = [("2023-02-06",-2.2),("2023-05-10",-5.6),("2023-08-10",-8.1),("2023-11-10",-4.1),
           ("2024-05-10",+0.3),("2024-11-08",-6.8)]
    print(f"{'时点':<12}{'收益':>7}  评分 明细")
    for t, ret in TPS:
        s, det = env_score(t)
        if s is not None:
            print(f"{t:<12}{ret:>6.1f}%  {s}分  {det['trend']}/位置{det['dd250']}%/涨家{det['b_up']}%/新高{det['b_new']}%/动量{det['mom20']}%")
