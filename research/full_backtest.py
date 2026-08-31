# -*- coding: utf-8 -*-
"""全历史模拟交易: 2025-01-01 起, 10万本金, 用程序全部规则自动买卖"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from concurrent.futures import ThreadPoolExecutor, as_completed
import scan_daily, layers, analysis as an
from data_fetcher import get_kline

# ========== 配置 ==========
START = "2025-01-01"
INIT_CAP = 100000.0
PER_POS = 20000.0       # 每只投入
MAX_POS = 5             # 最多同时持仓
POOL_FILE = "highfit_pool.json"
CACHE_FILE = "research/kline_cache.json"  # 预加载缓存

# ========== 预加载K线 ==========
def load_pool():
    return json.load(open(POOL_FILE, encoding="utf-8"))

def preload_kline(pool):
    """预加载全部池股票日K线到内存"""
    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            raw = json.load(open(CACHE_FILE, encoding="utf-8"))
            if raw.get("n") == len(pool) and raw.get("pool") == [p["code"] for p in pool]:
                cache = {k: raw["data"][k] for k in raw["data"]}
                print(f"[缓存] 加载 {len(cache)} 只K线")
                return cache
        except Exception:
            pass
    def load(it):
        try:
            df = get_kline(it["code"], "daily", 400, "qfq")
            if df is None or len(df) < 60:
                return None
            return (it["code"], [{"date": str(r["date"]), "open": float(r["open"]),
                                  "high": float(r["high"]), "low": float(r["low"]),
                                  "close": float(r["close"]), "volume": float(r["volume"])}
                                 for r in df.to_dict("records")])
        except Exception:
            return None
    print("[拉取] 预加载K线...")
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = [ex.submit(load, it) for it in pool]
        for f in as_completed(futs):
            r = f.result()
            if r: cache[r[0]] = r[1]
    print(f"[拉取] 完成 {len(cache)} 只")
    json.dump({"n": len(pool), "pool": [p["code"] for p in pool], "data": cache},
              open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    return cache

def df_at(rows, asof):
    """rows → 截断到 asof 的 DataFrame(类)"""
    import pandas as pd
    rr = [r for r in rows if r["date"] <= asof]
    if len(rr) < 60: return None
    return pd.DataFrame(rr)

# ========== 大盘 asof 判定 ==========
def mkt_snapshot(asof):
    """大盘在 asof 的状态: (gate_ok, sysweak, mkt_dir)"""
    rows = [r for r in layers.get_market_kline(400) if r["date"] <= asof]
    closes = [r["close"] for r in rows]
    if len(closes) < 65: return True, False, "unknown"
    mkt_dir = layers._direction(closes)
    gate = True
    if mkt_dir == "down": gate = False
    c, c5 = closes[-1], closes[-6]
    if c < c5 * 0.985: gate = False
    ma20 = sum(closes[-20:])/20; ma60 = sum(closes[-60:])/60
    sysweak = (ma20 < ma60) and (c < c5 * 0.98)
    return gate, sysweak, mkt_dir

def ind_snapshot(ind, asof):
    """行业在 asof: (gate_ok(not down+不在顶), notdown)"""
    rows = [r for r in layers._load_ind_cache().get(ind, []) if r["date"] <= asof]
    closes = [r["close"] for r in rows]
    if len(closes) < 61: return True, True
    d = layers._direction(closes)
    notdown = d != "down"
    hi60 = max(closes[-60:])
    notop = closes[-1] < hi60 * 0.98
    return (notdown and notop), notdown

# ========== 单股判定(截断, 复用 scan_daily 规则) ==========
def judge(it, df, asof, mkt_dir):
    """返回 (type, level, tags) 或 None. 完整复刻 _scan_one 的三层漏斗规则"""
    code = it["code"]; ind = it.get("ind", "")
    try:
        trend = an.analyze_trend(df, "日线")
        direction = trend.get("direction", "sideways")
        pats = scan_daily.detect_bullish(df)
        last = df.iloc[-1]
        hi60 = max(df["high"].tolist()[-60:])
        dd60 = float(last["close"]) / hi60 - 1
        # 门卫: 行业(超跌只查not down, 趋势查gate)
        _, ind_notdown = ind_snapshot(ind, asof)
        # 通道A: 超跌企稳
        stabilized = scan_daily._stabilized(df)
        if dd60 <= -0.20 and pats and stabilized and ind_notdown:
            best_grade = "weak"
            for pn, pi, pvc in pats:
                sc, lv, _ = scan_daily._pattern_quality(df, pi, pn, pvc)
                if sc > -99: best_grade = max(best_grade, lv, key=lambda x: ["weak","medium","strong"].index(x))
            if best_grade != "weak":
                return ("rebound", 1, ["超跌企稳"])
        # 通道B: 趋势
        if direction == "up":
            over, _ = scan_daily._overheated(df)
            if over: return None
            tm = scan_daily._trend_metrics(df)
            if tm is None: return None
            gain60, bias, dist_hi, _ = tm
            if gain60 > 100 or bias > 20 or dist_hi < 5 or gain60 > 60 or bias > 10: return None
            ig = scan_daily._ind_gain(ind, asof=asof)
            if ig is not None and gain60 - ig > 40: return None
            ind_rows = [r for r in layers._load_ind_cache().get(ind, []) if r["date"] <= asof]
            if len(ind_rows) >= 25:
                icl = [r["close"] for r in ind_rows]
                if icl[-1] < sum(icl[-20:])/20: return None
            ig2 = scan_daily._ind_gain(ind, asof=asof)
            if ig2 is not None and ig2 > 25: return None
            # 行业gate(趋势): not down + 不在顶
            ind_gate, _ = ind_snapshot(ind, asof)
            if not ind_gate: return None
            return ("trend", 2, ["上升趋势"])
        return None
    except Exception:
        return None

# ========== 主循环 ==========
def run_backtest():
    pool = load_pool()
    klines = preload_kline(pool)
    # 交易日列表(用大盘K线日期, 从START起)
    mkt_all = [r["date"] for r in layers.get_market_kline(400)]
    trade_days = [d for d in mkt_all if d >= START]
    print(f"交易日: {len(trade_days)} 个 ({trade_days[0]} ~ {trade_days[-1]})")

    cash = INIT_CAP
    positions = []  # {code,name,entry,entry_date,qty,cost,hi,days}
    trades = []     # 已平仓
    daily_recs = [] # 每日推荐(供统计)
    buy_price_cache = {}

    for ti, asof in enumerate(trade_days):
        # --- 1. 离场检查(先卖) ---
        gate, sysweak, mkt_dir = mkt_snapshot(asof)
        new_pos = []
        for p in positions:
            rows = klines.get(p["code"])
            if not rows:
                new_pos.append(p); continue
            rr = [r for r in rows if r["date"] <= asof]
            if not rr:
                new_pos.append(p); continue
            px = rr[-1]["close"]
            p["hi"] = max(p["hi"], px)
            p["days"] += 1
            reason = None
            c20 = sum(r["close"] for r in rr[-21:-1]) / 20 if len(rr) >= 21 else px
            if px <= p["hi"] * 0.90:
                reason = "移动止损"
            elif sysweak:
                if px <= p["entry"] or px < c20:
                    reason = "系统性转弱·清弱势"
            else:
                mkt_down = mkt_dir == "down"
                if px < c20 and mkt_down:
                    reason = "破MA20+大盘弱"
            if reason:
                ret = (px / p["entry"] - 1)
                trades.append({"code": p["code"], "name": p["name"], "entry_date": p["entry_date"],
                               "exit_date": asof, "ret_pct": round(ret * 100, 2), "reason": reason,
                               "profit": round(px * p["qty"] - p["cost"], 2)})
                cash += px * p["qty"]
            else:
                new_pos.append(p)
        positions = new_pos

        # --- 2. 扫描推荐(收盘信号) ---
        gate, sysweak, mkt_dir = mkt_snapshot(asof)
        if gate:
            recs = []
            for it in pool:
                rows = klines.get(it["code"])
                if not rows: continue
                df = df_at(rows, asof)
                if df is None: continue
                r = judge(it, df, asof, mkt_dir)
                if r:
                    recs.append((it, r[0], r[1], r[2]))
            # 按优先级排序: trend优先? level
            recs.sort(key=lambda x: -x[2])
            daily_recs.append((asof, [x[0]["name"] for x in recs]))
        else:
            recs = []
            daily_recs.append((asof, []))

        # --- 3. 买入(次日开盘) ---
        if recs and len(positions) < MAX_POS:
            # 用次日开盘价买入
            for it, typ, lv, tags in recs:
                if len(positions) >= MAX_POS: break
                if cash < PER_POS: break
                rows = klines.get(it["code"])
                if not rows: continue
                # 找 asof 后一个交易日
                dates = [r["date"] for r in rows]
                idx = None
                for k, d in enumerate(dates):
                    if d > asof:
                        idx = k; break
                if idx is None: continue
                open_px = rows[idx]["open"]
                if open_px <= 0: continue
                # 避免重复持仓
                if any(p["code"] == it["code"] for p in positions): continue
                qty = int(PER_POS / open_px)
                if qty <= 0: continue
                cost = qty * open_px
                cash -= cost
                positions.append({"code": it["code"], "name": it.get("name", ""),
                                  "entry": open_px, "entry_date": dates[idx],
                                  "qty": qty, "cost": cost, "hi": open_px, "days": 0})
    # 期末: 未平仓按最新价结算
    final_val = cash
    for p in positions:
        rows = klines.get(p["code"])
        px = rows[-1]["close"] if rows else p["entry"]
        final_val += px * p["qty"]
    return {"cash": cash, "positions": positions, "trades": trades, "final_val": final_val,
            "init": INIT_CAP, "daily_recs": daily_recs, "trade_days": trade_days}

if __name__ == "__main__":
    r = run_backtest()
    fin = r["final_val"]
    print("\n" + "="*50)
    print(f"初始资金: {r['init']:.0f}")
    print(f"期末总资产: {fin:.0f}  (收益率 {(fin/r['init']-1)*100:.2f}%)")
    print(f"已平仓: {len(r['trades'])} 笔  在持: {len(r['positions'])} 只  剩余现金: {r['cash']:.0f}")
    if r["trades"]:
        wins = sum(1 for t in r["trades"] if t["ret_pct"] > 0)
        total_profit = sum(t["profit"] for t in r["trades"])
        print(f"平仓胜率: {wins}/{len(r['trades'])} ({wins/len(r['trades'])*100:.0f}%)  已实现盈亏: {total_profit:.0f}")
        print("\n盈利前10:")
        for t in sorted(r["trades"], key=lambda x: -x["ret_pct"])[:10]:
            print(f"  {t['name']:8s} {t['entry_date']}→{t['exit_date']} {t['ret_pct']:+.1f}% [{t['reason']}]")
        print("\n亏损前10:")
        for t in sorted(r["trades"], key=lambda x: x["ret_pct"])[:10]:
            print(f"  {t['name']:8s} {t['entry_date']}→{t['exit_date']} {t['ret_pct']:+.1f}% [{t['reason']}]")
        # 离场原因分布
        from collections import Counter
        print("\n离场原因:", dict(Counter(t["reason"] for t in r["trades"])))
    print("="*50)
