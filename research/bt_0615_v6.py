# -*- coding: utf-8 -*-
"""最终回测: 2026-06-15 进取模式 10万本金 → 2026-08-14
扫描逻辑=变体5(放宽企稳+近3日放量+底部连阳+行业down放行强信号)
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import data_fetcher as df
import scan_daily
import layers
import analysis as an
import env_judge

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = json.load(open(os.path.join(BASE, "research", "bt_0815_data.json"), encoding="utf-8"))
klines = data["klines"]; mkt_rows = data["market"]; ind_rows = data["industry"]; pool = data["pool"]
print(f"数据加载: {len(klines)}只, 大盘{len(mkt_rows)}天, 行业{len(ind_rows)}个")

ASOF = None
def hist_get_kline(code, period="daily", limit=300, adjust="qfq"):
    rows = klines.get(code, [])
    if ASOF: rows = [r for r in rows if r["date"] <= ASOF]
    if not rows: return pd.DataFrame()
    kdf = pd.DataFrame(rows).tail(limit)
    if period == "weekly":
        kdf = kdf.copy(); kdf["date"] = pd.to_datetime(kdf["date"]); kdf = kdf.set_index("date")
        kdf = kdf.resample("W").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum","amount":"sum","pct_chg":"last","change":"last"}).dropna(subset=["close"]).reset_index()
        kdf["date"] = kdf["date"].dt.strftime("%Y-%m-%d")
    return kdf
def hist_get_market(limit=400):
    rows = [r for r in mkt_rows if r["date"] <= ASOF] if ASOF else mkt_rows
    return rows[-limit:]
def hist_load_ind():
    if ASOF: return {k: [r for r in v if r["date"] <= ASOF] for k, v in ind_rows.items()}
    return ind_rows
df.get_kline = hist_get_kline
layers.get_market_kline = lambda *a, **k: hist_get_market()
layers._load_ind_cache = lambda: hist_load_ind()
scan_daily.get_kline = hist_get_kline
env_judge.get_mode = lambda: "bull"

def _ind_mom20(ind):
    try:
        rows = ind_rows.get(ind, [])
        if ASOF: rows = [r for r in rows if r["date"] <= ASOF]
        ic = [r["close"] for r in rows]
        if len(ic) < 21: return None
        return (ic[-1]/ic[-21]-1)*100
    except: return None

def _weekly_strong(code):
    try:
        wdf = hist_get_kline(code, "weekly", 120, "")
        if wdf is None or len(wdf) < 11: return None
        c = wdf["close"].tolist()
        return bool(sum(c[-5:])/5 > sum(c[-10:])/10 and c[-1] > sum(c[-5:])/5)
    except: return None

def _vol3(df):
    v = df["volume"].tolist()
    if len(v) < 8: return 1.0
    best = 1.0
    for i in range(-3, 0):
        v5 = sum(v[i-6:i-1])/5
        if v5 > 0: best = max(best, v[i]/v5)
    return best

def _scan_v6(it):
    code = it.get("code"); name = it.get("name",""); ind = it.get("ind","")
    try:
        kdf = hist_get_kline(code, "daily", 300, "")
        if kdf is None or len(kdf) < 60: return None
        trend = an.analyze_trend(kdf, "日线")
        direction = trend.get("direction","sideways")
        ly = layers.analyze_layers(code, kdf)
        mkt_dir = ly["market"]["direction"]
        pats = scan_daily.detect_bullish(kdf)
        last = kdf.iloc[-1]; close = float(last["close"])
        closes = kdf["close"].tolist()
        chg = close/float(kdf.iloc[-2]["close"])-1 if len(kdf)>=2 else 0
        chg3 = close/closes[-4]-1 if len(closes)>=4 and closes[-4] else 0
        hi60 = max(kdf["high"].tolist()[-60:])
        dd60 = close/hi60 - 1
        stabilized = scan_daily._stabilized(kdf)
        ma20 = sum(closes[-20:])/20 if len(closes)>=20 else close
        ma10 = sum(closes[-10:])/10 if len(closes)>=10 else close
        ma5 = sum(closes[-5:])/5 if len(closes)>=5 else close
        above_ma20 = close > ma20
        above_ma10 = close > ma10
        above_ma5 = close > ma5
        short_up = ma5 > ma10
        base60 = closes[-61] if len(closes)>=61 else close
        gain60 = (close/base60-1)*100 if base60 else 0
        vr3 = _vol3(kdf)
        vol_hit = vr3 > 1.5
        vol_hit13 = vr3 > 1.3
        best_score, best_grade, best_pat = -99, "weak", ""
        for pn, pi, pvc in pats:
            sc, lv, _ = scan_daily._pattern_quality(kdf, pi, pn, pvc)
            if sc > best_score: best_score, best_grade, best_pat = sc, lv, pn
        if not scan_daily._mkt_gate(): return None
        ind_dir = ly["industry"]["direction"]
        ind_mom = _ind_mom20(ind)
        ind_mom_ok = (ind_mom is not None and ind_mom > 0)
        dist_hi = (1-close/hi60)*100 if hi60 else 0
        over = (dist_hi < 5 and gain60 > 40)
        wk_strong = _weekly_strong(code)
        ind_down = (ind_dir == "down")
        up3 = sum(1 for i in range(-3,0) if closes[i] > closes[i-1])
        lian_yang = (up3 >= 2 and chg3 > 0.02)

        score = 0; channel = None; tags = []
        if dd60 <= -0.25 and pats:
            confirm = wk_strong or above_ma10 or vol_hit13
            if confirm:
                score = 3
                if best_grade == "strong": score += 1
                if ind_mom_ok: score += 1
                if mkt_dir == "up": score += 1
                if ind_down: score -= 1
                channel = "超跌反弹"
                tags = ["超跌企稳", f"形态{best_grade}"]
                if wk_strong: tags.append("周线转强")
                if vol_hit13: tags.append("放量")
                if ind_down: tags.append("行业弱·强信号")
        if channel is None and dd60 <= -0.20:
            if vol_hit and (above_ma5 or chg3 > 0):
                score = 2
                if dd60 <= -0.25: score += 1
                if wk_strong: score += 1
                if ind_mom_ok: score += 1
                if ind_down: score -= 1
                channel = "超跌放量"
                tags = ["超跌放量", f"{vr3:.1f}x"]
                if wk_strong: tags.append("周线转强")
                if ind_mom_ok: tags.append("行业走强")
                if ind_down: tags.append("行业弱·强信号")
        if channel is None and -0.25 < dd60 <= -0.05:
            if lian_yang and above_ma10 and (vol_hit or pats or wk_strong):
                score = 2
                if best_grade == "strong": score += 1
                if vol_hit: score += 1
                if wk_strong: score += 1
                if ind_mom_ok: score += 1
                if ind_down: score -= 1
                channel = "底部连阳"
                tags = ["底部连阳", f"{chg3*100:+.0f}%/3日"]
                if vol_hit: tags.append("放量")
                if wk_strong: tags.append("周线转强")
                if pats: tags.append("+".join(sorted(set(p[0] for p in pats))[:2]))
                if ind_mom_ok: tags.append("行业走强")
                if ind_down: tags.append("行业弱·强信号")
        if channel is None and -0.20 < dd60 <= -0.10:
            if above_ma20 and (short_up or stabilized or wk_strong):
                cond = (best_grade in ("strong","medium")) or vol_hit or wk_strong
                if cond:
                    score = 1
                    if best_grade == "strong": score += 1
                    if vol_hit: score += 1
                    if ind_mom_ok: score += 1
                    if stabilized: score += 1
                    if mkt_dir == "up": score += 1
                    if ind_down: score -= 1
                    channel = "浅超跌启动"
                    tags = ["浅超跌启动", f"形态{best_grade}" if best_grade != "weak" else "转强"]
                    if vol_hit: tags.append("放量")
                    if wk_strong: tags.append("周线转强")
                    if ind_mom_ok: tags.append("行业走强")
        if channel is None and wk_strong and above_ma20 and not over:
            score = 1
            if pats: score += 1
            if direction == "down": score += 1
            elif direction == "side": score += 0.5
            if ind_mom_ok: score += 1
            if mkt_dir == "up" and ind_dir == "up": score += 1
            if gain60 > 60: score -= 1
            if ind_down: score -= 1
            channel = "周线趋势"
            tags = ["周线趋势"]
            if pats: tags.append("+".join(sorted(set(p[0] for p in pats))[:2]))
            if direction == "down": tags.append("日线深回调")
            if ind_mom_ok: tags.append("行业走强")
            if mkt_dir == "up" and ind_dir == "up": tags.append("三层共振")
        if channel is None: return None
        if ind_down:
            strong_signal = (dd60 <= -0.25 and (pats or vol_hit)) or (wk_strong and (pats or above_ma20)) or (lian_yang and (vol_hit or pats))
            if not strong_signal: return None
            if score < 2: return None
        else:
            if score < 2: return None
        level = min(3, int(round(score)))
        tags.append(f"评分{score:.0f}")
        return {"code":code,"name":name,"ind":ind,"type":channel,"level":level,
                "price":round(close,2),"change_pct":round(chg*100,2),"tags":tags,
                "dd60":round(dd60*100,1),"score":round(score,1),"pats":[p[0] for p in pats]}
    except Exception:
        return None

# ===== 1. 扫描 =====
SCAN_DATE = "2026-06-15"; END_DATE = "2026-08-14"
ASOF = SCAN_DATE
print(f"\n===== 模拟 {SCAN_DATE} 扫描(改进版v6, 进取模式) =====")
t0 = time.time()
sigs = []
for it in pool:
    rows = [r for r in klines.get(it["code"], []) if r["date"] <= SCAN_DATE]
    if len(rows) < 60: continue
    r = _scan_v6(it)
    if r: sigs.append(r)
print(f"扫描完成: {len(sigs)}只推荐, 耗时{time.time()-t0:.1f}s")
sigs.sort(key=lambda x: (-x["level"], -x.get("score",0), -x["price"]))
for s in sigs[:15]:
    print(f"  [{s['level']}星] {s['name']}({s['code']}) {s['type']} @{s['price']} {' '.join(s['tags'][:5])}")
if len(sigs) > 15: print(f"  ...共{len(sigs)}只")

# 推荐股后续表现统计
gains_map = {}
for it in pool:
    code = it["code"]; rows = klines.get(code, [])
    start_rows = [r for r in rows if r["date"] >= SCAN_DATE]
    end_rows = [r for r in rows if r["date"] <= END_DATE]
    if not start_rows or not end_rows: continue
    gains_map[code] = end_rows[-1]["close"]/start_rows[0]["close"]-1
rec_gains = [gains_map.get(s["code"], 0) for s in sigs]
all_gains = list(gains_map.values())
print(f"\n推荐股({len(sigs)}只) 平均涨幅: {sum(rec_gains)/len(rec_gains)*100:+.1f}% | 上涨率: {sum(1 for g in rec_gains if g>0)}/{len(rec_gains)} = {sum(1 for g in rec_gains if g>0)/len(rec_gains)*100:.0f}%")
print(f"全池({len(all_gains)}只)  平均涨幅: {sum(all_gains)/len(all_gains)*100:+.1f}% | 上涨率: {sum(1 for g in all_gains if g>0)}/{len(all_gains)} = {sum(1 for g in all_gains if g>0)/len(all_gains)*100:.0f}%")

# ===== 2. 交易模拟 =====
env = env_judge.env_action(asof=SCAN_DATE)
pos_pct = env["pos_pct"]
print(f"\n环境: 评分{env['score']}/6, 进取模式仓位 {pos_pct:.0f}%")

CASH_INIT = 100000.0; MAX_HOLD = 5
buy_list = sigs[:MAX_HOLD]
alloc = CASH_INIT * pos_pct / 100 / len(buy_list) if buy_list else 0
cash = CASH_INIT; holdings = {}; closed = []

print(f"\n===== 买入({SCAN_DATE}收盘) =====")
for s in buy_list:
    price = s["price"]
    qty = int(alloc / price / 100) * 100
    if qty <= 0: continue
    cost = qty * price; cash -= cost
    holdings[s["code"]] = {"name": s["name"], "entry": price, "entry_d": SCAN_DATE,
        "qty": qty, "high": price, "type": s["type"], "tags": s["tags"]}
    print(f"  买入 {s['name']}({s['code']}) @{price:.2f} {qty}股 成本{cost:.0f}元 [{s['type']} {s['level']}星]")
print(f"  现金: {cash:.0f}元, 持仓{len(holdings)}只")

trade_dates = sorted(set(r["date"] for r in mkt_rows if SCAN_DATE < r["date"] <= END_DATE))
print(f"\n===== 逐日跟踪({len(trade_dates)}个交易日) =====")
for d in trade_dates:
    ASOF = d
    mrr = [r for r in mkt_rows if r["date"] <= d]
    mcl = [r["close"] for r in mrr]
    mkt_down = (len(mcl) >= 5 and layers._direction(mcl) == "down")
    mkt_weak = (len(mcl) >= 20 and mcl[-1] < sum(mcl[-20:])/20)
    for code in list(holdings.keys()):
        h = holdings[code]
        rows = [r for r in klines.get(code, []) if r["date"] <= d]
        if not rows: continue
        px = rows[-1]["close"]
        h["price"] = px; h["high"] = max(h["high"], px)
        exit_px, reason = None, ""
        if px <= h["high"] * 0.90:
            exit_px, reason = px, "移动止损-10%"
        elif len(rows) >= 20:
            ma20 = sum(r["close"] for r in rows[-20:])/20
            if px < ma20 and (mkt_down or mkt_weak):
                exit_px, reason = px, "破MA20+大盘弱"
        elif px < h["entry"] * 0.90:
            exit_px, reason = px, "止损-10%"
        if exit_px:
            cash += exit_px * h["qty"]
            ret = exit_px / h["entry"] - 1
            closed.append({**h, "exit_d": d, "exit": exit_px, "ret": ret, "reason": reason})
            print(f"  卖出 {h['name']}({code}) @{exit_px:.2f} 收益{ret*100:+.1f}% [{reason}]")
            del holdings[code]

print(f"\n===== 结算({END_DATE}) =====")
final_cash = cash
for code, h in holdings.items():
    rows = [r for r in klines.get(code, []) if r["date"] <= END_DATE]
    px = rows[-1]["close"] if rows else h["price"]
    final_cash += px * h["qty"]; h["price"] = px

total_ret = final_cash / CASH_INIT - 1
realized = sum(c["ret"] * c["entry"] * c["qty"] for c in closed)
unreal = sum((h["price"]/h["entry"]-1) * h["entry"] * h["qty"] for h in holdings.values())
print(f"  最终资产: {final_cash:.0f}元")
print(f"  总收益: {total_ret*100:+.1f}% ({final_cash-CASH_INIT:+.0f}元)")
print(f"  已实现: {realized:+.0f}元({len(closed)}笔) | 浮动: {unreal:+.0f}元({len(holdings)}只)")
if closed:
    win = sum(1 for c in closed if c["ret"] > 0)
    print(f"\n  已卖出({len(closed)}笔, 胜{win}败{len(closed)-win}, 胜率{win/len(closed)*100:.0f}%):")
    for c in closed:
        print(f"    {c['name']} 买{c['entry_d']}@{c['entry']:.2f} 卖{c['exit_d']}@{c['exit']:.2f} {c['ret']*100:+.1f}% [{c['reason']}]")
if holdings:
    print(f"\n  仍持有({len(holdings)}只):")
    for h in holdings.values():
        print(f"    {h['name']} 买@{h['entry']:.2f} 现价{h['price']:.2f} 浮动{(h['price']/h['entry']-1)*100:+.1f}% {' '.join(h['tags'][:2])}")

mkt_start = next(r["close"] for r in mkt_rows if r["date"] >= SCAN_DATE)
mkt_end = next(r["close"] for r in mkt_rows if r["date"] >= END_DATE)
mkt_ret = mkt_end / mkt_start - 1
print(f"\n  大盘: 上证 {SCAN_DATE}@{mkt_start:.0f} -> {END_DATE}@{mkt_end:.0f} = {mkt_ret*100:+.1f}%")
print(f"  超额收益: {(total_ret-mkt_ret)*100:+.1f}%")

out = {"scan_date":SCAN_DATE,"end_date":END_DATE,"env_score":env["score"],"pos_pct":pos_pct,
       "sigs":sigs,"closed":closed,"holdings":list(holdings.values()),
       "final":final_cash,"total_ret":total_ret,"mkt_ret":mkt_ret,
       "rec_avg":sum(rec_gains)/len(rec_gains) if rec_gains else 0,
       "rec_wr":sum(1 for g in rec_gains if g>0)/len(rec_gains) if rec_gains else 0}
json.dump(out, open(os.path.join(BASE,"research","bt_0615_v6_result.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n结果已保存: research/bt_0615_v6_result.json")
