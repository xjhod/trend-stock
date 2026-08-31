import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
import bt_historical, scan_daily
from data_fetcher import get_kline

def grade_rec(rec, asof):
    """对推荐股重算形态质量"""
    code = rec["code"]
    df = get_kline(code, "daily", 400, "qfq")
    if df is None or len(df) < 60: return None
    df = df[df["date"] <= asof]
    if len(df) < 60: return None
    pats = scan_daily.detect_bullish(df)
    if not pats:
        # 趋势股可能无看涨形态, 用最后形态质量=中等
        return {"code": code, "name": rec.get("name",""), "type": rec["type"], "grade": None, "score": 0}
    name, i, vc = pats[0]
    sc, lv, rs = scan_daily._pattern_quality(df, i, name, vc)
    return {"code": code, "name": rec.get("name",""), "type": rec["type"], "grade": lv, "score": sc, "pat": name}

# 多个历史时点扫描（门卫会拦顶部）
ASOFS = [("2026-06-30","2026-07-01"), ("2026-06-22","2026-06-23"), ("2026-05-13","2026-05-14"),
         ("2026-05-08","2026-05-11"), ("2026-06-18","2026-06-19"), ("2026-06-12","2026-06-15")]
rows = []
for asof, buy in ASOFS:
    recs = bt_historical.scan_asof(asof=asof)
    if not recs: 
        print(f"{asof}: 0推荐(门卫拦截)"); continue
    hold = bt_historical.simulate(recs, use_rules=False, asof=asof, buy_day=buy)
    hmap = {x["code"]: x for x in hold}
    for r in recs:
        g = grade_rec(r, asof)
        if g is None: continue
        h = hmap.get(r["code"])
        ret = h["ret_pct"] if h else None
        rows.append({**g, "asof": asof, "ret": ret})
    print(f"{asof}: {len(recs)}推荐")

print(f"\n共收集 {len(rows)} 条(有收益 {sum(1 for r in rows if r['ret'] is not None)})")
# 按形态质量分级统计
import statistics
for typ in ["rebound", "trend"]:
    print(f"\n=== {typ} 形态质量分级 ===")
    g2 = [r for r in rows if r["type"]==typ and r["grade"] and r["ret"] is not None]
    for lv in ["strong", "medium", "weak"]:
        gg = [r for r in g2 if r["grade"]==lv]
        rets = [r["ret"] for r in gg]
        if len(rets) >= 3:
            w = sum(1 for x in rets if x>0)
            print(f"  {lv:7s}: {len(gg)}只 胜率{w/len(rets)*100:.0f}% 均{sum(rets)/len(rets):.2f}%")
        elif rets:
            print(f"  {lv:7s}: {len(gg)}只(样本少) 各{rets}")
# 高分(score>=3) vs 低分
print("\n=== 按评分(不分类型) ===")
allg = [r for r in rows if r["ret"] is not None]
for label, cond in [("score>=3", lambda r:r["score"]>=3), ("score>=2", lambda r:r["score"]>=2), ("score<1.5", lambda r:r["score"]<1.5)]:
    gg = [r for r in allg if cond(r)]
    rets=[r["ret"] for r in gg]
    if len(rets)>=3:
        w=sum(1 for x in rets if x>0)
        print(f"  {label:10s}: {len(gg)}只 胜率{w/len(rets)*100:.0f}% 均{sum(rets)/len(rets):.2f}%")
