# -*- coding: utf-8 -*-
"""每日扫描模块: 收盘后扫描高适配池, 找出出现机会的股票
机会信号(复用实证规则):
  - 通道A 超跌反弹(抄底): 60日回撤≥20% + 企稳 + 看涨形态
  - 通道B 周线趋势(真趋势): 周线多头排列(周MA5>10>20)为主判据
      [实证: 周线多头60日上涨率52%(+8.7%) vs 仅日线短均线多头46%(+5%)]
评分(通道B): 周线多头基础1 + 日线深回调/三层共振/看涨形态 各+1, 门槛≥2
"""
import json, os, time, threading
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from data_fetcher import get_kline
import layers
import analysis as an
import env_judge

BASE = os.path.dirname(os.path.abspath(__file__))
HIGHFIT_FILE = os.path.join(BASE, "highfit_pool.json")
SIGNALS_FILE = os.path.join(BASE, "daily_signals.json")
LOCK = threading.Lock()

SCAN_THREAD = None
_last_scan = {"running": False, "date": "", "ok": False, "msg": "", "count": 0}


def _pattern_quality(df, i, name, vc):
    """形态有效性分级: 形态 × 位置 × 量能 (用户资料: 形态必须在趋势/位置中才有效)
    返回 (score, level, reason)
    位置: 下跌末端形态最有效; 量能: 放量增强, 缩量削弱
    level: strong>=3 / medium>=1.5 / weak"""
    o = df["open"].tolist(); h = df["high"].tolist(); l = df["low"].tolist(); c = df["close"].tolist()
    v = df["volume"].tolist()
    n = len(c)
    score = 0.0
    reasons = []
    # ---- 位置分: 前10日走势 ----
    if i >= 11:
        prev10 = (c[i - 10] / c[i - 1] - 1) * 100 if c[i - 1] else 0  # 前10日跌幅%
        if prev10 <= -8:
            score += 2; reasons.append("深跌末端")
        elif prev10 <= -3:
            score += 1; reasons.append("下跌末端")
        elif prev10 >= 3:
            score -= 1; reasons.append("上涨中·反转弱")
        else:
            reasons.append("横盘")
    # 距60日低点位置（反转形态在低位更可靠）
    if n >= 60:
        lo60 = min(l[i - 59:i + 1])
        if lo60 and (c[i] - lo60) / lo60 < 0.20:
            score += 1; reasons.append("低位")
    # ---- 形态强度 ----
    if name in ("启明星", "看涨吞没"):
        score += 1; reasons.append("强反转形态")
    elif name == "锤子线":
        pass
    # ---- 量能 ----
    if vc:
        score += 1; reasons.append("放量确认")
    else:
        if i >= 6 and sum(v[max(0, i - 5):i]) / max(len(v[max(0, i - 5):i]), 1) > 0:
            avg5 = sum(v[i - 5:i]) / 5.0
            if avg5 > 0 and v[i] < 0.8 * avg5:
                score -= 1; reasons.append("缩量·削弱")
    # ---- 等级 ----
    if score >= 3:
        level = "strong"
    elif score >= 1.5:
        level = "medium"
    else:
        level = "weak"
    return round(score, 1), level, "+".join(reasons)


def _vol_confirm(df, i):
    """量能确认: 第i根量 >= 1.3 * 前5日均量"""
    vols = df["volume"].tolist()
    if i < 6:
        return False
    base = sum(vols[i - 5:i]) / 5.0
    return base > 0 and vols[i] >= 1.3 * base


def detect_bullish(df, last_n=6):
    """检测最近 last_n 根内的强看涨形态, 返回 [(name, index, vol_confirmed)]"""
    o = df["open"].tolist(); h = df["high"].tolist(); l = df["low"].tolist(); c = df["close"].tolist()
    v = df["volume"].tolist()
    out = []
    n = len(c)
    for i in range(max(1, n - last_n), n):
        body = abs(c[i] - o[i])
        rng = max(h[i] - l[i], 1e-9)
        upper = h[i] - max(o[i], c[i])
        lower = min(o[i], c[i]) - l[i]
        vc = _vol_confirm(df, i)
        # 看涨吞没: 前阴后阳, 今实体包前实体
        if i >= 1 and c[i - 1] < o[i - 1] and c[i] > o[i]:
            prev_body = abs(c[i - 1] - o[i - 1])
            if body > prev_body and c[i] >= max(o[i - 1], c[i - 1]) and o[i] <= min(o[i - 1], c[i - 1]) and prev_body > 0:
                out.append(("看涨吞没", i, vc))
        # 锤子线: 下影>=2倍实体, 上影短, 出现在下跌/低位
        if body > 0 and lower >= 2 * body and upper <= body * 0.5:
            out.append(("锤子线", i, vc))
        # 启明星: 阴 + 小实体 + 阳插入阴实体
        if i >= 2 and c[i - 2] < o[i - 2] and c[i] > o[i]:
            mid_body = abs(c[i - 1] - o[i - 1])
            if mid_body <= 0.4 * abs(c[i - 2] - o[i - 2]) and c[i] > (o[i - 2] + c[i - 2]) / 2:
                out.append(("启明星", i, vc))
        # 红三兵: 三连阳, 收盘渐高, 今日收盘近最高
        if i >= 2 and c[i] > o[i] and c[i - 1] > o[i - 1] and c[i - 2] > o[i - 2]:
            if c[i] > c[i - 1] > c[i - 2] and (h[i] - c[i]) <= body * 0.6:
                out.append(("红三兵", i, vc))
    return out


def _trend_metrics(df):
    """趋势位置指标: (gain60, bias20, dist_hi60, days_above20)
    gain60=60日涨幅% / bias20=乖离MA20% / dist_hi60=距60日高% / days_above20=站上MA20天数"""
    try:
        closes = df["close"].tolist()
        if len(closes) < 61:
            return None
        c = closes[-1]
        base = closes[-61]
        gain60 = (c / base - 1) * 100 if base else 0
        ma20 = sum(closes[-20:]) / 20
        bias = (c / ma20 - 1) * 100 if ma20 else 0
        hi60 = max(df["high"].tolist()[-60:])
        dist_hi = (1 - c / hi60) * 100 if hi60 else 0
        days = 0
        for i in range(len(closes) - 1, -1, -1):
            m20 = sum(closes[max(0, i - 19):i + 1]) / min(20, i + 1)
            if closes[i] > m20:
                days += 1
            else:
                break
        return gain60, bias, dist_hi, days
    except Exception:
        return None


def _ind_gain(ind, asof=None, df=None):
    """行业指数60日涨幅%（用于板块轮动后期过滤）; asof 为 None 用最新"""
    try:
        rows = layers._load_ind_cache().get(ind, [])
        if asof:
            rows = [r for r in rows if r["date"] <= asof]
        if len(rows) < 61:
            return None
        closes = [r["close"] for r in rows]
        return (closes[-1] / closes[-61] - 1) * 100 if closes[-61] else None
    except Exception:
        return None


def _weekly_trend_up(code):
    """周线多头排列确认(真趋势): 周MA5>周MA10>周MA20 且 收盘>周MA10
    [实证: 周线多头60日上涨率52%(+8.3%) vs 仅日线多头45% → 区分真趋势与弱反弹]
    返回 True=周线多头 / False=否 / None=数据不足
    """
    try:
        wdf = get_kline(code, "weekly", 120, "")
        if wdf is None or len(wdf) < 21:
            return None
        c = wdf["close"]
        ma5 = c.rolling(5).mean().iloc[-1]
        ma10 = c.rolling(10).mean().iloc[-1]
        ma20 = c.rolling(20).mean().iloc[-1]
        last = float(c.iloc[-1])
        if ma5 is None or ma10 is None or ma20 is None:
            return None
        return bool(ma5 > ma10 > ma20 and last > ma10)
    except Exception:
        return None


def _ind_mom20(ind, asof=None):
    """行业近20日动量% (行业轮动优先: 走强行业加分)"""
    try:
        rows = layers._load_ind_cache().get(ind, [])
        if asof:
            rows = [r for r in rows if r["date"] <= asof]
        ic = [r["close"] for r in rows]
        if len(ic) < 21:
            return None
        return (ic[-1] / ic[-21] - 1) * 100
    except Exception:
        return None


_IND_MOM_RANK = {}  # 行业动量排名缓存 {ind: percentile(0-1)}, None=无数据


def _industry_mom_rank(refresh=False):
    """行业近20日动量排名(百分位): 前30%为强势行业。
    用于精选分层(A级=行业共振)与中段通道放行。模块级缓存, 一次扫描只算一次。"""
    global _IND_MOM_RANK
    if _IND_MOM_RANK and not refresh:
        return _IND_MOM_RANK
    mom = {}
    try:
        rows_by_ind = layers._load_ind_cache()
        for ind, rows in rows_by_ind.items():
            ic = [r["close"] for r in rows]
            if len(ic) < 21 or ic[-21] <= 0:
                continue
            mom[ind] = ic[-1] / ic[-21] - 1
    except Exception:
        return {}
    if not mom:
        return {}
    ranked = sorted(mom.items(), key=lambda kv: -kv[1])
    n = len(ranked)
    _IND_MOM_RANK = {ind: (i + 1) / n for i, (ind, m) in enumerate(ranked)}
    return _IND_MOM_RANK


def _weekly_strong(code):
    """周线转强(门槛降低, 替代严格多头排列): 周MA5>周MA10 且 最新周收盘>周MA5
    [实证: 严格多头排列太滞后, 启动初期(医药/通信等)常被漏掉; 转强信号提前1-2周]"""
    try:
        wdf = get_kline(code, "weekly", 120, "")
        if wdf is None or len(wdf) < 11:
            return None
        c = wdf["close"].tolist()
        ma5 = sum(c[-5:]) / 5
        ma10 = sum(c[-10:]) / 10
        return bool(ma5 > ma10 and c[-1] > ma5)
    except Exception:
        return None


def _vol3_ratio(df):
    """近3日任一放量倍数(放量窗口放宽: 不只看当日, 捕捉提前放量启动)"""
    try:
        v = df["volume"].tolist()
        if len(v) < 8:
            return 1.0
        best = 1.0
        for i in range(-3, 0):
            v5 = sum(v[i - 6:i - 1]) / 5
            if v5 > 0:
                best = max(best, v[i] / v5)
        return best
    except Exception:
        return 1.0


def _mkt_high_weak():
    """大盘高位滞涨: 距60日高<5% 且 近20日动量转负 → 建议降仓
    [实证: 6/15大盘距高-3.4%且近20日-0.8%, 之后系统性下跌; 高位滞涨时应降仓]"""
    try:
        rows = layers.get_market_kline(80)
        closes = [r["close"] for r in rows]
        if len(closes) < 65:
            return False
        c = closes[-1]
        hi60 = max(closes[-60:])
        dd = c / hi60 - 1
        mom20 = c / closes[-21] - 1 if len(closes) >= 21 else 0
        return dd > -0.05 and mom20 < 0
    except Exception:
        return False


def _ind_weekly_up(ind):
    """行业周线多头(当前行业趋势向上): 行业周收盘MA5>MA10 且 最新周收盘>MA10
    [实证: 个股+行业双周线多头 15日收益+8.6% vs 全部+1.2%, 行情分化时区分度最高]
    返回 True=行业周线多头 / False=否 / None=数据不足(不拦截)
    """
    try:
        rows = layers._load_ind_cache().get(ind, [])
        if len(rows) < 50:
            return None
        weeks = {}
        for r in rows:
            d = datetime.datetime.strptime(r["date"], "%Y-%m-%d")
            wk = (d - datetime.timedelta(days=d.weekday())).strftime("%Y-%m-%d")
            weeks[wk] = r["close"]
        cs = [weeks[k] for k in sorted(weeks)]
        if len(cs) < 10:
            return None
        ma5 = sum(cs[-5:]) / 5
        ma10 = sum(cs[-10:]) / 10
        return bool(ma5 > ma10 and cs[-1] > ma10)
    except Exception:
        return None


def _overheated(df):
    """高位过热检测（趋势追涨过滤）:
    1. 距60日高点<8% = 追高
    2. RSI(14)>70 = 超买
    3. 高位放量滞涨: 放量(>1.5x前5日均量) 但 3日涨幅<1%
    返回 (是否过热, 原因)"""
    try:
        closes = df["close"].tolist()
        if len(closes) < 60:
            return False, ""
        hi60 = max(df["high"].tolist()[-60:])
        close = float(closes[-1])
        if hi60 > 0 and close / hi60 > 0.92:
            return True, "距60日高点<8%"
        tech = an.calc_indicators(df)
        rsi = (tech.get("rsi") or {}).get("value")
        if rsi is not None and rsi > 70:
            return True, "RSI超买(%.0f)" % rsi
        vol = df["volume"].tolist()
        if len(vol) >= 8:
            v5 = sum(vol[-6:-1]) / 5
            if v5 > 0 and vol[-1] > v5 * 1.5:
                ret3 = (closes[-1] / closes[-4] - 1) * 100 if len(closes) >= 4 and closes[-4] else 0
                if ret3 < 1:
                    return True, "高位放量滞涨"
    except Exception:
        pass
    return False, ""


def _mkt_weak():
    """大盘转弱: 上证指数收盘 < MA20(20)"""
    try:
        rows = layers.get_market_kline(60)
        closes = [r["close"] for r in rows]
        if len(closes) < 25:
            return False
        ma20 = sum(closes[-20:]) / 20
        return closes[-1] < ma20
    except Exception:
        return False


def _mkt_system_weak():
    """大盘系统性转弱: MA20<MA60(死叉) 且 近5日跌>2%
    [实证: 2026年触发12次, 后10日58%下跌且不跌也横盘; 6/29触发回避7月下跌,
     3/25假死叉因动量未转负被过滤]"""
    try:
        rows = layers.get_market_kline(80)
        closes = [r["close"] for r in rows]
        if len(closes) < 65:
            return False
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60
        if ma20 >= ma60:
            return False  # 未死叉
        c, c5 = closes[-1], closes[-6]
        return c < c5 * 0.98  # 近5日跌>2%
    except Exception:
        return False


def _mkt_gate():
    """大盘门卫: 不在下跌 + 不在顶部
    - 不在下跌: 大盘方向 != down (允许横盘)
    - 不在顶部: 大盘近5日不显著回落 (近5日跌幅<1.5%, 排除高位滞涨/顶部)
    回测: 5/20顶部(近5日-1.9%)拦截, 6/22企稳(近5日+3.3%)放行"""
    try:
        rows = layers.get_market_kline(80)
        closes = [r["close"] for r in rows]
        if len(closes) < 65:
            return True
        if layers._direction(closes) == "down":
            return False
        c, c5 = closes[-1], closes[-6]
        if c < c5 * 0.985:  # 近5日跌超1.5%
            return False
        if _mkt_system_weak():  # 死叉+5日跌2% → 系统性转弱
            return False
        return True
    except Exception:
        return True


def _ind_gate(ind, asof=None):
    """行业门卫: 不在下跌 + 不在顶部
    - 不在下跌: 行业方向 != down
    - 不在顶部: 行业距60日高回撤>2%"""
    try:
        rows = layers._load_ind_cache().get(ind, [])
        if asof:
            rows = [r for r in rows if r["date"] <= asof]
        closes = [r["close"] for r in rows]
        if len(closes) < 61:
            return True
        if layers._direction(closes) == "down":
            return False
        hi60 = max(closes[-60:])
        if closes[-1] >= hi60 * 0.98:  # 距高点<2% = 贴顶
            return False
        return True
    except Exception:
        return True


def _stabilized(df):
    """超跌企稳: 不再创新低 + 站上MA10 (下跌衰竭, 非接刀)
    - 不再创新低: 近5日最低 > 前25日最低
    - 站上MA10: 收盘 > MA10 (短期趋势转平)"""
    try:
        closes = df["close"].tolist()
        lows = df["low"].tolist()
        if len(closes) < 30:
            return True
        c = closes[-1]
        ma10 = sum(closes[-10:]) / 10
        if c <= ma10:
            return False
        recent_low = min(lows[-5:])
        prev_low = min(lows[-30:-5])
        return recent_low > prev_low
    except Exception:
        return True


def _mkt_strong():
    """大盘持续确认: MA20>MA60 且 MA20 上行（比 close>MA20 严格, 确认多头）"""
    try:
        rows = layers.get_market_kline(200)
        closes = [r["close"] for r in rows]
        if len(closes) < 61:
            return False
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60
        ma20_prev = sum(closes[-21:-1]) / 20
        return ma20 > ma60 and ma20 > ma20_prev
    except Exception:
        return False


def _ind_trend(ind, asof=None):
    """行业指数趋势健康: 行业 MA20>MA60 且 MA20 上行（行业整体向上, 非个股孤涨）
    返回 True/False/None(数据不足)"""
    try:
        rows = layers._load_ind_cache().get(ind, [])
        if asof:
            rows = [r for r in rows if r["date"] <= asof]
        closes = [r["close"] for r in rows]
        if len(closes) < 61:
            return None
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60
        ma20_prev = sum(closes[-21:-1]) / 20
        return ma20 > ma60 and ma20 > ma20_prev
    except Exception:
        return None


def _fund_inflow(code, asof=None, days=5):
    """资金进场: 近days日主力净流入合计为正（资金在推, 非仅价格上）
    返回 True/False/None(无数据)"""
    try:
        from data_fetcher import get_fund_flow
        ff = get_fund_flow(code, limit=20)
        if ff is None or len(ff) == 0:
            return None
        if asof:
            ff = ff[ff["date"] <= asof]
        if len(ff) == 0:
            return None
        return float(ff.tail(days)["main_net"].sum()) > 0
    except Exception:
        return None


def _calc_rating(code, daily):
    """给机会信号算综合评级（复用 generate_conclusion，与单股页口径一致）"""
    import concurrent.futures as cf
    from data_fetcher import get_fund_flow, get_financials, guess_market
    def fetch(kind):
        try:
            if kind == "weekly":
                return get_kline(code, "weekly", 120, "")
            if kind == "monthly":
                return get_kline(code, "monthly", 80, "")
            if kind == "ff":
                return get_fund_flow(code, limit=60)
            if kind == "fin":
                return get_financials(code, guess_market(code), limit=8)
        except Exception:
            return None
    if daily is None:
        return "中性"
    try:
        with cf.ThreadPoolExecutor(max_workers=4) as ex:
            fw = ex.submit(fetch, "weekly"); fm = ex.submit(fetch, "monthly")
            fff = ex.submit(fetch, "ff"); fn = ex.submit(fetch, "fin")
            weekly = fw.result(timeout=8); monthly = fm.result(timeout=8)
            ff = fff.result(timeout=8); fin = fn.result(timeout=8)
        trends = {
            "daily": an.analyze_trend(daily, "日线"),
            "weekly": an.analyze_trend(weekly, "周线") if weekly is not None and len(weekly) >= 30 else {"direction": "unknown", "strength": "weak"},
            "monthly": an.analyze_trend(monthly, "月线") if monthly is not None and len(monthly) >= 30 else {"direction": "unknown", "strength": "weak"},
        }
        tech = an.calc_indicators(daily)
        fund = an.analyze_fund_flow(ff) if ff is not None and len(ff) else None
        fundamentals = an.analyze_fundamentals(fin) if fin is not None and len(fin) else None
        return an.generate_conclusion(trends, tech, fund, fundamentals, None).get("rating", "中性")
    except Exception:
        return "中性"


def _scan_one(it):
    """机会多通道(改进版v2, 同步回测验证结果):
    通道1 深/中超跌反弹: 60日回撤≥20% + 形态 + (企稳/周线转强/站上MA10任一确认)
    通道2 超跌放量: 60日回撤≥20% + 近3日放量1.5x + 翻红/站上MA5 (捕捉提前放量启动)
    通道3 底部连阳: 回撤5-25% + 3日连阳>2% + 站上MA10 + (放量/形态/周线转强)
    通道4 浅超跌启动: 回撤10-20% + 站上MA20 + 强中形态/放量/周线转强
    通道5 周线转强趋势: 周MA5>MA10 + 站上MA20 + 不追高 (门槛低于原严格多头)
    行业门卫分级: 行业down不否决, 放行强信号(深超跌+放量/形态 或 周线转强+形态)
    评分重构: 行业弱·强信号+2 / 行业走强-1 / 超跌放量+1 / 深超跌+1
    """
    code = it.get("code"); name = it.get("name", ""); ind = it.get("ind", "")
    try:
        df = get_kline(code, "daily", 300, "")
        if df is None or len(df) < 60:
            return None
        trend = an.analyze_trend(df, "日线")
        direction = trend.get("direction", "sideways")
        strength = trend.get("strength", "weak")
        ly = layers.analyze_layers(code, df)
        mkt_dir = ly["market"]["direction"]
        pats = detect_bullish(df)
        last = df.iloc[-1]
        close = float(last["close"])
        closes = df["close"].tolist()
        chg = close / float(df.iloc[-2]["close"]) - 1 if len(df) >= 2 else 0
        chg3 = close / closes[-4] - 1 if len(closes) >= 4 and closes[-4] else 0
        # 超跌检测: 60日(含当日)高点回撤
        hi60 = max(df["high"].tolist()[-60:])
        dd60 = close / hi60 - 1
        # 企稳/均线
        stabilized = _stabilized(df)
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else close
        ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else close
        ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else close
        above_ma20 = close > ma20
        above_ma10 = close > ma10
        above_ma5 = close > ma5
        short_up = ma5 > ma10
        # 放量(近3日窗口)
        vr3 = _vol3_ratio(df)
        vol_hit = vr3 > 1.5
        vol_hit13 = vr3 > 1.3
        # 形态质量
        best_score, best_grade, best_pat = -99, "weak", ""
        for pn, pi, pvc in pats:
            sc, lv, _ = _pattern_quality(df, pi, pn, pvc)
            if sc > best_score:
                best_score, best_grade, best_pat = sc, lv, pn

        # ============ 门卫: 大盘 不下跌+不在顶部 ============
        if not _mkt_gate():
            return None
        # 大盘高位滞涨(不否决, 降权+建议降仓)
        mkt_high_weak = _mkt_high_weak()

        # ============ 行业门卫(分级) ============
        ind_dir = ly["industry"]["direction"]
        ind_down = (ind_dir == "down")
        ind_mom = _ind_mom20(ind)
        ind_rank = _industry_mom_rank().get(ind, None)
        ind_mom_ok = (ind_rank is not None and ind_rank <= 0.30 and ind_mom is not None and ind_mom > 0)
        # 60日涨幅(过热/位置)
        base60 = closes[-61] if len(closes) >= 61 else close
        gain60 = (close / base60 - 1) * 100 if base60 else 0
        dist_hi = (1 - close / hi60) * 100 if hi60 else 0
        over = (dist_hi < 5 and gain60 > 40)
        # 周线转强(门槛降低)
        wk_strong = _weekly_strong(code)
        # 连阳: 近3日收阳≥2天 且 3日累计涨>2%
        up3 = sum(1 for i in range(-3, 0) if closes[i] > closes[i - 1])
        lian_yang = (up3 >= 2 and chg3 > 0.02)

        score = 0; channel = None; tags = []

        # ============ 通道1: 深/中超跌反弹 ============
        if dd60 <= -0.20 and pats:
            confirm = wk_strong or above_ma10 or vol_hit13
            if confirm:
                score = 2
                if dd60 <= -0.25:
                    score += 1  # 深超跌
                if best_grade == "strong":
                    score += 1  # 强形态
                if ind_mom_ok:
                    score += 1  # 行业走强
                else:
                    score += 2  # 行业弱·强信号(实证高胜率)
                if mkt_dir == "up":
                    score += 1
                if mkt_high_weak:
                    score -= 1  # 大盘高位滞涨降权
                channel = "超跌反弹"
                tags = ["超跌企稳", f"形态{best_grade}"]
                if dd60 <= -0.25:
                    tags.append("深超跌")
                if wk_strong:
                    tags.append("周线转强")
                if vol_hit13:
                    tags.append("放量")
                if ind_mom_ok:
                    tags.append("行业走强")
                else:
                    tags.append("行业弱·强信号")

        # ============ 通道2: 超跌放量(近3日放量启动) ============
        if channel is None and dd60 <= -0.20:
            if vol_hit and (above_ma5 or chg3 > 0):
                score = 2
                if dd60 <= -0.25:
                    score += 1
                if wk_strong:
                    score += 1
                if ind_mom_ok:
                    score += 1
                else:
                    score += 2
                if mkt_high_weak:
                    score -= 1
                channel = "超跌放量"
                tags = ["超跌放量", f"{vr3:.1f}x"]
                if wk_strong:
                    tags.append("周线转强")
                if ind_mom_ok:
                    tags.append("行业走强")
                else:
                    tags.append("行业弱·强信号")

        # ============ 通道3: 底部连阳 ============
        if channel is None and -0.25 < dd60 <= -0.05:
            if lian_yang and above_ma10 and (vol_hit or pats or wk_strong):
                score = 2
                if best_grade == "strong":
                    score += 1
                if vol_hit:
                    score += 1
                if wk_strong:
                    score += 1
                if ind_mom_ok:
                    score += 1
                else:
                    score += 1
                if mkt_high_weak:
                    score -= 1
                channel = "底部连阳"
                tags = ["底部连阳", f"{chg3 * 100:+.0f}%/3日"]
                if vol_hit:
                    tags.append("放量")
                if wk_strong:
                    tags.append("周线转强")
                if pats:
                    tags.append("+".join(sorted(set(p[0] for p in pats))[:2]))
                if ind_mom_ok:
                    tags.append("行业走强")
                else:
                    tags.append("行业弱·强信号")

        # ============ 通道4: 浅超跌启动 ============
        if channel is None and -0.20 < dd60 <= -0.10:
            if above_ma20 and (short_up or stabilized or wk_strong):
                cond = (best_grade in ("strong", "medium")) or vol_hit or wk_strong
                if cond:
                    score = 1
                    if best_grade == "strong":
                        score += 1
                    if vol_hit:
                        score += 1
                    if ind_mom_ok:
                        score += 1
                    else:
                        score += 1
                    if stabilized:
                        score += 1
                    if mkt_dir == "up":
                        score += 1
                    if mkt_high_weak:
                        score -= 1
                    channel = "浅超跌启动"
                    tags = ["浅超跌启动", f"形态{best_grade}" if best_grade != "weak" else "转强"]
                    if vol_hit:
                        tags.append("放量")
                    if wk_strong:
                        tags.append("周线转强")
                    if ind_mom_ok:
                        tags.append("行业走强")
                    else:
                        tags.append("行业弱·强信号")

        # ============ 通道5: 周线转强趋势 ============
        if channel is None and wk_strong and above_ma20 and not over:
            score = 1
            if pats:
                score += 1
            if direction == "down":
                score += 1  # 日线深回调买点
            elif direction == "side":
                score += 0.5
            if ind_mom_ok:
                score += 1
            elif ind_down:
                score += 1  # 行业弱但个股周线转强
            if mkt_dir == "up" and ind_dir == "up":
                score += 1  # 三层共振
            if gain60 > 60:
                score -= 1  # 已涨太多降权
            channel = "周线趋势"
            tags = ["周线趋势"]
            if pats:
                tags.append("+".join(sorted(set(p[0] for p in pats))[:2]))
            if direction == "down":
                tags.append("日线深回调")
            if ind_mom_ok:
                tags.append("行业走强")
            elif ind_down:
                tags.append("行业弱·强信号")
            if mkt_dir == "up" and ind_dir == "up":
                tags.append("三层共振")

        # ============ 通道6: 中段强势(接近新高的趋势中段股) ============
        # [实证: 2/15前20漏掉6-8只都是dd60在0~-5%的强势中段股(德业/开山/电光/博众等),
        #  日线up+有形态+站上MA20, 但5个通道都要求回撤>=5%被全拒]
        # 仅行业动量前30%放行(行业弱时追高创新高股风险大)
        # 中段通道行业门槛: 放宽到前60%(仅本通道, 其余通道仍用前30%加分)
        ind_mid_ok = (ind_rank is not None and ind_rank <= 0.60
                      and ind_mom is not None and ind_mom > 0)
        if channel is None and ind_mid_ok:
            if -0.05 <= dd60 and above_ma20 and (direction == "up" or short_up):
                if pats or vol_hit:
                    hot = (gain60 > 80) or (dist_hi < 1 and gain60 > 40)
                    if not hot:
                        # 中段股特征: 沿均线爬升, 少有强反转形态 → 基础分2(有行业共振+接近新高+日线up三重前提)
                        score = 2
                        if best_grade == "strong":
                            score += 1
                        if vol_hit:
                            score += 1
                        if mkt_dir == "up" and ind_rank is not None and ind_rank <= 0.15:
                            score += 1  # 大盘+行业双共振
                        if direction == "up" and best_grade in ("strong", "medium"):
                            score += 1
                        channel = "中段强势"
                        tags = ["中段强势", f"距高{dist_hi:.0f}%"]
                        if pats:
                            tags.append("+".join(sorted(set(p[0] for p in pats))[:2]))
                        if vol_hit:
                            tags.append("放量")
                        if best_grade == "strong":
                            tags.append(f"形态{best_grade}")
                        tags.append("行业共振")

        if channel is None:
            return None
        # ============ 质量门槛 + 行业down强信号 ============
        if ind_down:
            strong_signal = (dd60 <= -0.25 and (pats or vol_hit)) or (wk_strong and (pats or above_ma20)) or (lian_yang and (vol_hit or pats))
            if not strong_signal:
                return None
            if score < 2:
                return None
        else:
            # 差异化门槛: 超跌类通道(1-4)需score>=3(强确认), 趋势类通道(5/6)保持>=2
            if channel in ("超跌反弹", "超跌放量", "底部连阳", "浅超跌启动") and score < 3:
                return None
            if score < 2:
                return None
        level = min(3, int(round(score)))
        tags.append(f"评分{score:.0f}")
        if mkt_high_weak:
            tags.append("大盘高位·降仓")
        return {
            "code": code, "name": name, "ind": ind,
            "type": channel, "level": level,
            "price": round(close, 2),
            "change_pct": round(chg * 100, 2),
            "tags": tags, "resonance": False,
            "direction": direction, "strength": strength,
            "dd60": round(dd60 * 100, 1),
            "pats": [p[0] for p in pats],
            "rating": _calc_rating(code, df),
            "tier": "A" if ind_mom_ok else "B",
        }
    except Exception:
        return None


def run_scan_async(limit=None, workers=4):
    """后台线程扫描，立即返回，前端轮询进度。避免同步阻塞主服务。"""
    if _last_scan["running"]:
        return {"ok": False, "msg": "扫描进行中"}
    def _work():
        try:
            run_scan(limit=limit, workers=workers)
        except Exception as e:
            _last_scan.update(running=False, ok=False, msg=f"扫描异常: {e}")
    threading.Thread(target=_work, daemon=True).start()
    return {"ok": True, "started": True, "msg": "已开始后台扫描"}


def run_scan(limit=None, workers=4):
    """扫描高适配池, 返回信号列表并保存。同一时间只允许一次。"""
    global SCAN_THREAD
    if _last_scan["running"]:
        return {"ok": False, "msg": "扫描进行中"}
    try:
        pool = json.load(open(HIGHFIT_FILE, encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "msg": f"高适配池读取失败: {e}"}
    if limit:
        pool = pool[:limit]
    _last_scan.update(running=True, msg=f"扫描 {len(pool)} 只高适配股...")
    signals = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_scan_one, it) for it in pool]
        for f in as_completed(futs):
            try:
                r = f.result()
            except Exception:
                r = None  # 单只股票扫描失败不影响整体
            if r:
                signals.append(r)
    # 精选分层标签: A级=行业共振(顺势型), B级=行业弱·强信号(反转型)
    # 实证: 排序偏置会压掉高分股(2/15协鑫+25.8%被压出前5, 收益+1.2%→-2.0%),
    #       故不做A/B排序偏置, 仅作类型标签供用户区分
    try:
        _hwl = _mkt_high_weak()
    except Exception:
        _hwl = False
    signals.sort(key=lambda x: (-x["level"], x["change_pct"]))
    _mode_note = "高位滞涨·注意降仓" if _hwl else "正常环境"
    a_cnt = sum(1 for sg in signals if sg.get("tier") == "A")
    # 市场环境模式(用户可调): 决定是否过滤/仓位
    try:
        env = env_judge.env_action()
    except Exception as e:
        # 环境计算失败（网络/数据问题）时用默认值放行，不让整个扫描崩掉
        env = {"action": "hold_buy", "pos_pct": 100, "score": None,
               "mode": "unknown", "threshold": 4, "min_pos": 30,
               "note": f"环境计算失败({e}), 默认放行"}
    if env["action"] == "filter_out":
        signals = []  # 环境评分不足(稳健/自动模式) → 空仓, 不推荐
    elif env["pos_pct"] < 100:
        # 动态仓位(进取/自动): 给信号标注建议仓位
        for sg in signals:
            if "建议仓位" not in "".join(sg.get("tags", [])):
                sg.setdefault("tags", []).append(f"建议仓位{env['pos_pct']:.0f}%")
    out = {
        "date": time.strftime("%Y-%m-%d"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scanned": len(pool),
        "elapsed_sec": round(time.time() - t0, 1),
        "signals": signals,
        "env": env,
        "tier_a": a_cnt,
        "sort_mode": _mode_note,
    }
    with LOCK:
        json.dump(out, open(SIGNALS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        _last_scan.update(running=False, date=out["date"], ok=True,
                          msg=f"完成, {len(signals)} 只机会", count=len(signals))
    return {"ok": True, **{k: out[k] for k in ("date", "updated_at", "scanned", "elapsed_sec", "signals", "env")}}


def load_signals():
    try:
        return json.load(open(SIGNALS_FILE, encoding="utf-8"))
    except Exception:
        return {"date": "", "updated_at": "", "scanned": 0, "signals": []}


def scan_status():
    return dict(_last_scan)


def schedule_daily(hour=15, minute=35):
    """在后台线程里每日定点自动扫描(仅当软件运行时)。交易日约15:35 A股收盘后。"""
    def _loop():
        while True:
            now = time.localtime()
            # 简单: 每天在目标时间后首次触发(避免反复)
            target = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, hour, minute, 0, 0, 0, -1))
            if time.time() >= target:
                cur = load_signals()
                if cur.get("date") != time.strftime("%Y-%m-%d"):
                    run_scan()
                time.sleep(3600)
            else:
                time.sleep(60)
    threading.Thread(target=_loop, daemon=True).start()


def maybe_scan_on_startup():
    """启动时: 若今天还没扫描过且已过收盘时间, 自动扫描"""
    cur = load_signals()
    today = time.strftime("%Y-%m-%d")
    if cur.get("date") == today:
        return {"ok": True, "msg": "今日已扫描"}
    if int(time.strftime("%H%M")) >= 1530:
        return run_scan()
    return {"ok": True, "msg": "未到收盘时间(15:30), 暂不自动扫描"}
