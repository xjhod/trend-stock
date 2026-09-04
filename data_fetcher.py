# -*- coding: utf-8 -*-
"""
数据获取模块：直接封装腾讯/东方财富公开接口
不依赖 akshare，轻量稳定
"""
import time
import json
import os
import requests
import pandas as pd

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
TIMEOUT = 20
RETRY = 6

# 当日内存缓存：网络差时重复查看同一股票直接读缓存，秒开
import datetime as _dt
_CACHE = {}
_CACHE_MAX = 600  # 缓存上限（防止扫描/浏览大量股票时内存无限增长）
_CACHE_ORDER = []  # LRU 顺序
def _cache_get(key):
    c = _CACHE.get(key)
    if c and c[0] == _dt.date.today().isoformat():
        # 刷新 LRU 位置
        try:
            _CACHE_ORDER.remove(key)
            _CACHE_ORDER.append(key)
        except ValueError:
            _CACHE_ORDER.append(key)
        return c[1]
    return None
def _cache_set(key, val):
    # 空数据不缓存：避免某次数据源抖动返回空后，当天一直读到空缓存（前端显示"暂无数据"且不自动恢复）
    try:
        if val is None:
            return
        if hasattr(val, "empty") and val.empty:
            return
        if isinstance(val, (list, dict)) and not val:
            return
    except Exception:
        pass
    if key not in _CACHE:
        _CACHE_ORDER.append(key)
    _CACHE[key] = (_dt.date.today().isoformat(), val)
    # 超上限时淘汰最久未用的（只清理当天旧数据）
    while len(_CACHE) > _CACHE_MAX and _CACHE_ORDER:
        old_key = _CACHE_ORDER.pop(0)
        _CACHE.pop(old_key, None)


def _get_json(url, params=None, retry=RETRY, sleep=1.5, timeout=TIMEOUT):
    """带重试的 JSON 请求，代理不稳定时自动重试"""
    last = None
    for i in range(retry):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            return r.json()
        except Exception as e:
            last = e
            time.sleep(sleep * (i + 1))
    raise last


def _code_prefix(code):
    """东财 secid 市场前缀：沪市=1，深市/北交所=0"""
    code = str(code)
    return "1" if code.startswith("6") else "0"


def _tencent_symbol(code):
    """腾讯代码前缀：sh/sz/bj。已带前缀的直接返回（支持指数如sh000001/sz399001）"""
    code = str(code).lower()
    if code.startswith(("sh", "sz", "bj")):
        return code
    if code.startswith("6"):
        return "sh" + code
    if code.startswith(("0", "3")):
        return "sz" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    return "sh" + code


# ---------------------------------------------------------------
# 1. 实时行情（腾讯，可批量）
# ---------------------------------------------------------------
def get_realtime_quotes(codes):
    """批量获取实时行情，返回 list[dict]。分批（每批80只）并行请求，避免URL过长失败。"""
    if not codes:
        return []
    from concurrent.futures import ThreadPoolExecutor, as_completed
    BATCH = 80
    batches = [codes[i:i+BATCH] for i in range(0, len(codes), BATCH)]
    def _fetch_batch(batch):
        q = ",".join(_tencent_symbol(c) for c in batch)
        try:
            r = requests.get(f"https://qt.gtimg.cn/q={q}", headers=HEADERS, timeout=10)
            r.encoding = "gbk"
            return r.text
        except Exception:
            return ""
    all_text = []
    with ThreadPoolExecutor(max_workers=min(5, len(batches))) as ex:
        futures = [ex.submit(_fetch_batch, b) for b in batches]
        for f in as_completed(futures):
            try:
                t = f.result()
                if t:
                    all_text.append(t)
            except Exception:
                continue
    result = []
    for text in all_text:
        for line in text.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            try:
                val = line.split("=", 1)[1].strip().strip('"')
                fields = val.split("~")
                if len(fields) < 49:
                    continue
                result.append({
                    "code": fields[2],
                    "name": fields[1],
                    "price": _f(fields[3]),
                    "pre_close": _f(fields[4]),
                    "open": _f(fields[5]),
                    "high": _f(fields[33]),
                    "low": _f(fields[34]),
                    "change": _f(fields[31]),
                    "pct_chg": _f(fields[32]),
                    "volume": _f(fields[36]),
                    "amount": _f(fields[37]) * 10000,
                    "turnover": _f(fields[38]),
                    "pe": _f(fields[39]),
                    "amplitude": _f(fields[43]),
                    "float_mv": _f(fields[44]) * 1e8,
                    "total_mv": _f(fields[45]) * 1e8,
                    "pb": _f(fields[46]),
                    "limit_up": _f(fields[47]),
                    "limit_down": _f(fields[48]),
                })
            except Exception:
                continue
    return result


def _f(x):
    try:
        v = float(x)
        return v if v == v else 0.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------
# 2. 历史K线（东方财富）
# ---------------------------------------------------------------
def _kline_from_eastmoney(code, klt, fqt, params_base, limit=300):
    """东财 K 线（前复权），返回 DataFrame；失败返回空"""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = dict(params_base)
    params.update({"klt": klt, "fqt": fqt,
                   "secid": f"{_code_prefix(code)}.{code}"})
    # 东财不可用时必须快速失败（单次短超时、不重试），尽快回退到新浪兜底源
    try:
        data = _get_json(url, params, retry=1, sleep=0.2, timeout=2.5).get("data")
        if data and data.get("klines"):
            rows = []
            for line in data["klines"]:
                p = line.split(",")
                rows.append({
                    "date": p[0], "open": float(p[1]), "close": float(p[2]),
                    "high": float(p[3]), "low": float(p[4]),
                    "volume": float(p[5]), "amount": float(p[6]),
                    "amplitude": float(p[7]), "pct_chg": float(p[8]),
                    "change": float(p[9]), "turnover": float(p[10]),
                })
            df = pd.DataFrame(rows)
            if not df.empty:
                return df.tail(limit)
    except Exception:
        pass
    return pd.DataFrame()


def _kline_from_sina(code, period="daily", limit=300):
    """新浪 K 线兜底源（不复权）：返回 DataFrame；失败返回空"""
    scale = {"daily": 240, "weekly": 1200, "monthly": 7200}.get(period, 240)
    sym = _tencent_symbol(code)
    url = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
    params = {"symbol": sym, "scale": scale, "ma": "no", "datalen": str(limit)}
    for _ in range(2):
        try:
            d = _get_json(url, params, retry=1, sleep=0.2, timeout=3)
            if isinstance(d, list) and d:
                rows = []
                for it in d:
                    rows.append({
                        "date": it["day"],
                        "open": float(it["open"]),
                        "close": float(it["close"]),
                        "high": float(it["high"]),
                        "low": float(it["low"]),
                        "volume": float(it["volume"]),
                        "amount": 0.0, "amplitude": 0.0,
                        "pct_chg": 0.0, "change": 0.0, "turnover": 0.0,
                    })
                df = pd.DataFrame(rows)
                if not df.empty:
                    return df.tail(limit)
        except Exception:
            pass
        time.sleep(1.2)
    return pd.DataFrame()


def _kline_from_tencent(code, period="daily", limit=300, adjust="qfq"):
    """腾讯 K 线（前复权/后复权/不复权）。作为最后兜底源（东财/新浪都不通时）。
    返回 DataFrame（列同其他源）；失败返回空。
    腾讯接口偶发 501 限流，用独立浏览器 UA + 腾讯 Referer + 重试3次。"""
    klt = {"daily": "day", "weekly": "week", "monthly": "month"}.get(period, "day")
    sym = _tencent_symbol(code)
    _hdr = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Referer": "https://gu.qq.com/",
    }
    try:
        if adjust in ("qfq", "hfq"):
            url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            params = {"param": f"{sym},{klt},,,{limit},{adjust}"}
        else:
            url = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
            params = {"param": f"{sym},{klt},,{limit}"}
        lines = []
        for i in range(4):
            try:
                r = requests.get(url, params=params, headers=_hdr, timeout=5)
                if r.status_code != 200:
                    time.sleep(1.0 + i)
                    continue
                d = r.json()
                if not isinstance(d, dict) or d.get("code") != 0:
                    time.sleep(1.0 + i)
                    continue
                node = (d.get("data") or {}).get(sym) or {}
                # 腾讯返回列序: [date, open, close, high, low, volume]
                key = "qfqday" if adjust == "qfq" else ("hfqday" if adjust == "hfq" else "day")
                lines = node.get(key) or node.get("day") or []
                if lines:
                    break  # 拿到真实数据
                time.sleep(1.5 + i)  # data为空=限流，等待后重试
            except Exception:
                time.sleep(1.0 + i)
        if not lines:
            return pd.DataFrame()
        rows = []
        for it in lines:
            try:
                rows.append({
                    "date": str(it[0]), "open": float(it[1]), "close": float(it[2]),
                    "high": float(it[3]), "low": float(it[4]), "volume": float(it[5]),
                    "amount": 0.0, "amplitude": 0.0,
                    "pct_chg": 0.0, "change": 0.0, "turnover": 0.0,
                })
            except Exception:
                continue
        df = pd.DataFrame(rows)
        if not df.empty:
            return df.tail(limit)
    except Exception:
        pass
    return pd.DataFrame()


# ---------------------------------------------------------------
# 数据源自适应探测: 检测当前网络哪些源可用, 把可用源排前面
# 解决"某个源被屏蔽时每只股票都白等超时才回退"导致的扫描慢
# ---------------------------------------------------------------
_SOURCE_ORDER = None
_SOURCE_ORDER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bt_data", "source_order.json")

def _probe_sources():
    """探测东财/新浪/腾讯连通性, 返回可用源顺序(可用在前). 当天缓存到文件, 进程内只探测一次."""
    global _SOURCE_ORDER
    if _SOURCE_ORDER is not None:
        return _SOURCE_ORDER
    # 先读当天缓存文件(避免每次启动都花几秒探测)
    try:
        with open(_SOURCE_ORDER_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("date") == time.strftime("%Y-%m-%d") and d.get("order"):
            _SOURCE_ORDER = d["order"]
            return _SOURCE_ORDER
    except Exception:
        pass
    order = []
    params_base = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "beg": "20220101", "end": "20500101",
    }
    # 用贵州茅台(sh600519)短数据探测
    try:
        df = _kline_from_eastmoney("sh600519", 101, 1, params_base, 5)
        if df is not None and not df.empty:
            order.append("eastmoney")
    except Exception:
        pass
    try:
        df = _kline_from_tencent("sh600519", "daily", 5, "qfq")
        if df is not None and not df.empty:
            order.append("tencent")
    except Exception:
        pass
    try:
        df = _kline_from_sina("sh600519", "daily", 5)
        if df is not None and not df.empty:
            order.append("sina")
    except Exception:
        pass
    if not order:
        order = ["eastmoney", "sina", "tencent"]
    # 写缓存文件(当天有效)
    try:
        os.makedirs(os.path.dirname(_SOURCE_ORDER_FILE), exist_ok=True)
        with open(_SOURCE_ORDER_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": time.strftime("%Y-%m-%d"), "order": order}, f, ensure_ascii=False)
    except Exception:
        pass
    _SOURCE_ORDER = order
    return order


def source_order():
    """对外暴露当前数据源优先级(供诊断/界面显示)"""
    return list(_probe_sources())


def get_kline(code, period="daily", limit=300, adjust="qfq"):
    """
    period: daily=日线(101) weekly=周线(102) monthly=月线(103)
    adjust: qfq=前复权 hfq=后复权 ""=不复权
    优先东财（复权），失败自动回退新浪（不复权）再回退腾讯（复权）
    """
    klt = {"daily": 101, "weekly": 102, "monthly": 103}.get(period, 101)
    fqt = {"qfq": 1, "hfq": 2, "": 0}.get(adjust, 1)
    params_base = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "beg": "20220101", "end": "20500101",
    }
    ck = f"kline:{code}:{period}:{limit}:{adjust}"
    hit = _cache_get(ck)
    if hit is not None:
        return hit
    # 按探测到的可用源顺序尝试(避免被屏蔽的源白等超时)
    df = pd.DataFrame()
    for src in _probe_sources():
        if src == "eastmoney":
            df = _kline_from_eastmoney(code, klt, fqt, params_base, limit)
        elif src == "sina":
            df = _kline_from_sina(code, period, limit)
        elif src == "tencent":
            df = _kline_from_tencent(code, period, limit, adjust)
        if df is not None and not df.empty:
            break
    if df is None:
        df = pd.DataFrame()
    _cache_set(ck, df)
    return df


# ---------------------------------------------------------------
# 3. 主力资金流（东方财富，日线）
# ---------------------------------------------------------------
def _fund_flow_from_sina(code, limit=60):
    """新浪资金流兜底源（主力/超大/大/中/小单 净流入，日线）"""
    try:
        url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_qsfx_zjlrqs"
        params = {"page": "1", "num": str(max(limit, 60)), "sort": "opendate",
                  "asc": "0", "daima": _tencent_symbol(code)}
        d = _get_json(url, params, retry=1, sleep=0.2, timeout=3)
        if not isinstance(d, list) or not d:
            return pd.DataFrame()
        def _f(v):
            try:
                return float(v)
            except Exception:
                return 0.0
        rows = []
        for it in d:
            try:
                rows.append({
                    "date": it["opendate"],
                    "main_net": _f(it.get("netamount")),     # 主力净流入（元）
                    "main_pct": _f(it.get("ratioamount")) * 100,  # 主力净占比 %
                    "super_net": _f(it.get("r0_net")),       # 超大单净流入
                    "big_net": _f(it.get("r1_net", 0)),      # 大单（接口未分时置0）
                    "mid_net": _f(it.get("r2_net", 0)),      # 中单
                    "small_net": _f(it.get("r3_net", 0)),    # 小单
                })
            except Exception:
                continue
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame()
        df = df.sort_values("date").reset_index(drop=True)  # 新浪按日期倒序，转升序
        return df.tail(limit)
    except Exception:
        return pd.DataFrame()


def get_fund_flow(code, limit=60):
    """主力资金流日线数据：优先东财，失败快速回退新浪"""
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "lmt": "0", "klt": "101",
        "secid": f"{_code_prefix(code)}.{code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
    }
    ck = f"fundflow:{code}:{limit}"
    hit = _cache_get(ck)
    if hit is not None:
        return hit
    # 探测到东财不可用则直接新浪，避免每只都白等2.5s超时
    if "eastmoney" not in _probe_sources():
        df = _fund_flow_from_sina(code, limit)
        _cache_set(ck, df)
        return df
    # push2his 域名在部分网络下不稳定，快速失败后自动用新浪兜底
    try:
        data = _get_json(url, params, retry=1, sleep=0.2, timeout=2.5).get("data")
        if data and data.get("klines"):
            rows = []
            for line in data["klines"]:
                p = line.split(",")
                # f51日期 f52主力净流入 f53小单 f54中单 f55大单 f56超大单 f57主力净占比
                rows.append({
                    "date": p[0],
                    "main_net": float(p[1]),     # 主力净流入（元）
                    "main_pct": float(p[6]),     # 主力净占比 %
                    "super_net": float(p[5]),    # 超大单净流入
                    "big_net": float(p[4]),      # 大单净流入
                    "mid_net": float(p[3]),      # 中单
                    "small_net": float(p[2]),    # 小单
                })
            df = pd.DataFrame(rows)
            if not df.empty:
                df = df.tail(limit)
                _cache_set(ck, df)
                return df
    except Exception:
        pass
    # 东财不可用 → 新浪兜底
    df = _fund_flow_from_sina(code, limit)
    _cache_set(ck, df)
    return df


# ---------------------------------------------------------------
# 4. 财务指标（东方财富数据中心）
# ---------------------------------------------------------------
def get_financials(code, market="SH", limit=8):
    """主要财务指标（按报告期），返回 DataFrame"""
    secucode = f"{code}.{market}"
    url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "sortColumns": "REPORT_DATE", "sortTypes": "-1",
        "pageSize": limit, "pageNumber": "1",
        "reportName": "RPT_F10_FINANCE_MAINFINADATA",
        "columns": "ALL",
        "filter": f'(SECUCODE="{secucode}")',
    }
    for _ in range(3):
        try:
            d = _get_json(url, params, retry=1, sleep=0.2, timeout=5)
            rows = d.get("result", {}).get("data") or []
            if not rows:
                time.sleep(1.5)
                continue
            df = pd.DataFrame(rows)
            if df.empty:
                time.sleep(1.5)
                continue
            keep = [
                "REPORT_DATE", "REPORT_TYPE", "TOTALOPERATEREVE", "PARENTNETPROFIT",
                "TOTALOPERATEREVETZ", "PARENTNETPROFITTZ", "XSMLL", "ROEJQ",
                "KCFJCXSYJLR", "EPSJB", "BPS",
            ]
            for col in keep:
                if col not in df.columns:
                    df[col] = None
            return df[keep].head(limit)
        except Exception:
            time.sleep(1.5)
    return pd.DataFrame()


# ---------------------------------------------------------------
# 5. 股票搜索（东方财富）
# ---------------------------------------------------------------
def search_stocks(keyword, limit=10):
    """按名称/代码搜索 A 股，返回 list[dict]"""
    url = "https://search-codetable.eastmoney.com/codetable/search/web"
    params = {"client": "web", "keyword": keyword,
              "pageIndex": "1", "pageSize": str(limit)}
    try:
        d = _get_json(url, params, retry=1, sleep=0.2, timeout=5)
        rows = (d.get("result") or []) if d.get("code") == "0" else []
        out = []
        for row in rows:
            # 仅保留 A 股（沪A/深A/京A）
            tname = row.get("securityTypeName", "")
            if not any(k in tname for k in ("沪A", "深A", "京A", "沪市", "深市", "北交所")):
                continue
            out.append({
                "code": row["code"],
                "name": row["shortName"],
                "market": tname,
            })
        return out
    except Exception:
        return []


# ---------------------------------------------------------------
# 6. 判断市场（沪/深/北）
# ---------------------------------------------------------------
def guess_market(code):
    code = str(code)
    if code.startswith("6"):
        return "SH"
    if code.startswith(("0", "3")):
        return "SZ"
    if code.startswith(("4", "8")):
        return "BJ"
    return "SH"


if __name__ == "__main__":
    # 自测
    print("行情:", get_realtime_quotes(["600519", "000001"])[:1])
    print("K线:", len(get_kline("600519", "daily")))
    print("周线:", len(get_kline("600519", "weekly")))
    print("资金流:", get_fund_flow("600519").tail(2).to_dict("records"))
    fin = get_financials("600519", "SH")
    print("财务:", fin[["REPORT_DATE", "TOTALOPERATEREVETZ", "XSMLL"]].head(3).to_dict("records"))
    print("搜索:", search_stocks("茅台")[:3])
