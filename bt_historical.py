# -*- coding: utf-8 -*-
"""历史回测：假设 2026-06-30 收盘扫描推荐 → 7/1 开盘买入 → 按离场规则跟踪至今天。

离场规则（复用用户实证规则, 与软件一致, 无固定持有期限）:
  1. 移动止损: 收盘 <= 买入以来最高收盘 * 0.90 -> 卖出
  2. 系统性转弱 → 清弱势
  3. 双确认: 个股跌破MA20 且 大盘转弱 -> 卖出
  4. 趋势未破位则继续持有（让利润奔跑）
对比口径: 有规则 vs 无规则(一直持有)
"""
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from data_fetcher import get_kline
import analysis as an
import layers
import scan_daily

BASE = os.path.dirname(os.path.abspath(__file__))
ASOF = "2026-06-30"          # 收盘扫描日
BUY_DAY = "2026-07-01"       # 买入日（取当日或之后首个交易日开盘）
OUT = os.path.join(BASE, "research", "bt_630.json")


def _dir_at(rows, upto):
    """rows[:upto] 的方向判断"""
    closes = [r["close"] for r in rows[:upto]]
    return layers._direction(closes) if len(closes) >= 60 else "unknown"


def scan_asof(asof=ASOF):
    """历史扫描: 用 asof 收盘(含当日)数据判定机会"""
    pool = json.load(open(os.path.join(BASE, "highfit_pool.json"), encoding="utf-8"))
    mkt_rows = [r for r in layers.get_market_kline(400) if r["date"] <= asof]
    mkt_dir = layers._direction([r["close"] for r in mkt_rows]) if len(mkt_rows) >= 60 else "unknown"
    ind_cache = layers._load_ind_cache()
    results = []

    def scan_one(it):
        code = it["code"]
        try:
            df = get_kline(code, "daily", 400, "qfq")
            if df is None or len(df) < 60:
                return None
            df = df[df["date"] <= asof]
            if len(df) < 60:
                return None
            trend = an.analyze_trend(df, "日线")
            direction = trend.get("direction", "sideways")
            pats = scan_daily.detect_bullish(df)
            vol_hit = any(p[2] for p in pats)
            last = df.iloc[-1]
            chg = float(last["close"]) / float(df.iloc[-2]["close"]) - 1 if len(df) >= 2 else 0
            hi60 = max(df["high"].tolist()[-60:])
            dd60 = float(last["close"]) / hi60 - 1
            # 行业方向（历史截断）
            ind = it.get("ind", "")
            ind_rows = [r for r in ind_cache.get(ind, []) if r["date"] <= asof]
            ind_dir = layers._direction([r["close"] for r in ind_rows]) if len(ind_rows) >= 60 else "unknown"
            stl = direction if direction != "sideways" else "none"
            reso = (mkt_dir == "up" and ind_dir == "up" and stl == "up")

            # 门卫: 大盘 不下跌+不在顶部 (两通道必过)
            mkt_closes = [r["close"] for r in mkt_rows]
            mkt_dir_ok = (mkt_dir != "down")
            mkt_mom_ok = True
            if len(mkt_closes) >= 65:
                mkt_mom_ok = mkt_closes[-1] >= mkt_closes[-6] * 0.985
            if not (mkt_dir_ok and mkt_mom_ok):
                return None
            # 超跌通道: 行业仅要求不在下跌(不做顶部限制, 超跌补涨不依赖行业位置)
            if ind_dir == "down":
                return None

            # 通道A 超跌企稳(抄底): 超跌 + 企稳(不再创新低+站上MA10) + 形态(位置量能配合)
            stabilized = scan_daily._stabilized(df)
            if dd60 <= -0.20 and pats and stabilized:
                # 形态质量: 取最优形态, weak过滤 [回测: strong/med 40% vs weak 20%]
                best_score, best_grade = -99, "weak"
                for pn, pi, pvc in pats:
                    sc, lv, _ = scan_daily._pattern_quality(df, pi, pn, pvc)
                    if sc > best_score:
                        best_score, best_grade = sc, lv
                if best_grade == "weak":
                    return None
                level = 1
                if dd60 <= -0.25:
                    level += 1
                if mkt_dir == "down":
                    level += 1
                level = min(level, 2)
                return {
                    "code": code, "name": it.get("name", ""), "ind": ind,
                    "type": "rebound", "level": level,
                    "asof_price": round(float(last["close"]), 2),
                    "dd60": round(dd60 * 100, 1),
                    "pats": [p[0] for p in pats],
                    "direction": direction,
                }
            # 通道B 趋势机会(追涨)
            if direction == "up":
                # 过滤0: 行业门卫(not down + 不在顶部, 趋势追涨怕高位接力)
                if not scan_daily._ind_gate(ind, asof=asof):
                    return None
                # 过滤1: 高位过热
                over, _ = scan_daily._overheated(df)
                if over:
                    return None
                # 过滤2: 趋势阶段（启动/确认期才追）
                tm = scan_daily._trend_metrics(df)
                if tm is None:
                    return None
                gain60, bias, dist_hi, _ = tm
                if gain60 > 100 or bias > 20 or dist_hi < 5 or gain60 > 60 or bias > 10:
                    return None
                # 过滤3: 板块轮动后期（个股涨幅远超行业不追）
                ig = scan_daily._ind_gain(ind, asof=asof)
                if ig is not None and gain60 - ig > 40:
                    return None
                # 过滤4: 行业未破位（行业 close<MA20 → 拦）
                if len(ind_rows) >= 25:
                    icl = [r["close"] for r in ind_rows]
                    if icl[-1] < sum(icl[-20:]) / 20:
                        return None
                # 过滤5: 行业位置（行业60日涨幅>25% = 高位, 不做）
                ig2 = scan_daily._ind_gain(ind, asof=asof)
                if ig2 is not None and ig2 > 25:
                    return None
                score = 0
                if reso:
                    score += 2
                if pats:
                    score += 2 if vol_hit else 1
                if score < 1:
                    return None
                return {
                    "code": code, "name": it.get("name", ""), "ind": ind,
                    "type": "trend", "level": score,
                    "asof_price": round(float(last["close"]), 2),
                    "dd60": round(dd60 * 100, 1),
                    "pats": [p[0] for p in pats],
                    "direction": direction,
                    "resonance": reso,
                }
            return None
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(scan_one, it) for it in pool]
        for f in as_completed(futs):
            r = f.result()
            if r:
                results.append(r)
    results.sort(key=lambda x: (-x["level"], x.get("asof_price", 0)))
    return results


def simulate(recs, use_rules=True, max_days=None, asof=ASOF, buy_day=BUY_DAY):
    """7/1(或之后首个交易日)开盘买入, 逐日按规则离场, 持有至今"""
    mkt_rows = layers.get_market_kline(400)
    out = []

    def sim_one(rec):
        code = rec["code"]
        try:
            df = get_kline(code, "daily", 400, "qfq")
            if df is None or len(df) < 30:
                return None
            rows = df.to_dict("records")
            dates = [str(r["date"]) for r in rows]
            # 买入日索引: 第一个 date >= buy_day
            idx_buy = None
            for i, dt in enumerate(dates):
                if dt >= buy_day:
                    idx_buy = i
                    break
            if idx_buy is None or idx_buy >= len(rows) - 1:
                return None
            entry = float(rows[idx_buy]["open"])
            if entry <= 0:
                return None
            last_i = len(rows) - 1
            # 逐日跟踪
            exit_i = None
            exit_price = None
            reason = ""
            high_since = entry
            for i in range(idx_buy, last_i + 1):
                px = float(rows[i]["close"])
                high_since = max(high_since, px)
                days = i - idx_buy + 1
                if use_rules:
                    if px <= high_since * 0.90:
                        exit_i, exit_price, reason = i, px, "移动止损(自最高回撤≥10%)"
                        break
                    if max_days and days >= max_days:
                        exit_i, exit_price, reason = i, px, f"{max_days}日到期离场"
                        break
                    # 双确认: 破MA20 + 大盘转弱
                    try:
                        closes = [float(r["close"]) for r in rows[:i]]
                        ma20 = sum(closes[-20:]) / min(20, len(closes))
                        mkt_now = _dir_at(mkt_rows, i + 1) if i + 1 <= len(mkt_rows) else "unknown"
                        if px < ma20 and mkt_now == "down":
                            exit_i, exit_price, reason = i, px, "破MA20+大盘转弱双确认"
                            break
                    except Exception:
                        pass
            if exit_i is not None:
                ret = exit_price / entry - 1
                return {
                    "code": code, "name": rec.get("name", ""), "type": rec["type"],
                    "entry_date": dates[idx_buy], "entry_price": round(entry, 2),
                    "exit_date": dates[exit_i], "exit_price": round(exit_price, 2),
                    "ret_pct": round(ret * 100, 2), "reason": reason,
                    "hold_days": exit_i - idx_buy + 1,
                }
            # 持有至今
            cur = float(rows[last_i]["close"])
            ret = cur / entry - 1
            return {
                "code": code, "name": rec.get("name", ""), "type": rec["type"],
                "entry_date": dates[idx_buy], "entry_price": round(entry, 2),
                "exit_date": "至今(持有)", "exit_price": round(cur, 2),
                "ret_pct": round(ret * 100, 2), "reason": "持有至今",
                "hold_days": last_i - idx_buy + 1,
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(sim_one, rec) for rec in recs]
        for f in as_completed(futs):
            r = f.result()
            if r:
                out.append(r)
    out.sort(key=lambda x: x["ret_pct"], reverse=True)
    return out


def summarize(items, label=""):
    if not items:
        return {"label": label, "n": 0}
    rets = [x["ret_pct"] for x in items]
    wins = sum(1 for r in rets if r > 0)
    avg = sum(rets) / len(rets)
    mx = max(rets); mn = min(rets)
    med = sorted(rets)[len(rets) // 2]
    return {
        "label": label, "n": len(items), "wins": wins, "win_rate": round(wins / len(items) * 100, 1),
        "avg_ret": round(avg, 2), "median_ret": round(med, 2),
        "max_ret": round(mx, 2), "min_ret": round(mn, 2),
    }


def main():
    print(f"== 历史回测: {ASOF} 收盘扫描 → {BUY_DAY} 开盘买入 → 至今 ==")
    recs = scan_asof()
    print(f"6/30 推荐 {len(recs)} 只（抄底 {sum(1 for r in recs if r['type']=='rebound')} / 趋势 {sum(1 for r in recs if r['type']=='trend')}）")
    # 有规则
    ruled = simulate(recs, use_rules=True)
    # 无规则(一直持有)
    hold = simulate(recs, use_rules=False)
    s_r = summarize(ruled, "有规则(移动止损+清弱势+双确认, 无到期)")
    s_h = summarize(hold, "无规则(一直持有)")
    print(json.dumps({"scan": {"date": ASOF, "n": len(recs),
                               "rebound": sum(1 for r in recs if r['type'] == 'rebound'),
                               "trend": sum(1 for r in recs if r['type'] == 'trend')},
                       "ruled": s_r, "hold": s_h,
                       "ruled_items": ruled, "hold_items": hold},
                      ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
