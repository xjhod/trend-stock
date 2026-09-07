# -*- coding: utf-8 -*-
"""回测: 2026-04-15 进取模式 10万本金 → 2026-06-14
扫描逻辑=同步后的改进版 scan_daily._scan_one(v2)
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
    elif period == "monthly":
        kdf = kdf.copy(); kdf["date"] = pd.to_datetime(kdf["date"]); kdf = kdf.set_index("date")
        kdf = kdf.resample("ME").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum","amount":"sum","pct_chg":"last","change":"last"}).dropna(subset=["close"]).reset_index()
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
scan_daily._calc_rating = lambda code, df: {"score": 0, "detail": []}
env_judge.get_mode = lambda: "bull"

# ===== 1. 扫描 =====
SCAN_DATE = "2026-02-15"; END_DATE = "2026-04-14"
ASOF = SCAN_DATE
print(f"\n===== 模拟 {SCAN_DATE} 扫描(改进版v2) =====")
t0 = time.time()
sigs = []
for it in pool:
    rows = [r for r in klines.get(it["code"], []) if r["date"] <= SCAN_DATE]
    if len(rows) < 60: continue
    r = scan_daily._scan_one(it)
    if r: sigs.append(r)
print(f"扫描完成: {len(sigs)}只推荐, 耗时{time.time()-t0:.1f}s")
sigs.sort(key=lambda x: (-x["level"], x["change_pct"]))
for s in sigs[:15]:
    print(f"  [{s['level']}星] {s['name']}({s['code']}) {s['type']} @{s['price']} {' '.join(s['tags'][:5])}")
if len(sigs) > 15: print(f"  ...共{len(sigs)}只")

# 推荐股后续表现
gains_map = {}
for it in pool:
    code = it["code"]; rows = klines.get(code, [])
    start_rows = [r for r in rows if r["date"] >= SCAN_DATE]
    end_rows = [r for r in rows if r["date"] <= END_DATE]
    if not start_rows or not end_rows: continue
    gains_map[code] = end_rows[-1]["close"] / start_rows[0]["close"] - 1
rec_gains = [gains_map.get(s["code"], 0) for s in sigs]
all_gains = list(gains_map.values())
print(f"\n推荐股({len(sigs)}只) 平均: {sum(rec_gains)/len(rec_gains)*100:+.1f}% | 上涨率: {sum(1 for g in rec_gains if g>0)}/{len(rec_gains)} = {sum(1 for g in rec_gains if g>0)/len(rec_gains)*100:.0f}%")
print(f"全池({len(all_gains)}只)  平均: {sum(all_gains)/len(all_gains)*100:+.1f}% | 上涨率: {sum(1 for g in all_gains if g>0)}/{len(all_gains)} = {sum(1 for g in all_gains if g>0)/len(all_gains)*100:.0f}%")

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
    mkt_weak = (len(mcl) >= 20 and mcl[-1] < sum(mcl[-20:]) / 20)
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
            ma20 = sum(r["close"] for r in rows[-20:]) / 20
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
unreal = sum((h["price"] / h["entry"] - 1) * h["entry"] * h["qty"] for h in holdings.values())
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
json.dump(out, open(os.path.join(BASE, "research", "bt_0215_v3_result.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n结果已保存: research/bt_0215_v3_result.json")
