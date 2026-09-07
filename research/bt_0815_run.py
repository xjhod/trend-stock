# -*- coding: utf-8 -*-
"""回测: 2026-08-15 进取模式 10万本金, 从推荐个股操作, 到2026-09-05结算"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import data_fetcher as df
import scan_daily
import layers
import analysis as an

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = json.load(open(os.path.join(BASE, "research", "bt_0815_data.json"), encoding="utf-8"))
klines = data["klines"]
mkt_rows = data["market"]
ind_rows = data["industry"]
pool = data["pool"]
print(f"数据加载: {len(klines)}只股票, 大盘{len(mkt_rows)}天, 行业{len(ind_rows)}个")

# 全局asof日期(monkey-patch用)
ASOF = None

def hist_get_kline(code, period="daily", limit=300, adjust="qfq"):
    """历史模式get_kline: 从预加载数据取, 截断到ASOF"""
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

def hist_get_market(limit=80):
    rows = [r for r in mkt_rows if r["date"] <= ASOF] if ASOF else mkt_rows
    return rows[-limit:]

def hist_load_ind():
    if ASOF:
        return {k: [r for r in v if r["date"] <= ASOF] for k, v in ind_rows.items()}
    return ind_rows

# Monkey-patch
df.get_kline = hist_get_kline
layers.get_market_kline = lambda *a, **k: hist_get_market()
layers._load_ind_cache = lambda: hist_load_ind()
scan_daily.get_kline = hist_get_kline

def hist_scan(asof):
    """历史扫描: 截断到asof, 运行_scan_one"""
    global ASOF
    ASOF = asof
    sigs = []
    for it in pool:
        code = it["code"]
        if code not in klines:
            continue
        rows = [r for r in klines[code] if r["date"] <= asof]
        if len(rows) < 60:
            continue
        try:
            r = scan_daily._scan_one(it)
            if r:
                sigs.append(r)
        except Exception:
            pass
    return sigs

# ===== 1. 模拟2026-08-15扫描 =====
SCAN_DATE = "2026-08-15"
print(f"\n===== 模拟 {SCAN_DATE} 扫描(进取模式) =====")
t0 = time.time()
sigs = hist_scan(SCAN_DATE)
print(f"扫描完成: {len(sigs)}只推荐, 耗时{time.time()-t0:.1f}s")
for s in sigs[:10]:
    print(f"  {s['name']}({s['code']}) {s['type']} Lv{s['level']} @{s['price']} {' '.join(s['tags'][:3])}")
if len(sigs) > 10:
    print(f"  ...共{len(sigs)}只")

# ===== 2. 模拟交易 =====
print(f"\n===== 模拟交易 {SCAN_DATE} ~ 2026-09-05 =====")
# 进取模式: 动态仓位, 最低30%, 永不清仓
# 简化: 用80%仓位(进取), 等权买入最多5只推荐
CASH_INIT = 100000.0
POS_PCT = 0.80  # 进取模式80%仓位
MAX_HOLD = 5
buy_list = sorted(sigs, key=lambda x: -x["level"])[:MAX_HOLD]
alloc = CASH_INIT * POS_PCT / len(buy_list) if buy_list else 0

cash = CASH_INIT
holdings = {}
closed = []

# 买入(8/15收盘价)
for s in buy_list:
    price = s["price"]
    qty = int(alloc / price / 100) * 100  # 整手
    if qty <= 0:
        continue
    cost = qty * price
    cash -= cost
    holdings[s["code"]] = {
        "name": s["name"], "entry": price, "entry_d": SCAN_DATE,
        "qty": qty, "high": price, "type": s["type"], "tags": s["tags"],
    }
    print(f"  买入 {s['name']}({s['code']}) @{price:.2f} {qty}股 成本{cost:.0f}元")

print(f"  现金剩余: {cash:.0f}元, 持仓{len(holdings)}只")

# 逐日更新持仓+离场(8/16~9/5)
trade_dates = sorted(set(r["date"] for r in mkt_rows if SCAN_DATE < r["date"] <= "2026-09-05"))
print(f"\n  交易日: {len(trade_dates)}天 ({trade_dates[0]}~{trade_dates[-1]})")

for d in trade_dates:
    ASOF = d
    # 大盘方向
    mrr = [r for r in mkt_rows if r["date"] <= d]
    mcl = [r["close"] for r in mrr]
    mkt_down = (len(mcl) >= 5 and layers._direction(mcl) == "down")
    
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
        # 破MA20+大盘弱
        elif mkt_down and len(rows) >= 20:
            ma20 = sum(r["close"] for r in rows[-20:]) / 20
            if px < ma20:
                exit_px, reason = px, "破MA20+大盘弱"
        # 止损10%
        elif px < h["entry"] * 0.90:
            exit_px, reason = px, "止损-10%"
        
        if exit_px:
            cash += exit_px * h["qty"]
            ret = exit_px / h["entry"] - 1
            closed.append({**h, "exit_d": d, "exit": exit_px, "ret": ret, "reason": reason})
            print(f"  卖出 {h['name']}({code}) @{exit_px:.2f} 收益{ret*100:+.1f}% [{reason}] (买{h['entry_d']}@{h['entry']:.2f})")
            del holdings[code]

# ===== 3. 结算(9/5) =====
print(f"\n===== 结算 2026-09-05 =====")
final_cash = cash
for code, h in holdings.items():
    rows = [r for r in klines.get(code, []) if r["date"] <= "2026-09-05"]
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
print(f"  现金: {cash:.0f}元")

if closed:
    print(f"\n  已卖出明细:")
    win = sum(1 for c in closed if c["ret"] > 0)
    for c in closed:
        print(f"    {c['name']} 买{c['entry_d']}@{c['entry']:.2f} 卖{c['exit_d']}@{c['exit']:.2f} {c['ret']*100:+.1f}% [{c['reason']}]")
    print(f"  胜率: {win}/{len(closed)} = {win/len(closed)*100:.0f}%")

if holdings:
    print(f"\n  仍持有:")
    for h in holdings.values():
        print(f"    {h['name']} 买{h['entry_d']}@{h['entry']:.2f} 现价{h['price']:.2f} 浮动{(h['price']/h['entry']-1)*100:+.1f}% {' '.join(h['tags'][:2])}")

# 大盘对比
mkt_start = next(r["close"] for r in mkt_rows if r["date"] >= SCAN_DATE)
mkt_end = mkt_rows[-1]["close"]
mkt_ret = mkt_end / mkt_start - 1
print(f"\n  大盘对比: 上证指数 {SCAN_DATE}@{mkt_start:.0f} -> 9/5@{mkt_end:.0f} = {mkt_ret*100:+.1f}%")
print(f"  超额收益: {(total_ret-mkt_ret)*100:+.1f}%")
