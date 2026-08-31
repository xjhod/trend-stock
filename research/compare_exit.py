import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bt_historical, scan_daily
from data_fetcher import get_kline

def simulate(buy, recs, mode):
    """逐日模拟离场规则。mode=1全清, mode=2清弱势"""
    klines = {}
    for r in recs:
        df = get_kline(r["code"], "daily", 400, "qfq")
        if df is None: continue
        klines[r["code"]] = df.to_dict("records")
    if not klines: return []
    code_dates = {c: [str(x["date"]) for x in rows] for c, rows in klines.items()}
    alld = sorted(set(d for c in code_dates for d in code_dates[c] if d >= buy))
    if not alld: return []
    pos = {}
    for r in recs:
        rows = klines.get(r["code"])
        if not rows: continue
        entry = None
        for k, d in enumerate([str(x["date"]) for x in rows]):
            if d >= buy: entry = float(rows[k]["open"]); break
        if entry is None: continue
        pos[r["code"]] = {"entry": entry, "hi": entry, "day": 0, "r": r}
    exits = []
    for d in alld:
        mkt = [r for r in scan_daily.layers.get_market_kline(400) if r["date"] <= d]
        mc = [r["close"] for r in mkt]
        sysweak = False
        if len(mc) >= 65:
            ma20=sum(mc[-20:])/20; ma60=sum(mc[-60:])/60
            sysweak = (ma20 < ma60) and (mc[-1] < mc[-6]*0.98)
        mkt_down = len(mc)>=60 and mc[-1] < sum(mc[-20:])/20
        for code in list(pos.keys()):
            p = pos[code]; p["day"] += 1
            rows = klines[code]
            idxs = [k for k,dd in enumerate(code_dates[code]) if dd==d]
            if not idxs: continue
            i = idxs[0]; px = float(rows[i]["close"]); p["hi"] = max(p["hi"], px)
            c20 = sum(float(x["close"]) for x in rows[max(0,i-20):i])/20
            reason = None
            if px <= p["hi"]*0.90:
                reason = "移动止损"
            elif p["day"] >= 20:
                reason = "20日到期"
            elif sysweak:
                if mode == 1:
                    reason = "系统性转弱·全清"
                elif px <= p["entry"] or px < c20:
                    reason = "系统性转弱·清弱势"
            else:
                if px < c20 and mkt_down:
                    reason = "破MA20+大盘弱"
            if reason:
                exits.append({"d":d, "code":code, "reason":reason,
                              "ret": round((px/p["entry"]-1)*100,2), "r":p["r"]})
                del pos[code]
    for code, p in pos.items():
        rows = klines[code]; px = float(rows[-1]["close"])
        exits.append({"d":alld[-1], "code":code, "reason":"持有至今",
                      "ret": round((px/p["entry"]-1)*100,2), "r":p["r"]})
    return exits

ASOFS = [("2026-05-08","2026-05-11"), ("2026-05-13","2026-05-14"), ("2026-05-25","2026-05-26"),
         ("2026-06-05","2026-06-08"), ("2026-06-12","2026-06-15"), ("2026-06-18","2026-06-19"),
         ("2026-06-22","2026-06-23"), ("2026-06-30","2026-07-01")]
results = {1: [], 2: []}
for asof, buy in ASOFS:
    recs = bt_historical.scan_asof(asof=asof)
    if not recs:
        print(f"{asof}: 0推荐(门卫拦截)")
        continue
    for mode in [1, 2]:
        ex = simulate(buy, recs, mode)
        results[mode].extend(ex)
    n1 = len(results[1]); n2 = len(results[2])
    r1 = [e["ret"] for e in results[1] if e["d"]<=buy or True]
    print(f"{asof}: 推荐{len(recs)} 累计 方案1={n1} 方案2={n2}")

def stat(name, exs):
    rets = [e["ret"] for e in exs]
    w = sum(1 for r in rets if r>0)
    reasons = {}
    for e in exs: reasons[e["reason"]] = reasons.get(e["reason"],0)+1
    print(f"\n{name}: {len(rets)}只 胜率{w/len(rets)*100:.0f}% 均{sum(rets)/len(rets):.2f}% "
          f"最大亏{min(rets):.1f}% 最大赚{max(rets):.1f}%")
    print(f"  离场方式: {reasons}")
    return rets

r1 = stat("方案1(全清)", results[1])
r2 = stat("方案2(清弱势)", results[2])
