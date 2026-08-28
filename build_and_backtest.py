# -*- coding: utf-8 -*-
"""构建更贴合形态研究的股票池并回测对比
池子（均从 ≥100亿 池出发）：
  P500     市值≥500亿
  P1000    市值≥1000亿
  PLOWIND  低波动行业(银行/电力/煤炭/白酒/铁路公路/航运/保险/石油/家电/公用事业等)
  VOL 波动率分组：随机抽80只算年化波动率 → 最低20 vs 最高20
国企标注：对波动率分组40只拉实控人，判断国企占比
回测：新浪日线1000根，与全A抽样同一套算法
"""
import urllib.request, json, random, os, sys, time
import pandas as pd
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_fetcher import _kline_from_sina
from backtest import backtest_rows, _rows_from_df, pct

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bt_data")
random.seed(42)

LOW_IND = ["银行Ⅱ", "电力", "煤炭开采", "白酒Ⅱ", "铁路公路", "航运港口",
           "保险", "石油", "家用电器", "白色家电", "燃气", "公用事业",
           "环境治理", "物流", "港口", "高速公路", "机场", "交通设施",
           "饮料乳品", "食品加工", "贵金属", "石油加工", "炼化及贸易"]

def get_soe(code, pre):
    """拉实控人，返回 True=国企 / False=非国企 / None=无法判定"""
    url = f"https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={pre}{code}"
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                       "Referer": "https://emweb.securities.eastmoney.com/"})
            d = json.load(urllib.request.urlopen(req, timeout=10))
            holders = d.get("sjkzr") or []
            if holders:
                h = (holders[0].get("HOLDER_NAME") or "")
                if any(k in h for k in ["国有资产", "国资委", "国务院", "国有"]):
                    return True
                if h:  # 有人名则非国企（个人实控）
                    return False
            return None
        except Exception:
            time.sleep(0.8)
    return None

def pre_of(code):
    return "sh" if code.startswith(("6", "9")) else "sz"

def annual_vol(code):
    df = _kline_from_sina(code, "daily", 260)
    if df.empty or len(df) < 60:
        return None
    c = df["close"].astype(float).values
    r = np.diff(c) / c[:-1]
    return float(np.std(r) * np.sqrt(250))

def main():
    pool = json.load(open(os.path.join(DATA, "pool_100e.json"), encoding="utf-8"))
    print("≥100亿池:", len(pool))

    # 1) 各池子
    p500 = random.sample([x for x in pool if x["mv"] >= 5e10], 50)
    p1000 = random.sample([x for x in pool if x["mv"] >= 1e11], 50)
    lowind_pool = [x for x in pool if x["ind"] in LOW_IND]
    print("低波动行业池:", len(lowind_pool))
    p_lowind = random.sample(lowind_pool, min(50, len(lowind_pool)))

    # 2) 波动率分组：随机抽80算波动率
    vol_cand = random.sample(pool, 80)
    volrows = []
    for x in vol_cand:
        v = annual_vol(x["code"])
        if v: volrows.append({**x, "vol": v})
    volrows.sort(key=lambda x: x["vol"])
    low20 = volrows[:20]
    high20 = volrows[-20:]
    print("波动率区间: 低20组", round(min(x['vol'] for x in low20), 3), "~", round(max(x['vol'] for x in low20), 3),
          " 高20组", round(min(x['vol'] for x in high20), 3), "~", round(max(x['vol'] for x in high20), 3))

    # 3) 国企标注（波动率两组40只）
    for grp, tag in [(low20, "low"), (high20, "high")]:
        soe = 0; cnt = 0
        for x in grp:
            s = get_soe(x["code"], pre_of(x["code"]))
            x["soe"] = s
            if s is not None: cnt += 1
            if s: soe += 1
        print(f"  波动率[{tag}] 国企占比: {soe}/{len(grp)} (可判定{cnt})")
        time.sleep(0.2)

    groups = {
        "P500(市值≥500亿)": p500,
        "P1000(市值≥1000亿)": p1000,
        "PLOWIND(低波动行业)": p_lowind,
        "VOL低(波动最小20只)": low20,
        "VOL高(波动最大20只)": high20,
    }
    json.dump({k: [{"code": x["code"], "name": x["name"], "mv": x.get("mv"), "ind": x.get("ind"),
                    "vol": x.get("vol"), "soe": x.get("soe")} for x in v]
               for k, v in groups.items()},
              open(os.path.join(DATA, "groups.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 4) 回测汇总
    summary = {}
    for gname, g in groups.items():
        agg = {}
        for x in g:
            df = _kline_from_sina(x["code"], "daily", 1000)
            if df.empty or len(df) < 150:
                print(f"  {gname} {x['code']} {x['name']} 数据不足，跳过"); continue
            r = backtest_rows(_rows_from_df(df))
            for k in r:
                agg.setdefault(k, {"t": 0, "hit": 0})
                agg[k]["t"] += r[k]["t"]; agg[k]["hit"] += r[k]["hit"]
        summary[gname] = agg
        pp = {k: pct(agg.get(k, {"t": 0, "hit": 0})) for k in ["trend", "sr_support", "sr_resist", "div", "pat", "pat_bear", "pat_bull"]}
        print(f"\n===== {gname} ({len(g)}只) =====")
        for k, v in pp.items():
            print(f"  {k:12s}: {v[0]}%  ({v[1]}/{v[2]})")
    json.dump({k: {kk: vv for kk, vv in v.items()} for k, v in summary.items()},
              open(os.path.join(DATA, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()
