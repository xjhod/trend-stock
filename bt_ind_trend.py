"""
行业趋势判断标准回测：比较不同标准下行业内个股后续上涨率
A: 周线多头排列（周MA5>10>20 且 收盘>周MA10）
B: 日线趋势向上（收盘>MA20 且 MA20上行 且 MA5>MA10）
C: A + B 结合
"""
import json, os
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))

# 加载行业指数缓存
with open(os.path.join(BASE, "bt_data", "ind_idx_cache.json"), encoding="utf-8") as f:
    ind_cache = json.load(f)

# 加载高适配池（带行业分类）
with open(os.path.join(BASE, "highfit_pool.json"), encoding="utf-8") as f:
    pool = json.load(f)

# 股票代码 -> 行业映射
code2ind = {}
for s in pool:
    code2ind[s.get("code")] = s.get("ind", "")

print(f"行业指数数: {len(ind_cache)}")
print(f"高适配股票数: {len(code2ind)}")

# 加载个股K线数据（用已有的回测数据缓存）
# 先检查有没有全量K线缓存
kline_cache_file = os.path.join(BASE, "bt_data", "all_kline_cache.json")
if os.path.exists(kline_cache_file):
    with open(kline_cache_file, encoding="utf-8") as f:
        stock_klines = json.load(f)
    print(f"加载个股K线缓存: {len(stock_klines)} 只")
else:
    print("未找到全量K线缓存，需要先构建")
    stock_klines = {}

def ma(arr, n):
    """计算移动平均线"""
    if len(arr) < n:
        return None
    return sum(arr[-n:]) / n

def trend_A_weekly(weekly_rows):
    """A: 周线多头排列"""
    if len(weekly_rows) < 25:
        return False
    closes = [r["close"] for r in weekly_rows]
    ma5 = ma(closes, 5)
    ma10 = ma(closes, 10)
    ma20 = ma(closes, 20)
    if ma5 is None or ma10 is None or ma20 is None:
        return False
    return ma5 > ma10 > ma20 and closes[-1] > ma10

def trend_B_daily(daily_rows):
    """B: 日线趋势向上"""
    if len(daily_rows) < 25:
        return False
    closes = [r["close"] for r in daily_rows]
    ma5 = ma(closes, 5)
    ma10 = ma(closes, 10)
    ma20 = ma(closes, 20)
    ma20_prev = ma(closes[:-1], 20) if len(closes) > 20 else None
    if ma5 is None or ma10 is None or ma20 is None or ma20_prev is None:
        return False
    return closes[-1] > ma20 and ma20 > ma20_prev and ma5 > ma10

def to_weekly(daily_rows):
    """日线转周线（每5个交易日取最后一个收盘价）"""
    if not daily_rows:
        return []
    weekly = []
    for i in range(0, len(daily_rows), 5):
        chunk = daily_rows[i:i+5]
        if chunk:
            weekly.append({"date": chunk[-1]["date"], "close": chunk[-1]["close"]})
    return weekly

# 回测：取多个历史时点，统计各标准下行业内个股后续上涨率
# 用行业指数的日线数据来判断行业趋势
test_dates = ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
horizons = [5, 10, 20]

results = {
    "A": defaultdict(lambda: {"up": 0, "total": 0}),
    "B": defaultdict(lambda: {"up": 0, "total": 0}),
    "C": defaultdict(lambda: {"up": 0, "total": 0}),
    "all": defaultdict(lambda: {"up": 0, "total": 0}),
}

ind_stocks = defaultdict(list)
for code, ind in code2ind.items():
    ind_stocks[ind].append(code)

# 为了简化，用行业指数本身的后续涨跌来代表行业内个股的平均表现
# （行业指数涨跌和行业内个股涨跌高度相关）
for ind_name, rows in ind_cache.items():
    if len(rows) < 60:
        continue
    for test_date in test_dates:
        # 找到test_date对应的索引
        idx = None
        for i, r in enumerate(rows):
            if r.get("date", "") >= test_date:
                idx = i
                break
        if idx is None or idx < 25:
            continue
        if idx + 20 >= len(rows):
            continue

        daily_slice = rows[:idx+1]
        weekly_slice = to_weekly(daily_slice)

        a = trend_A_weekly(weekly_slice)
        b = trend_B_daily(daily_slice)
        c = a and b

        # 后续涨跌
        for h in horizons:
            if idx + h >= len(rows):
                continue
            future_ret = rows[idx+h]["close"] / rows[idx]["close"] - 1
            up = future_ret > 0

            results["all"][h]["total"] += 1
            results["all"][h]["up"] += int(up)

            if a:
                results["A"][h]["total"] += 1
                results["A"][h]["up"] += int(up)
            if b:
                results["B"][h]["total"] += 1
                results["B"][h]["up"] += int(up)
            if c:
                results["C"][h]["total"] += 1
                results["C"][h]["up"] += int(up)

print("\n===== 行业趋势判断标准回测结果 =====")
print(f"{'标准':<8} {'5日上涨率':<12} {'10日上涨率':<12} {'20日上涨率':<12} {'样本数(20日)':<10}")
print("-" * 60)
for std in ["all", "A", "B", "C"]:
    name = {"all": "全部基准", "A": "A周线多头", "B": "B日线趋势", "C": "C两者结合"}[std]
    rates = []
    for h in horizons:
        r = results[std][h]
        rate = r["up"] / r["total"] * 100 if r["total"] > 0 else 0
        rates.append(f"{rate:.1f}%")
    sample_20 = results[std][20]["total"]
    print(f"{name:<8} {rates[0]:<12} {rates[1]:<12} {rates[2]:<12} {sample_20:<10}")

# 额外：计算平均涨幅
print("\n===== 平均涨幅 =====")
ret_results = {std: defaultdict(list) for std in ["all", "A", "B", "C"]}
for ind_name, rows in ind_cache.items():
    if len(rows) < 60:
        continue
    for test_date in test_dates:
        idx = None
        for i, r in enumerate(rows):
            if r.get("date", "") >= test_date:
                idx = i
                break
        if idx is None or idx < 25 or idx + 20 >= len(rows):
            continue
        daily_slice = rows[:idx+1]
        weekly_slice = to_weekly(daily_slice)
        a = trend_A_weekly(weekly_slice)
        b = trend_B_daily(daily_slice)
        c = a and b
        for h in horizons:
            if idx + h >= len(rows):
                continue
            ret = (rows[idx+h]["close"] / rows[idx]["close"] - 1) * 100
            ret_results["all"][h].append(ret)
            if a: ret_results["A"][h].append(ret)
            if b: ret_results["B"][h].append(ret)
            if c: ret_results["C"][h].append(ret)

print(f"{'标准':<8} {'5日平均':<12} {'10日平均':<12} {'20日平均':<12}")
print("-" * 50)
for std in ["all", "A", "B", "C"]:
    name = {"all": "全部基准", "A": "A周线多头", "B": "B日线趋势", "C": "C两者结合"}[std]
    avgs = []
    for h in horizons:
        rets = ret_results[std][h]
        avg = sum(rets) / len(rets) if rets else 0
        avgs.append(f"{avg:+.2f}%")
    print(f"{name:<8} {avgs[0]:<12} {avgs[1]:<12} {avgs[2]:<12}")
