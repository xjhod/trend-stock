# -*- coding: utf-8 -*-
"""诊断：605167(利柏特) 日线数据源。运行：python diag_605167.py"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import data_fetcher as df

code = "605167"
print("=" * 50)
print("诊断 605167 数据源连通性")
print("=" * 50)

for period, limit in [("daily", 300), ("weekly", 120), ("monthly", 80)]:
    # 清缓存强制重拉
    ck = f"kline:{code}:{period}:{limit}:qfq"
    df._CACHE.pop(ck, None)
    klt = {"daily":101,"weekly":102,"monthly":103}[period]
    print(f"\n--- {period} (limit={limit}) ---")
    # 东财
    try:
        d = df._kline_from_eastmoney(code, klt, 1, {
            "fields1":"f1,f2,f3,f4,f5,f6",
            "fields2":"f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut":"7eea3edcaed734bea9cbfc24409ed989",
            "beg":"20220101","end":"20500101"}, limit)
        print(f"东财: {'OK' if len(d) else '空'}  {len(d)}行")
    except Exception as e:
        print(f"东财: 异常 {e}")
    # 新浪
    try:
        d2 = df._kline_from_sina(code, period, limit)
        print(f"新浪: {'OK' if len(d2) else '空'}  {len(d2)}行")
    except Exception as e:
        print(f"新浪: 异常 {e}")
    # 腾讯
    try:
        d3 = df._kline_from_tencent(code, period, limit, "qfq")
        print(f"腾讯: {'OK' if len(d3) else '空'}  {len(d3)}行")
    except Exception as e:
        print(f"腾讯: 异常 {e}")

print("\n" + "=" * 50)
print("综合 get_kline 结果:")
for period, limit in [("daily",300),("weekly",120),("monthly",80)]:
    ck = f"kline:{code}:{period}:{limit}:qfq"
    df._CACHE.pop(ck, None)
    d = df.get_kline(code, period, limit, "qfq")
    print(f"  {period}: {len(d) if d is not None else 'None'} 行")
print("=" * 50)
print("如果 daily 三个源都空/异常，而 weekly/monthly 正常，")
print("说明该股票日线在你这网络下数据源异常。请把输出截图发我。")
