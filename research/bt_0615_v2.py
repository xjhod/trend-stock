# -*- coding: utf-8 -*-
"""改进版扫描+回测: 2026-06-15 进取模式 10万本金 → 2026-08-14结算
按上轮5个改进方向实现v2扫描:
  1. 行业轮动优先(行业动量加分)
  2. 超跌分级: 深(≤-25%)/中(-20~-25%)/浅(-10~-20%)
  3. 周线门槛降低: 周MA5>MA10(转强) 替代 严格多头MA5>10>20
  4. 过热过滤放宽: 距高<5%且60日涨>40% 才判过热(原<8%误杀启动股)
  5. 浅超跌/启动需形态或放量确认
"""
import sys, os, json, time, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import data_fetcher as df
import scan_daily
import layers
import analysis as an
import env_judge

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = json.load(open(os.path.join(BASE, "research", "bt_0815_data.json"), encoding="utf-8"))
klines = data["klines"]
mkt_rows = data["market"]
ind_rows = data["industry"]
pool = data["pool"]
print(f"数据加载: {len(klines)}只, 大盘{len(mkt_rows)}天, 行业{len(ind_rows)}个")

ASOF = None

def hist_get_kline(code, period="daily", limit=300, adjust="qfq"):
    rows = klines.get(code, [])
    if ASOF:
        rows = [r for r in rows if r["date"] <= ASOF]
    if not rows:
        return pd.DataFrame()
    kdf = pd.DataFrame(rows).tail(limit)
    if period == "weekly":
        kdf = kdf.copy()
        kdf["date"] = pd.to_datetime(kdf["date"])
        kdf = kdf.set_index("date")
        kdf = kdf.resample("W").agg({
            "open":"first","high":"max","low":"min","close":"last",
            "volume":"sum","amount":"sum","pct_chg":"last","change":"last"
        }).dropna(subset=["close"]).reset_index()
        kdf["date"] = kdf["date"].dt.strftime("%Y-%m-%d")
    elif period == "monthly":
        kdf = kdf.copy()
        kdf["date"] = pd.to_datetime(kdf["date"])
        kdf = kdf.set_index("date")
        kdf = kdf.resample("ME").agg({
            "open":"first","high":"max","low":"min","close":"last",
            "volume":"sum","amount":"sum","pct_chg":"last","change":"last"
        }).dropna(subset=["close"]).reset_index()
        kdf["date"] = kdf["date"].dt.strftime("%Y-%m-%d")
    return kdf

def hist_get_market(limit=400):
    rows = [r for r in mkt_rows if r["date"] <= ASOF] if ASOF else mkt_rows
    return rows[-limit:]

def hist_load_ind():
    if ASOF:
        return {k: [r for r in v if r["date"] <= ASOF] for k, v in ind_rows.items()}
    return ind_rows

df.get_kline = hist_get_kline
layers.get_market_kline = lambda *a, **k: hist_get_market()
layers._load_ind_cache = lambda: hist_load_ind()
scan_daily.get_kline = hist_get_kline
env_judge.get_mode = lambda: "bull"  # 进取模式

def _ind_mom20(ind):
    """行业近20日动量%"""
    try:
        rows = ind_rows.get(ind, [])
        if ASOF:
            rows = [r for r in rows if r["date"] <= ASOF]
        ic = [r["close"] for r in rows]
        if len(ic) < 21:
            return None
        return (ic[-1] / ic[-21] - 1) * 100
    except Exception:
        return None

def _weekly_strong(code):
    """周线转强(门槛降低): 周MA5>周MA10 且 最新周收盘>周MA5
    替代原版严格多头(周MA5>10>20), 启动初期即可捕捉"""
    try:
        wdf = hist_get_kline(code, "weekly", 120, "")
        if wdf is None or len(wdf) < 11:
            return None
        c = wdf["close"].tolist()
        ma5 = sum(c[-5:]) / 5
        ma10 = sum(c[-10:]) / 10
        return bool(ma5 > ma10 and c[-1] > ma5)
    except Exception:
        return None

def _vol_confirm(df):
    """放量确认: 今日量 > 1.5x 前5日均量"""
    try:
        v = df["volume"].tolist()
        if len(v) < 6:
            return False
        v5 = sum(v[-6:-1]) / 5
        return v5 > 0 and v[-1] > v5 * 1.5
    except Exception:
        return False

def _scan_one_v2(it):
    """改进版扫描"""
    code = it.get("code"); name = it.get("name", ""); ind = it.get("ind", "")
    try:
        kdf = hist_get_kline(code, "daily", 300, "")
        if kdf is None or len(kdf) < 60:
            return None
        trend = an.analyze_trend(kdf, "日线")
        direction = trend.get("direction", "sideways")
        ly = layers.analyze_layers(code, kdf)
        mkt_dir = ly["market"]["direction"]
        pats = scan_daily.detect_bullish(kdf)
        last = kdf.iloc[-1]
        close = float(last["close"])
        chg = close / float(kdf.iloc[-2]["close"]) - 1 if len(kdf) >= 2 else 0
        hi60 = max(kdf["high"].tolist()[-60:])
        dd60 = close / hi60 - 1
        stabilized = scan_daily._stabilized(kdf)
        # 均线
        closes = kdf["close"].tolist()
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else close
        ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else close
        ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else close
        above_ma20 = close > ma20
        short_up = ma5 > ma10
        # 60日涨幅(用于过热放宽)
        base60 = closes[-61] if len(closes) >= 61 else close
        gain60 = (close / base60 - 1) * 100 if base60 else 0
        # 放量
        vol_hit = _vol_confirm(kdf)
        # 形态质量
        best_score, best_grade, best_pat = -99, "weak", ""
        for pn, pi, pvc in pats:
            sc, lv, _ = scan_daily._pattern_quality(kdf, pi, pn, pvc)
            if sc > best_score:
                best_score, best_grade, best_pat = sc, lv, pn
        # ===== 大盘门卫 =====
        if not scan_daily._mkt_gate():
            return None
        # ===== 行业门卫: 不在下跌 =====
        if ly["industry"]["direction"] == "down":
            return None
        ind_mom = _ind_mom20(ind)
        ind_mom_ok = (ind_mom is not None and ind_mom > 0)
        # ===== 过热(放宽): 距高<5% 且 60日涨幅>40% =====
        dist_hi = (1 - close / hi60) * 100 if hi60 else 0
        over = (dist_hi < 5 and gain60 > 40)
        # 周线转强(门槛降低)
        wk_strong = _weekly_strong(code)

        tags = []
        score = 0
        channel = None

        # ===== 通道A: 深/中超跌反弹 =====
        if dd60 <= -0.20:
            if pats and stabilized:
                score = 2
                if dd60 <= -0.25:
                    score += 1  # 深超跌
                if best_grade == "strong":
                    score += 1  # 强形态
                if ind_mom_ok:
                    score += 1  # 行业走强
                if mkt_dir == "up":
                    score += 1  # 大盘配合
                channel = "超跌反弹"
                tags = ["超跌企稳", f"形态{best_grade}"]
                if dd60 <= -0.25: tags.append("深超跌")
                if ind_mom_ok: tags.append("行业走强")
                if mkt_dir == "down": tags.append("大盘弱·谨慎")

        # ===== 通道B: 浅超跌启动(-20%~-10%) =====
        if channel is None and -0.20 < dd60 <= -0.10:
            # 需短期转强 + (强形态或放量或周线转强)
            if above_ma20 and (short_up or stabilized):
                cond = (best_grade in ("strong", "medium")) or vol_hit or wk_strong
                if cond:
                    score = 1
                    if best_grade == "strong": score += 1
                    if vol_hit: score += 1
                    if ind_mom_ok: score += 1
                    if stabilized: score += 1
                    if mkt_dir == "up": score += 1
                    channel = "浅超跌启动"
                    tags = ["浅超跌启动", f"形态{best_grade}" if best_grade != "weak" else "转强"]
                    if vol_hit: tags.append("放量")
                    if wk_strong: tags.append("周线转强")
                    if ind_mom_ok: tags.append("行业走强")

        # ===== 通道C: 周线转强(趋势启动) =====
        if channel is None and wk_strong and above_ma20 and not over:
            score = 1
            if pats: score += 1
            if direction == "down": score += 1  # 日线深回调买点
            elif direction == "side": score += 0.5
            if ind_mom_ok: score += 1
            if mkt_dir == "up" and ly["industry"]["direction"] == "up": score += 1  # 三层共振
            if gain60 > 60: score -= 1  # 已涨太多降权
            channel = "周线趋势"
            tags = ["周线趋势"]
            if pats: tags.append("+".join(sorted(set(p[0] for p in pats))[:2]))
            if direction == "down": tags.append("日线深回调")
            if ind_mom_ok: tags.append("行业走强")
            if mkt_dir == "up" and ly["industry"]["direction"] == "up": tags.append("三层共振")

        if channel is None:
            return None
        # 质量门槛: 综合评分>=2才推荐
        if score < 2:
            return None
        level = min(3, int(round(score)))
        tags.append(f"评分{score:.0f}")
        return {
            "code": code, "name": name, "ind": ind,
            "type": channel, "level": level,
            "price": round(close, 2), "change_pct": round(chg * 100, 2),
            "tags": tags, "dd60": round(dd60 * 100, 1),
            "score": round(score, 1), "pats": [p[0] for p in pats],
        }
    except Exception:
        return None

# ===== 1. 模拟2026-06-15扫描 =====
SCAN_DATE = "2026-06-15"
END_DATE = "2026-08-14"
ASOF = SCAN_DATE
print(f"\n===== 模拟 {SCAN_DATE} 扫描(改进版v2, 进取模式) =====")
t0 = time.time()
sigs = []
for it in pool:
    code = it["code"]
    rows = [r for r in klines.get(code, []) if r["date"] <= SCAN_DATE]
    if len(rows) < 60:
        continue
    r = _scan_one_v2(it)
    if r:
        sigs.append(r)
print(f"扫描完成: {len(sigs)}只推荐, 耗时{time.time()-t0:.1f}s")
# 按level和score排序
sigs.sort(key=lambda x: (-x["level"], -x.get("score", 0)))
for s in sigs[:15]:
    print(f"  [{s['level']}星] {s['name']}({s['code']}) {s['type']} @{s['price']} {' '.join(s['tags'][:4])}")
if len(sigs) > 15:
    print(f"  ...共{len(sigs)}只")

# ===== 2. 环境评分(进取模式动态仓位) =====
env = env_judge.env_action(asof=SCAN_DATE)
pos_pct = env["pos_pct"]
print(f"\n环境评分: {env['score']}/6, 进取模式仓位: {pos_pct:.0f}%")

# ===== 3. 模拟交易 =====
print(f"\n===== 模拟交易 {SCAN_DATE} ~ {END_DATE} =====")
CASH_INIT = 100000.0
MAX_HOLD = 5
buy_list = sigs[:MAX_HOLD]
alloc = CASH_INIT * pos_pct / 100 / len(buy_list) if buy_list else 0

cash = CASH_INIT
holdings = {}
closed = []

for s in buy_list:
    price = s["price"]
    qty = int(alloc / price / 100) * 100
    if qty <= 0:
        continue
    cost = qty * price
    cash -= cost
    holdings[s["code"]] = {
        "name": s["name"], "entry": price, "entry_d": SCAN_DATE,
        "qty": qty, "high": price, "type": s["type"], "tags": s["tags"],
    }
    print(f"  买入 {s['name']}({s['code']}) @{price:.2f} {qty}股 成本{cost:.0f}元 [{s['type']} {s['level']}星]")

print(f"  现金剩余: {cash:.0f}元, 持仓{len(holdings)}只")

# 逐日更新持仓+离场
trade_dates = sorted(set(r["date"] for r in mkt_rows if SCAN_DATE < r["date"] <= END_DATE))
print(f"\n  交易日: {len(trade_dates)}天 ({trade_dates[0]}~{trade_dates[-1]})")

for d in trade_dates:
    ASOF = d
    # 大盘方向(进取模式双确认: 破MA20 + 大盘转弱)
    mrr = [r for r in mkt_rows if r["date"] <= d]
    mcl = [r["close"] for r in mrr]
    mkt_down = (len(mcl) >= 5 and layers._direction(mcl) == "down")
    mkt_weak = (len(mcl) >= 20 and mcl[-1] < sum(mcl[-20:]) / 20)

    for code in list(holdings.keys()):
        h = holdings[code]
        rows = [r for r in klines.get(code, []) if r["date"] <= d]
        if not rows:
            continue
        px = rows[-1]["close"]
        h["price"] = px
        h["high"] = max(h["high"], px)

        exit_px, reason = None, ""
        # 移动止损10%
        if px <= h["high"] * 0.90:
            exit_px, reason = px, "移动止损-10%"
        # 进取模式: 破MA20 + 大盘转弱 双确认
        elif len(rows) >= 20:
            ma20 = sum(r["close"] for r in rows[-20:]) / 20
            if px < ma20 and (mkt_down or mkt_weak):
                exit_px, reason = px, "破MA20+大盘弱"
        # 止损10%
        elif px < h["entry"] * 0.90:
            exit_px, reason = px, "止损-10%"

        if exit_px:
            cash += exit_px * h["qty"]
            ret = exit_px / h["entry"] - 1
            closed.append({**h, "exit_d": d, "exit": exit_px, "ret": ret, "reason": reason})
            print(f"  卖出 {h['name']}({code}) @{exit_px:.2f} 收益{ret*100:+.1f}% [{reason}]")
            del holdings[code]

# ===== 4. 结算 =====
print(f"\n===== 结算 {END_DATE} =====")
final_cash = cash
for code, h in holdings.items():
    rows = [r for r in klines.get(code, []) if r["date"] <= END_DATE]
    px = rows[-1]["close"] if rows else h["price"]
    final_cash += px * h["qty"]
    h["price"] = px

total_ret = final_cash / CASH_INIT - 1
realized = sum(c["ret"] * c["entry"] * c["qty"] for c in closed)
unreal = sum((h["price"]/h["entry"]-1) * h["entry"] * h["qty"] for h in holdings.values())

print(f"  最终资产: {final_cash:.0f}元")
print(f"  总收益: {total_ret*100:+.1f}% ({final_cash-CASH_INIT:+.0f}元)")
print(f"  已实现盈亏: {realized:+.0f}元 ({len(closed)}笔)")
print(f"  浮动盈亏: {unreal:+.0f}元 ({len(holdings)}只)")

if closed:
    win = sum(1 for c in closed if c["ret"] > 0)
    print(f"\n  已卖出明细({len(closed)}笔, 胜{win}败{len(closed)-win}):")
    for c in closed:
        print(f"    {c['name']} 买{c['entry_d']}@{c['entry']:.2f} 卖{c['exit_d']}@{c['exit']:.2f} {c['ret']*100:+.1f}% [{c['reason']}]")
    print(f"  卖出胜率: {win}/{len(closed)} = {win/len(closed)*100:.0f}%")

if holdings:
    print(f"\n  仍持有({len(holdings)}只):")
    for h in holdings.values():
        print(f"    {h['name']} 买{h['entry_d']}@{h['entry']:.2f} 现价{h['price']:.2f} 浮动{(h['price']/h['entry']-1)*100:+.1f}% {' '.join(h['tags'][:2])}")

# 持有期内最大回撤(未卖出持仓的浮亏峰值)
print(f"\n  持仓最大浮亏: {min((h['price']/h['entry']-1)*100 for h in holdings.values()) if holdings else 0:+.1f}%")

# 大盘对比
mkt_start = next(r["close"] for r in mkt_rows if r["date"] >= SCAN_DATE)
mkt_end = next(r["close"] for r in mkt_rows if r["date"] >= END_DATE)
mkt_ret = mkt_end / mkt_start - 1
print(f"\n  大盘对比: 上证指数 {SCAN_DATE}@{mkt_start:.0f} -> {END_DATE}@{mkt_end:.0f} = {mkt_ret*100:+.1f}%")
print(f"  超额收益: {(total_ret-mkt_ret)*100:+.1f}%")

# 保存结果
out = {
    "scan_date": SCAN_DATE, "end_date": END_DATE,
    "env": {"score": env["score"], "pos_pct": pos_pct},
    "sigs": sigs, "closed": closed, "holdings": list(holdings.values()),
    "final": final_cash, "total_ret": total_ret, "mkt_ret": mkt_ret,
}
json.dump(out, open(os.path.join(BASE, "research", "bt_0615_v2_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n结果已保存: research/bt_0615_v2_result.json")
