# -*- coding: utf-8 -*-
"""数据源诊断：测东财/腾讯/新浪 K 线连通性 + 自动探测顺序。
修复了腾讯参数逗号bug后，请重新运行本脚本确认腾讯是否可用。
运行：python diag_605167.py"""
import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import data_fetcher as df

code = "605167"
print("=" * 56)
print("数据源诊断（修复腾讯参数bug后的版本）")
print("=" * 56)

# 1. 自动探测顺序
print("\n【1】数据源自动探测顺序:", df.source_order())
print("   (越靠前优先级越高, 探测结果当天缓存)")

# 2. 各源 K 线行数（清缓存强制重拉）
print("\n【2】各数据源 K 线连通性 (605167 日线)")
klt = 101
params_base = {
    "fields1":"f1,f2,f3,f4,f5,f6",
    "fields2":"f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    "ut":"7eea3edcaed734bea9cbfc24409ed989",
    "beg":"20220101","end":"20500101"}
df._CACHE.clear()
for name, fn in [
    ("东财", lambda: df._kline_from_eastmoney(code, klt, 1, params_base, 300)),
    ("腾讯(复权)", lambda: df._kline_from_tencent(code, "daily", 300, "qfq")),
    ("新浪", lambda: df._kline_from_sina(code, "daily", 300)),
]:
    t0 = time.time()
    try:
        d = fn()
        el = time.time() - t0
        print(f"   {name}: {'✓ 可用' if len(d) else '✗ 空'}  {len(d)}行  ({el:.1f}s)"
              + (f"  最新收盘 {d.iloc[-1]['close']}" if len(d) else ""))
    except Exception as e:
        print(f"   {name}: 异常 {e}")

# 3. 综合 get_kline
print("\n【3】综合 get_kline (qfq 300根):")
t0 = time.time()
d = df.get_kline(code, "daily", 300, "qfq")
print(f"   行数 {len(d)}, 耗时 {time.time()-t0:.1f}s, 最新收盘 {d.iloc[-1]['close'] if len(d) else '-'}")

print("\n" + "=" * 56)
print("结论：探测顺序里有 tencent 且【2】腾讯✓可用 → 程序会自动用腾讯复权数据(精度更好)")
print("若只有 sina → 你的网络屏蔽了腾讯K线域名, 继续用新浪(不复权)")
print("=" * 56)
