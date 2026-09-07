# -*- coding: utf-8 -*-
"""变体测试: 行业门卫分级 — 行业down时放行强信号(深超跌+强形态 或 周线转强+形态)
对比原改进版(行业down一票否决)"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import data_fetcher as df
import scan_daily
import layers
import analysis as an

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = json.load(open(os.path.join(BASE, "research", "bt_0815_data.json"), encoding="utf-8"))
klines = data["klines"]; mkt_rows = data["market"]; ind_rows = data["industry"]; pool = data["pool"]

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

def _vol_confirm(df):
    try:
        v = df["volume"].tolist()
        if len(v) < 6: return False
        v5 = sum(v[-6:-1])/5
        return v5 > 0 and v[-1] > v5*1.5
    except: return False

def _scan_v3(it, ind_down_policy="strong"):
    """变体: 行业门卫分级
    ind_down_policy:
      "veto"   = 行业down一票否决(原改进版)
      "strong" = 行业down时放行强信号(深超跌≤-25%+强形态 或 周线转强+形态strong)
    """
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
        chg = close/float(kdf.iloc[-2]["close"])-1 if len(kdf)>=2 else 0
        hi60 = max(kdf["high"].tolist()[-60:])
        dd60 = close/hi60 - 1
        stabilized = scan_daily._stabilized(kdf)
        closes = kdf["close"].tolist()
        ma20 = sum(closes[-20:])/20 if len(closes)>=20 else close
        ma10 = sum(closes[-10:])/10 if len(closes)>=10 else close
        ma5 = sum(closes[-5:])/5 if len(closes)>=5 else close
        above_ma20 = close > ma20
        short_up = ma5 > ma10
        base60 = closes[-61] if len(closes)>=61 else close
        gain60 = (close/base60-1)*100 if base60 else 0
        vol_hit = _vol_confirm(kdf)
        best_score, best_grade, best_pat = -99, "weak", ""
        for pn, pi, pvc in pats:
            sc, lv, _ = scan_daily._pattern_quality(kdf, pi, pn, pvc)
            if sc > best_score: best_score, best_grade, best_pat = sc, lv, pn
        # 大盘门卫
        if not scan_daily._mkt_gate(): return None
        ind_dir = ly["industry"]["direction"]
        ind_mom = _ind_mom20(ind)
        ind_mom_ok = (ind_mom is not None and ind_mom > 0)
        dist_hi = (1-close/hi60)*100 if hi60 else 0
        over = (dist_hi < 5 and gain60 > 40)
        wk_strong = _weekly_strong(code)

        # 行业门卫(分级)
        ind_down = (ind_dir == "down")
        if ind_down and ind_down_policy == "veto":
            return None
        # 强信号定义(用于行业down时放行)
        strong_signal = (dd60 <= -0.25 and best_grade == "strong") or (wk_strong and best_grade == "strong")

        score = 0; channel = None; tags = []

        # 通道A 深/中超跌
        if dd60 <= -0.20:
            if pats and stabilized:
                score = 2
                if dd60 <= -0.25: score += 1
                if best_grade == "strong": score += 1
                if ind_mom_ok: score += 1
                if mkt_dir == "up": score += 1
                if ind_down: score -= 1  # 行业逆风降权
                channel = "超跌反弹"
                tags = ["超跌企稳", f"形态{best_grade}"]
                if dd60 <= -0.25: tags.append("深超跌")
                if ind_mom_ok: tags.append("行业走强")
                if ind_down: tags.append("行业弱·强信号")

        # 通道B 浅超跌启动
        if channel is None and -0.20 < dd60 <= -0.10:
            if above_ma20 and (short_up or stabilized):
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

        # 通道C 周线转强
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
        # 行业down时的强信号放行: score需>=2 且 强信号
        if ind_down:
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

# 测试两个策略
SCAN_DATE = "2026-06-15"
END_DATE = "2026-08-14"
ASOF = SCAN_DATE

gains = []
for it in pool:
    code = it["code"]
    rows = klines.get(code, [])
    start_rows = [r for r in rows if r["date"] >= SCAN_DATE]
    end_rows = [r for r in rows if r["date"] <= END_DATE]
    if not start_rows or not end_rows: continue
    gains.append({"code":code, "name":it.get("name",""), "ind":it.get("ind",""),
                  "start":start_rows[0]["close"], "end":end_rows[-1]["close"],
                  "gain":end_rows[-1]["close"]/start_rows[0]["close"]-1})
gains.sort(key=lambda x: -x["gain"])

for policy in ["veto", "strong"]:
    ASOF = SCAN_DATE
    sigs = []
    for it in pool:
        rows = [r for r in klines.get(it["code"], []) if r["date"] <= SCAN_DATE]
        if len(rows) < 60: continue
        r = _scan_v3(it, ind_down_policy=policy)
        if r: sigs.append(r)
    rec_codes = set(s["code"] for s in sigs)
    rec_gains = [g for g in gains if g["code"] in rec_codes]
    hit20 = sum(1 for g in gains[:20] if g["code"] in rec_codes)
    hit50 = sum(1 for g in gains[:50] if g["code"] in rec_codes)
    avg = sum(g["gain"] for g in rec_gains)/len(rec_gains)*100 if rec_gains else 0
    wr = sum(1 for g in rec_gains if g["gain"]>0)/len(rec_gains)*100 if rec_gains else 0
    print(f"\n【{policy}】推荐{len(rec_codes)}只 | 平均涨幅{avg:+.1f}% | 上涨率{wr:.0f}% | 前20命中{hit20}/20 | 前50命中{hit50}/50")
    # 命中详情
    hits = [g for g in gains[:20] if g["code"] in rec_codes]
    if hits:
        print(f"  前20命中: " + ", ".join(f"{g['name']}({g['gain']*100:+.0f}%)" for g in hits))
    else:
        print("  前20命中: 无")
    # 推荐前10
    sigs.sort(key=lambda x: (-x["level"], -x.get("score",0)))
    print(f"  推荐前10: " + ", ".join(f"{s['name']}({s['type']})" for s in sigs[:10]))
PYEOF