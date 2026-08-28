# -*- coding: utf-8 -*-
"""每日扫描模块: 收盘后扫描高适配池, 找出出现机会的股票
机会信号(复用实证规则):
  - 三层共振看涨 (大盘↑+行业↑+个股上升趋势线)   [实证: 胜率54-56%, 收益2倍基准]
  - 趋势内强看涨形态 + 量能确认 (≥1.3×前5日均量) [实证: 大市值蓝筹命中率显著提升]
评分: 共振看涨+2, 上升趋势+看涨形态+放量 +2, 上升趋势+看涨形态 +1
"""
import json, os, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from data_fetcher import get_kline
import layers
import analysis as an

BASE = os.path.dirname(os.path.abspath(__file__))
HIGHFIT_FILE = os.path.join(BASE, "highfit_pool.json")
SIGNALS_FILE = os.path.join(BASE, "daily_signals.json")
LOCK = threading.Lock()

SCAN_THREAD = None
_last_scan = {"running": False, "date": "", "ok": False, "msg": "", "count": 0}


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
    通道B 趋势机会(追涨): 日线上升趋势 + 看涨形态(放量加分)
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

        # ============ 通道A: 超跌反弹(抄底) ============
        if dd60 <= -0.20 and pats:
            level = 1
            if dd60 <= -0.25:
                level += 1
            if mkt_dir == "down":
                level += 1  # 大盘弱时胜率最高(58%)
            level = min(level, 2)
            tags = ["超跌反弹"]
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

        # ============ 通道B: 趋势机会(追涨) ============
        if direction == "down":
            return None
        reso = (mkt_dir == "up" and
                ly["industry"]["direction"] == "up" and
                direction == "up")
        score = 0
        if reso:
            score += 2
        if direction == "up" and pats:
            score += 2 if vol_hit else 1
        if score < 1:
            return None
        tags = []
        if reso:
            tags.append("三层共振")
        if pats:
            names = [p[0] for p in pats]
            tags.append("+".join(sorted(set(names))[:2]) + ("·放量" if vol_hit else ""))
        if direction == "up":
            tags.append("上升趋势")
        return {
            "code": code, "name": name, "ind": ind,
            "type": "trend", "level": score,
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
            r = f.result()
            if r:
                signals.append(r)
    signals.sort(key=lambda x: (-x["level"], x["change_pct"]))
    out = {
        "date": time.strftime("%Y-%m-%d"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scanned": len(pool),
        "elapsed_sec": round(time.time() - t0, 1),
        "signals": signals,
    }
    with LOCK:
        json.dump(out, open(SIGNALS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        _last_scan.update(running=False, date=out["date"], ok=True,
                          msg=f"完成, {len(signals)} 只机会", count=len(signals))
    return {"ok": True, **{k: out[k] for k in ("date", "updated_at", "scanned", "elapsed_sec", "signals")}}


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
