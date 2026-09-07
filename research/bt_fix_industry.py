# -*- coding: utf-8 -*-
"""用bt_0815_data.json的300天K线合成行业指数(池内行业市值Top15等权),
替换原130天行业数据, 使2/15等早期时点行业动量可算"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data = json.load(open(os.path.join(BASE, "research", "bt_0815_data.json"), encoding="utf-8"))
klines = data["klines"]; pool = data["pool"]

# 池内行业市值Top15
ind_members = {}
for r in pool:
    ind_members.setdefault(r["ind"], []).append(r)

ind_cache = {}
for ind, ms in ind_members.items():
    ms = sorted(ms, key=lambda r: -r["mv"])[:15]
    series = {}
    ok = 0
    for m in ms:
        rows = klines.get(m["code"])
        if not rows:
            continue
        for row in rows:
            series.setdefault(str(row["date"]), []).append(float(row["close"]))
        ok += 1
    if ok < 5:
        continue
    rows = [{"date": d, "close": round(sum(v) / len(v), 4)}
            for d, v in series.items() if len(v) >= 5]
    rows.sort(key=lambda x: x["date"])
    if len(rows) >= 60:
        ind_cache[ind] = rows

print(f"合成行业指数: {len(ind_cache)}个")
# 验证范围
sample = list(ind_cache.keys())[:3]
for k in sample:
    v = ind_cache[k]
    print(f"  {k}: {len(v)}行 {v[0]['date']} ~ {v[-1]['date']}")

data["industry"] = ind_cache
out = os.path.join(BASE, "research", "bt_0815_data.json")
json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False)
print(f"已更新 {out}, {os.path.getsize(out)/1024/1024:.1f}MB")
