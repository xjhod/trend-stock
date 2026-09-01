# -*- coding: utf-8 -*-
"""高适配池随机 50 只批量形态可靠性测试
对每只股票复用 bt.pattern_test_rows（与软件内'形态测试'完全一致的口径：
趋势内 + 量能确认），统计每个形态出现后 3/6/10 日的胜率与平均收益，
跨股票汇总，评估各形态在"高适配股票"上的整体可靠性。
"""
import json
import random
import sys
import time
import pandas as pd

import backtest as bt
import data_fetcher as df

HORIZONS = (3, 6, 10)
SEED = 20260831
LIMIT = 1200


def fetch(code):
    """拉取日线，失败返回空 DataFrame"""
    try:
        d = df.get_kline(code, period="daily", limit=LIMIT)
        if d is not None and len(d) >= 200:
            return d
    except Exception as e:
        print(f"  [!] {code} 拉取失败: {e}")
    return pd.DataFrame()


def main():
    pool = json.load(open("highfit_pool.json", encoding="utf-8"))
    if not isinstance(pool, list):
        pool = list(pool.values()) if isinstance(pool, dict) else []
    codes = [p["code"] if isinstance(p, dict) else str(p) for p in pool]
    names = {p["code"]: p.get("name", "") for p in pool if isinstance(p, dict)}
    # 支持命令行参数指定抽样数量，默认 50
    n_sample = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    random.seed(SEED)
    sample = random.sample(codes, n_sample)
    print(f"高适配池共 {len(codes)} 只，随机抽取 {n_sample} 只（seed={SEED}）")

    # agg[name][H] = {hit,total,rets}
    agg = {}
    per_stock = {}
    ok_n = 0
    for code in sample:
        d = fetch(code)
        if d.empty:
            per_stock[code] = None
            print(f"  {code} {names.get(code,'')}: 数据不足，跳过")
            continue
        rows = bt._rows_from_df(d)
        res = bt.pattern_test_rows(rows, horizons=HORIZONS)
        n_pat = sum(p["count"] for p in res["patterns"])
        per_stock[code] = {"name": names.get(code, ""), "n": len(rows), "patterns": n_pat,
                           "range": f"{rows[0]['date']}~{rows[-1]['date']}"}
        ok_n += 1
        for p in res["patterns"]:
            for H in HORIZONS:
                b = p["by_day"][str(H)]
                key = (p["name"], p["dir"], H)
                a = agg.setdefault(key, {"hit": 0, "total": 0, "rets": []})
                a["hit"] += b["hit"]
                a["total"] += b["total"]
                if b["avg_ret"] is not None:
                    a["rets"].append(b["avg_ret"] * b["total"])  # 加权便于重新平均
        print(f"  ✓ {code} {names.get(code,'')}: {n_pat}个形态样本")
        time.sleep(0.3)  # 避免请求过快

    print(f"\n成功 {ok_n}/50 只")

    # 保存逐股明细
    with open(f"bt_data/pattern_test_{n_sample}.json", "w", encoding="utf-8") as f:
        json.dump({"seed": SEED, "sample": [{"code": c, **per_stock[c]} for c in sample if per_stock[c]],
                   "ok": ok_n}, f, ensure_ascii=False, indent=2)

    # 汇总：按形态聚合
    print("\n" + "=" * 78)
    print(f"{'形态':<8}{'方向':<4}{'样本数':<6}{'3日胜率':<9}{'3日均收':<9}{'6日胜率':<9}{'6日均收':<9}{'10日胜率':<9}{'10日均收':<9}")
    print("=" * 78)
    # 按方向汇总
    for dirlab, dirv in (("看涨", 1), ("看跌", -1)):
        h3 = agg.get(("__all__", dirv, 3))
    # 先打印总方向汇总
    def show_summary(label, key):
        print(f"\n--- {label}形态（全部） ---")
        for H in HORIZONS:
            a = agg.get((label, "all", H), {"hit": 0, "total": 0, "rets": []})
            tot = sum(v["total"] for k, v in agg.items() if k[1] == "all" and k[2] == H)
            # 兜底：无 all 键时从具体形态聚合
            if a["total"] == 0:
                for k, v in agg.items():
                    if k[1] == label and k[2] == H:
                        a["hit"] += v["hit"]; a["total"] += v["total"]; a["rets"].extend([v["rets"]])
            print(f"  {H}日: {a['hit']}/{a['total']} = {a['hit']/a['total']*100 if a['total'] else 0:.1f}%")
    # 直接用具体形态聚合成方向汇总
    for dirlab in ("看涨", "看跌"):
        tot_hit = {H: 0 for H in HORIZONS}
        tot_n = {H: 0 for H in HORIZONS}
        sum_ret = {H: 0.0 for H in HORIZONS}
        for (name, d, H), a in agg.items():
            if d != dirlab:
                continue
            tot_hit[H] += a["hit"]; tot_n[H] += a["total"]
            if a["rets"]:
                sum_ret[H] += sum(a["rets"])
        if tot_n[3] == 0:
            continue
        print(f"\n◆ {dirlab}形态 合计样本: {tot_n[3]}")
        for H in HORIZONS:
            rate = tot_hit[H] / tot_n[H] * 100 if tot_n[H] else 0
            avg = sum_ret[H] / tot_n[H] if tot_n[H] else 0
            print(f"   {H}日: 胜率 {rate:.1f}%  ({tot_hit[H]}/{tot_n[H]})   平均收益 {avg:+.2f}%")
    print("\n" + "=" * 78)

    # 按具体形态聚合
    by_name = {}
    for (name, d, H), a in agg.items():
        b = by_name.setdefault(name, {"dir": d, "hit": {h: 0 for h in HORIZONS},
                                     "total": {h: 0 for h in HORIZONS},
                                     "ret": {h: 0.0 for h in HORIZONS}})
        b["hit"][H] += a["hit"]; b["total"][H] += a["total"]
        if a["rets"]:
            b["ret"][H] += sum(a["rets"])
    print(f"\n{'形态':<8}{'方向':<4}{'总样本':<7}{'3日':<14}{'6日':<14}{'10日':<14}")
    for name in sorted(by_name, key=lambda n: -sum(by_name[n]["total"].values())):
        b = by_name[name]
        line = f"{name:<8}{b['dir']:<4}{sum(b['total'].values()):<7}"
        for H in HORIZONS:
            t = b["total"][H]
            if not t:
                line += f"{'-':<14}"
            else:
                r = b["hit"][H] / t * 100
                avg = b["ret"][H] / t
                line += f"{r:.0f}% {avg:+.2f}%".ljust(14)
        print(line)
    print("=" * 78)


if __name__ == "__main__":
    main()
