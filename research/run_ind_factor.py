import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
import bt_historical
from concurrent.futures import ThreadPoolExecutor, as_completed
from data_fetcher import get_kline

def run(asof, buy):
    recs = bt_historical.scan_asof(asof=asof)
    tr=[r for r in recs if r["type"]=="trend"]
    rb=[r for r in recs if r["type"]=="rebound"]
    print(f"=== {asof} 扫描（含行业因子）===")
    print(f"推荐 {len(recs)}（趋势{len(tr)}/抄底{len(rb)}）")
    for r in tr:
        print(f"  趋势: {r['name']} {r['ind']} level{r['level']} 价{r.get('asof_price')}")
    hold = bt_historical.simulate(recs, use_rules=False, asof=asof, buy_day=buy)
    for t,tl in [("trend","趋势"),("rebound","抄底")]:
        g=[x for x in hold if x["type"]==t]
        rets=[x["ret_pct"] for x in g]
        if g:
            w=sum(1 for r in rets if r>0)
            print(f"  {tl} 持有至今: {len(g)}只 胜率{w/len(g)*100:.0f}% 均{sum(rets)/len(g):.2f}%")
            for x in sorted(g,key=lambda z:-z['ret_pct']):
                print(f"      {x['name']:8s} {x['ret_pct']:>7}% ({x['hold_days']}日)")
    print()
    return recs

run("2026-06-22","2026-06-23")
run("2026-05-20","2026-05-21")
run("2026-06-30","2026-07-01")
print("ALL DONE")
