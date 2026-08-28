# -*- coding: utf-8 -*-
"""市值≥100亿股票池随机抽样回测
数据源：东财（与软件内一致，前复权）。每只取约4年日线，
测试点只使用当时可见数据，无未来函数。
"""
import urllib.request, json, random, time, os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_fetcher import _kline_from_sina
from backtest import backtest_rows, _rows_from_df, pct

OUT = "/tmp/bt100e"
os.makedirs(OUT, exist_ok=True)
random.seed(42)

def fetch_all_a():
    out = []
    pn = 1
    total = None
    while True:
        url = (f"https://push2delay.eastmoney.com/api/qt/clist/get?pn={pn}&pz=200&po=1&np=1&fltt=2&invt=2&fid=f20"
               "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f20")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
        try:
            d = json.load(urllib.request.urlopen(req, timeout=20))
        except Exception as e:
            print("分页请求失败", pn, e)
            time.sleep(1); continue
        diff = d.get("data", {}).get("diff")
        if isinstance(diff, dict):
            diff = [diff]
        if not diff:
            break
        for it in diff:
            mv_raw = it.get("f20")
            try:
                mv = float(mv_raw)
            except (TypeError, ValueError):
                mv = 0.0
            out.append({"code": str(it["f12"]), "name": it["f14"], "mv": mv})
        if total is None:
            total = d.get("data", {}).get("total", 0)
        print(f"  已拉取 {len(out)}/{total}")
        if len(out) >= total:
            break
        pn += 1
        time.sleep(0.3)
    return out

def main():
    all_list = fetch_all_a()
    print("全A总数:", len(all_list))
    pool = [x for x in all_list if x["mv"] and x["mv"] >= 1e10 and "ST" not in x["name"].upper()]
    print("市值≥100亿且非ST:", len(pool))
    with open(os.path.join(OUT, "pool.json"), "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False)
    sample = random.sample(pool, 50)
    with open(os.path.join(OUT, "sample.json"), "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=1)
    print("抽样50只:", [x["code"] for x in sample])

    agg = {}
    ok, fail = 0, []
    for i, it in enumerate(sample):
        code = it["code"]; name = it["name"]
        try:
            df = _kline_from_sina(code, "daily", 1000)
            if df.empty or len(df) < 150:
                fail.append(code); print(f"[{i+1}/50] {code} {name} 数据不足({0 if df.empty else len(df)})，跳过")
                continue
            df = df.reset_index(drop=True)
            df.to_csv(os.path.join(OUT, f"bt_{code}.csv"), index=False)
            r = backtest_rows(_rows_from_df(df))
            ok += 1
            for k in r:
                agg.setdefault(k, {"t": 0, "hit": 0})
                agg[k]["t"] += r[k]["t"]; agg[k]["hit"] += r[k]["hit"]
            pp = {k: pct(r[k]) for k in r}
            print(f"[{i+1}/50] {code} {name} {len(df)}根 趋势{pp['trend'][0]}% 支撑{pp['sr_support'][0]}% "
                  f"阻力{pp['sr_resist'][0]}% 形态{pp['pat'][0]}%")
        except Exception as e:
            fail.append(code); print(f"[{i+1}/50] {code} {name} 异常: {e}")
        time.sleep(0.4)
    print("\n========== 市值≥100亿 随机50只汇总 ==========")
    print(f"成功: {ok}  失败: {len(fail)} {fail}")
    for k in ["trend", "sr_support", "sr_resist", "div", "pat", "pat_bear", "pat_bull"]:
        pp, h, t = pct(agg.get(k, {"t": 0, "hit": 0}))
        print(f"  {k:12s}: {pp}%  ({h}/{t})")
    with open(os.path.join(OUT, "agg.json"), "w", encoding="utf-8") as f:
        json.dump({k: agg[k] for k in agg}, f, ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
