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
        wdf = get_kline(code, "weekly", 120, "qfq")
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
                return get_kline(code, "weekly", 120, "qfq")
            if kind == "monthly":
                return get_kline(code, "monthly", 80, "qfq")
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
    """机会双通道:
    通道A 超跌反弹(抄底): 60日回撤≥20% + 看涨形态
          [实证: 回撤越深胜率越高(20%→55.8%, 25%→57.8%), 大盘弱时更高(58%),
           放量反而降胜率→仅提示谨慎]
    通道B 周线趋势(真趋势): 周线多头排列(周MA5>10>20且收盘>MA10) + 日线回调/共振/形态加分
    """
    code = it.get("code"); name = it.get("name", ""); ind = it.get("ind", "")
    try:
        df = get_kline(code, "daily", 300, "qfq")
        if df is None or len(df) < 60:
            return None
        trend = an.analyze_trend(df, "日线")
        direction = trend.get("direction", "sideways")
        strength = trend.get("strength", "weak")
        ly = layers.analyze_layers(code, df)
        mkt_dir = ly["market"]["direction"]
        pats = detect_bullish(df)
        vol_hit = any(p[2] for p in pats)
        last = df.iloc[-1]
        chg = float(last["close"]) / float(df.iloc[-2]["close"]) - 1 if len(df) >= 2 else 0
        # 超跌检测: 60日(含当日)高点回撤
        hi60 = max(df["high"].tolist()[-60:])
        dd60 = float(last["close"]) / hi60 - 1

        # ============ 门卫: 大盘 不下跌+不在顶部 (两通道必过) ============
        if not _mkt_gate():
            return None

        # ============ 通道A: 超跌企稳(抄底) ============
        # 超跌通道: 行业仅要求不在下跌(不做顶部限制, 超跌补涨不依赖行业位置)
        if ly["industry"]["direction"] == "down":
            return None
        # 企稳: 不再创新低 + 站上MA10 (超跌但趋势企稳, 在稳定趋势中找突破)
        stabilized = _stabilized(df)
        if dd60 <= -0.20 and pats and stabilized:
            # 形态质量: 取最优形态(位置+量能配合) [回测: strong/med 40% vs weak 20%]
            best_score, best_grade, best_pat = -99, "weak", ""
            for pn, pi, pvc in pats:
                sc, lv, _ = _pattern_quality(df, pi, pn, pvc)
                if sc > best_score:
                    best_score, best_grade, best_pat = sc, lv, pn
            if best_grade == "weak":
                return None  # 形态弱(位置/量能不配合) → 过滤
            level = 1
            if dd60 <= -0.25:
                level += 1
            if mkt_dir == "down":
                level += 1  # 大盘弱时胜率最高(58%)
            level = min(level, 2)
            tags = ["超跌企稳", f"形态{best_grade}"]
            tags.append("+".join(sorted(set(p[0] for p in pats))[:2]))
            if mkt_dir == "down":
                tags.append("大盘弱·抄底")
            elif mkt_dir == "up":
                tags.append("大盘强·谨慎")
            if vol_hit:
                tags.append("放量·谨慎")
            return {
                "code": code, "name": name, "ind": ind,
                "type": "rebound", "level": level,
                "price": round(float(last["close"]), 2),
                "change_pct": round(chg * 100, 2),
                "tags": tags, "resonance": False,
                "direction": direction, "strength": strength,
                "dd60": round(dd60 * 100, 1),
                "pats": [p[0] for p in pats],
                "rating": _calc_rating(code, df),
            }

        # ============ 通道B: 周线趋势(真趋势) ============
        # 主判据: 周线多头排列(周MA5>10>20 且 收盘>周MA10)
        #   [实证: 周线多头60日上涨率52%(+8.7%) vs 仅日线短均线多头46%(+5%)]
        #   日线回调质量加分: 深回调(3日54%)>回调(50%)>追高(45%)
        wk_up = _weekly_trend_up(code)
        if not wk_up:
            return None
        # 过滤0: 行业门卫(not down + 不在顶部, 趋势追涨怕高位接力)
        if not _ind_gate(ly["industry"]["name"]):
            return None
        # 过滤1: 高位过热（距60日高<8% / RSI超买 / 高位放量滞涨, 防日线追高）
        over, over_reason = _overheated(df)
        if over:
            return None
        # 过滤2: 趋势阶段（买启动/确认期, 不追中继/衰竭期）
        tm = _trend_metrics(df)
        if tm is None:
            return None
        gain60, bias, dist_hi, days_above20 = tm
        if gain60 > 100 or bias > 20 or dist_hi < 5:
            return None  # 衰竭期
        if gain60 > 60 or bias > 10:
            return None  # 中继期
        # 过滤3: 板块轮动后期（个股涨幅远超行业=追高接力, 不追）
        ig = _ind_gain(ly["industry"]["name"])
        if ig is not None and gain60 - ig > 40:
            return None
        # 过滤4: 行业未破位（行业 close<MA20 → 拦; 允许个股先启动）
        ind_rows = layers._load_ind_cache().get(ly["industry"]["name"], [])
        if len(ind_rows) >= 25:
            icl = [r["close"] for r in ind_rows]
            if icl[-1] < sum(icl[-20:]) / 20:
                return None
        # 过滤5: 行业位置（行业60日涨幅>25% = 行业高位, 高位接力不做）
        ig2 = _ind_gain(ly["industry"]["name"])
        if ig2 is not None and ig2 > 25:
            return None
        # 过滤6: 行业趋势确认（行业周线多头 = 当前行业趋势向上）
        #   [实证: 个股+行业双周线多头 15日收益+8.6% vs 全部+1.2%;
        #    行业60日涨幅是坏指标(会误杀刚启动板块, 如半导体60日<0但正走强)]
        iw_up = _ind_weekly_up(ly["industry"]["name"])
        if iw_up is False:
            return None
        reso = (mkt_dir == "up" and
                ly["industry"]["direction"] == "up")
        # 评分: 周线多头基础1 + 深回调/共振/形态各+1
        score = 1
        if direction == "down":
            score += 1  # 日线深回调买点(实证60日54%, 3日54%)
        if reso:
            score += 1  # 三层共振
        if pats:
            score += 1  # 看涨形态
        if score < 2:
            return None  # 周线多头但无任何加分(追高且无形态无共振) → 过滤
        level = min(score, 3)
        tags = []
        if reso:
            tags.append("三层共振")
        if pats:
            names = [p[0] for p in pats]
            tags.append("+".join(sorted(set(names))[:2]) + ("·放量" if vol_hit else ""))
        tags.append("周线趋势")
        if direction == "down":
            tags.append("日线深回调")
        elif direction == "side":
            tags.append("日线回调")
        return {
            "code": code, "name": name, "ind": ind,
            "type": "trend", "level": level,
            "price": round(float(last["close"]), 2),
            "change_pct": round(chg * 100, 2),
            "tags": tags,
            "resonance": reso,
            "direction": direction, "strength": strength,
            "pats": [p[0] for p in pats],
            "rating": _calc_rating(code, df),
        }
    except Exception:
        return None


def run_scan(limit=None, workers=8):
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
    signals.sort(key=lambda x: (-x["level"], x["change_pct"]))
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
