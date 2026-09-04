# -*- coding: utf-8 -*-
"""趋势全景 · K线数据源诊断脚本
用法：在 stock-analysis 文件夹里运行  python test_kline.py
输出每个数据源的连通性，帮助定位 K 线加载失败的原因。
"""
import sys
import time
import datetime
import requests

print("=" * 55)
print("  趋势全景 · K线数据源诊断")
print("  " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
print("=" * 55)

# 1. 检查 data_fetcher.py 是否是新版（含腾讯回退）
import os
df_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_fetcher.py")
try:
    with open(df_path, encoding="utf-8") as f:
        c = f.read()
    has_tencent = "_kline_from_tencent" in c
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(df_path)).strftime("%Y-%m-%d %H:%M")
    print(f"\n[1] data_fetcher.py 修改时间: {mtime}")
    print(f"    是否包含腾讯K线回退: {'是（v1.8.5新版）' if has_tencent else '否（旧版，需更新！）'}")
    if not has_tencent:
        print("    >>> 请先更新 data_fetcher.py 到 v1.8.5，否则不会使用腾讯数据源")
except Exception as e:
    print(f"\n[1] 读取 data_fetcher.py 失败: {e}")

# 2. 测试各数据源
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
TX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://gu.qq.com/",
}

def test(name, url, params, headers, need_key=None):
    try:
        r = requests.get(url, params=params, headers=headers, timeout=5)
        if r.status_code != 200:
            print(f"  {name}: HTTP {r.status_code} ❌")
            return False
        txt = r.text
        if need_key and need_key not in txt:
            print(f"  {name}: 返回但无数据（可能被限流）⚠️")
            return False
        print(f"  {name}: 正常 ✅")
        return True
    except Exception as e:
        print(f"  {name}: 失败 ❌ ({type(e).__name__}: {str(e)[:60]})")
        return False

print("\n[2] 各数据源连通性测试（平安银行 000001）：")
ok = []
ok.append(test("东方财富K线", "https://push2his.eastmoney.com/api/qt/stock/kline/get",
              {"secid": "0.000001", "fields1": "f1,f2,f3,f4,f5,f6",
               "fields2": "f51,f52,f53,f54,f55,f56,f57", "klt": "101", "fqt": "1", "lmt": "5"},
              HEADERS, need_key="klines"))
time.sleep(1)
ok.append(test("新浪K线", "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
              {"symbol": "sz000001", "scale": "240", "ma": "no", "datalen": "5"},
              HEADERS, need_key="day"))
time.sleep(1)
ok.append(test("腾讯K线(web)", "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
              {"param": "sz000001,day,,,5,qfq"}, TX_HEADERS, need_key="qfqday"))
time.sleep(1)
ok.append(test("腾讯K线(ifzq)", "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
              {"param": "sz000001,day,,,5,qfq"}, TX_HEADERS, need_key="qfqday"))
time.sleep(1)
ok.append(test("腾讯K线(proxy)", "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
              {"param": "sz000001,day,,,5,qfq"}, TX_HEADERS, need_key="qfqday"))
time.sleep(1)
ok.append(test("腾讯行情(自选股用)", "https://qt.gtimg.cn/q=sz000001",
              {}, TX_HEADERS, need_key="000001"))

print("\n[3] 结论：")
print(f"  可用数据源数: {sum(ok)} / 6")
if sum(ok) == 0:
    print("  >>> 所有数据源都不通，可能是办公网络/防火墙屏蔽了行情接口，或需要代理")
elif sum(ok) >= 1:
    print("  >>> 有可用数据源，程序应该能正常加载K线")
    print("      如果程序仍显示加载失败，说明 data_fetcher.py 不是最新版，请更新到 v1.8.5")
print("\n  请把本窗口的完整输出复制给我，我来帮你定位。")
print("=" * 55)
