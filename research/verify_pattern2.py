import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
import bt_historical

# 更多历史时点（门卫拦截顶部的）
ASOFS = [("2026-06-30","2026-07-01"), ("2026-06-22","2026-06-23"), ("2026-06-18","2026-06-19"),
         ("2026-06-12","2026-06-15"), ("2026-06-09","2026-06-10"), ("2026-06-05","2026-06-08"),
         ("2026-05-13","2026-05-14"), ("2026-05-08","2026-05-11"), ("2026-05-29","2026-06-01"),
         ("2026-05-25","2026-05-26")]
all_recs = {}
for asof, buy in ASOFS:
    recs = bt_historical.scan_asof(asof=asof)
    if not recs:
        print(f"{asof}: 0推荐(门卫/过滤)"); continue
    hold = bt_historical.simulate(recs, use_rules=False, asof=asof, buy_day=buy)
    hmap = {x["code"]: x for x in hold}
    n = {"trend":0, "rebound":0}
    for r in recs:
        n[r["type"]] += 1
        h = hmap.get(r["code"])
        r["ret"] = h["ret_pct"] if h else None
        r["asof"] = asof
    all_recs[asof] = recs
    print(f"{asof}: 趋势{n['trend']} 超跌{n['rebound']}")

# 汇总超跌/趋势胜率
for typ, tl in [("rebound","超跌企稳"), ("trend","趋势")]:
    rows = [r for rs in all_recs.values() for r in rs if r["type"]==typ and r["ret"] is not None]
    if rows:
        rets = [r["ret"] for r in rows]
        w = sum(1 for x in rets if x>0)
        print(f"\n{tl}: {len(rows)}只(10时点) 胜率{w/len(rets)*100:.0f}% 均{sum(rets)/len(rets):.2f}%")
        # 按日看
        byday = {}
        for r in rows:
            byday.setdefault(r["asof"], []).append(r["ret"])
        for d in sorted(byday):
            rr = byday[d]
            ww = sum(1 for x in rr if x>0)
            print(f"  {d}: {len(rr)}只 胜率{ww/len(rr)*100:.0f}% 均{sum(rr)/len(rr):.2f}%")
# 超跌最佳/最差
rb = [r for rs in all_recs.values() for r in rs if r["type"]=="rebound" and r["ret"] is not None]
if rb:
    rb.sort(key=lambda x:-x["ret"])
    print("\n超跌最佳5:", [(x.get("name"), x["ret"]) for x in rb[:5]])
    print("超跌最差5:", [(x.get("name"), x["ret"]) for x in rb[-5:]])
