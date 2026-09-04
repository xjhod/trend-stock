# -*- coding: utf-8 -*-
"""趋势全景 · 股票分析 Flask 服务
接口: /api/stock /api/watchlist /api/highfit /api/search /api/backtest /api/layers
"""
import json
import os
import threading
import time

# 后端代码版本（与 VERSION 文件保持同步；硬编码便于前端显示后端进程实际加载的版本）
_BACKEND_VERSION = "1.9.8"

import pandas as pd
from flask import Flask, jsonify, request

import analysis as an
import backtest as bt
import data_fetcher as df
import layers
import scan_daily
import notify
import updater
import paper_trade
import positions
import pool_manager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BASE_DIR, "watchlist.json")
HIGHFIT_FILE = os.path.join(BASE_DIR, "highfit_pool.json")
DEFAULT_WATCHLIST = ["600519", "000001", "300750", "601318", "000858"]

app = Flask(__name__, static_folder="static", static_url_path="")
_lock = threading.Lock()
POOL_BUILD_STATE = {"state": "idle", "msg": "", "progress": 0, "result": None}
INDUSTRY_STOCKS_CACHE = {}

# ---------------------------------------------------------------
# 工具
# ---------------------------------------------------------------
def _safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _robust_kline(code, period="daily", limit=300):
    return df.get_kline(code, period=period, limit=limit)


def _load_watchlist():
    try:
        with open(WATCHLIST_FILE, encoding="utf-8") as f:
            codes = json.load(f)
        if isinstance(codes, list) and codes:
            return codes
    except Exception:
        pass
    return list(DEFAULT_WATCHLIST)


def _save_watchlist(codes):
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(codes, f, ensure_ascii=False, indent=2)


def _lookup_name(code):
    try:
        q = df.get_realtime_quotes([code])
        if q:
            return q[0].get("name", "")
    except Exception:
        pass
    return ""


def _kline_to_json(kdf):
    if kdf is None or len(kdf) == 0:
        return []
    out = []
    for _, r in kdf.iterrows():
        out.append({
            "date": r["date"],
            "open": _f(r["open"]), "close": _f(r["close"]),
            "high": _f(r["high"]), "low": _f(r["low"]),
            "volume": _f(r.get("volume", 0)),
            "pct_chg": _f(r.get("pct_chg", 0)),
        })
    return out


def _fund_to_json(ffdf):
    if ffdf is None or len(ffdf) == 0:
        return []
    out = []
    for _, r in ffdf.iterrows():
        out.append({
            "date": r["date"],
            "main_net": _f(r.get("main_net", 0)),
            "main_pct": _f(r.get("main_pct", 0)),
        })
    return out


def _fin_to_json(fin):
    if fin is None or len(fin) == 0:
        return []
    out = []
    for _, r in fin.iterrows():
        out.append({
            "date": str(r.get("REPORT_DATE", "")),
            "rev_yoy": _f(r.get("TOTALOPERATEREVETZ")),
            "np_yoy": _f(r.get("PARENTNETPROFITTZ")),
            "gm": _f(r.get("XSMLL")),
            "revenue": _f(r.get("TOTALOPERATEREVE")),
            "net_profit": _f(r.get("PARENTNETPROFIT")),
        })
    return out


def _f(v):
    try:
        fv = float(v)
        if fv != fv:
            return None
        return round(fv, 4)
    except Exception:
        return None


# ---------------------------------------------------------------
# 自选股
# ---------------------------------------------------------------
@app.route("/api/watchlist", methods=["GET"])
def api_watchlist():
    codes = _load_watchlist()
    quotes = df.get_realtime_quotes(codes)
    def _norm(c):
        c = str(c).lower()
        for p in ("sh", "sz", "bj"):
            if c.startswith(p):
                return c[2:]
        return c
    qmap = {_norm(q["code"]): q for q in quotes}
    ordered = []
    for c in codes:
        nc = _norm(c)
        if nc in qmap:
            q = dict(qmap[nc])
            q["code"] = c  # 保留原始code（带前缀的指数）
            ordered.append(q)
    return jsonify({"ok": True, "items": ordered})


@app.route("/api/watchlist", methods=["POST"])
def api_watchlist_add():
    body = request.get_json(silent=True) or {}
    keyword = str(body.get("code", "")).strip()
    if not keyword:
        return jsonify({"ok": False, "msg": "请输入股票代码或名称"}), 400
    if keyword.isdigit():
        code = keyword
        name = _lookup_name(code)
        if not name:
            hits = df.search_stocks(code)
            if not hits:
                return jsonify({"ok": False, "msg": f"未找到股票 {code}"}), 404
            code = hits[0]["code"]
            name = hits[0]["name"]
    else:
        hits = df.search_stocks(keyword)
        if not hits:
            return jsonify({"ok": False, "msg": f"未找到股票：{keyword}"}), 404
        code = hits[0]["code"]
        name = hits[0]["name"]
    with _lock:
        codes = _load_watchlist()
        if code not in codes:
            codes.append(code)
            _save_watchlist(codes)
    return jsonify({"ok": True, "code": code, "name": name, "msg": f"已添加 {name}({code})"})


@app.route("/api/watchlist/<code>", methods=["DELETE"])
def api_watchlist_del(code):
    with _lock:
        codes = _load_watchlist()
        if code in codes:
            codes.remove(code)
            _save_watchlist(codes)
    return jsonify({"ok": True, "msg": "已删除"})


@app.route("/api/watchlist/import", methods=["POST"])
def api_watchlist_import():
    """从同花顺导出的自选股文本批量导入。支持：600519 / SH600519 / 600519.SH / 600519 贵州茅台"""
    import re
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text", "")
    added = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.search(r"(?:[SHBJ]|sh|sz|bj)[A-Za-z]*(\d{6})", line)
        cand = m.group(1) if m else None
        if not cand:
            nums = re.findall(r"\d{6}", line)
            cand = nums[0] if nums else None
        if cand and cand[0] in "03684" and cand not in added:
            added.append(cand)
    with _lock:
        codes = _load_watchlist()
        new = 0
        for c in added:
            if c not in codes:
                codes.append(c)
                new += 1
        _save_watchlist(codes)
    return jsonify({"ok": True, "added": added, "new": new, "watchlist": codes})


# ---------------------------------------------------------------
# 高适配池（按行业分组）
# ---------------------------------------------------------------
@app.route("/api/highfit", methods=["GET"])
def api_highfit():
    try:
        with open(HIGHFIT_FILE, encoding="utf-8") as f:
            pool = json.load(f)
    except Exception:
        return jsonify({"ok": False, "msg": "高适配池未生成"}), 500
    codes = [x["code"] for x in pool]
    quotes = df.get_realtime_quotes(codes)
    qmap = {q["code"]: q for q in quotes}
    groups = []
    for ind in sorted({x["ind"] for x in pool}):
        items = []
        for x in pool:
            if x["ind"] != ind:
                continue
            q = qmap.get(x["code"]) or {}
            items.append({
                "code": x["code"], "name": x["name"], "ind": ind, "mv": x["mv"],
                "price": q.get("price"), "pct_chg": q.get("pct_chg"),
                "pre_close": q.get("pre_close"), "volume": q.get("volume"), "turnover": q.get("turnover"),
                "pe": q.get("pe"), "pb": q.get("pb"), "total_mv": q.get("total_mv"),
            })
        if items:
            groups.append({"ind": ind, "count": len(items), "items": items})
    return jsonify({"ok": True, "total": len(codes), "groups": groups})


# ---------------------------------------------------------------
# 搜索
# ---------------------------------------------------------------
@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"ok": True, "items": []})
    hits = df.search_stocks(q, limit=10)
    return jsonify({"ok": True, "items": [{"code": h["code"], "name": h["name"]} for h in hits]})


# ---------------------------------------------------------------
# 个股分析
# ---------------------------------------------------------------
@app.route("/api/stock/<code>")
def api_stock(code):
    code = str(code).strip()
    market = df.guess_market(code)

    import concurrent.futures as cf

    def _fetch_daily():
        return _safe_call(lambda: _robust_kline(code, "daily", 300), pd.DataFrame())
    def _fetch_weekly():
        return _safe_call(lambda: _robust_kline(code, "weekly", 120), pd.DataFrame())
    def _fetch_monthly():
        return _safe_call(lambda: _robust_kline(code, "monthly", 80), pd.DataFrame())
    def _fetch_ff():
        return _safe_call(lambda: df.get_fund_flow(code, limit=60), pd.DataFrame())
    def _fetch_fin():
        return _safe_call(lambda: df.get_financials(code, market, limit=8), pd.DataFrame())
    def _fetch_quote():
        return _safe_call(lambda: df.get_realtime_quotes([code]), [])

    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        f_daily = ex.submit(_fetch_daily)
        f_weekly = ex.submit(_fetch_weekly)
        f_monthly = ex.submit(_fetch_monthly)
        f_ff = ex.submit(_fetch_ff)
        f_fin = ex.submit(_fetch_fin)
        f_quote = ex.submit(_fetch_quote)
        # 每个请求加超时上限(8s)：防止某数据源挂起导致整个页面无限等待
        daily = _safe_call(lambda: f_daily.result(timeout=8), pd.DataFrame())
        weekly = _safe_call(lambda: f_weekly.result(timeout=8), pd.DataFrame())
        monthly = _safe_call(lambda: f_monthly.result(timeout=8), pd.DataFrame())
        ff = _safe_call(lambda: f_ff.result(timeout=8), pd.DataFrame())
        fin = _safe_call(lambda: f_fin.result(timeout=8), pd.DataFrame())
        quotes = _safe_call(lambda: f_quote.result(timeout=8), [])
    quote = quotes[0] if quotes else {}

    trends = {
        "daily": an.analyze_trend(daily, "日线"),
        "weekly": an.analyze_trend(weekly, "周线"),
        "monthly": an.analyze_trend(monthly, "月线"),
    }
    tech = an.calc_indicators(daily)
    fund = an.analyze_fund_flow(ff)
    fundamentals = an.analyze_fundamentals(fin)
    conclusion = an.generate_conclusion(trends, tech, fund, fundamentals, quote)

    kline_data = _kline_to_json(daily.tail(120))
    kline_weekly = _kline_to_json(weekly.tail(120))
    kline_monthly = _kline_to_json(monthly.tail(60))
    fund_data = _fund_to_json(ff.tail(60))

    # 层级趋势（大盘 -> 行业 -> 个股）
    layer_info = _safe_call(
        lambda: layers.analyze_layers(code, daily),
        {"market": {"name": "上证指数", "direction": "unknown"},
         "industry": {"name": "", "direction": "unknown"},
         "stock": {"trendline": "none"},
         "resonance_bull": False, "resonance_bear": False})
    # 离场状态（破线+大盘转弱双确认 / 破线后10%止损）
    exit_info = _safe_call(
        lambda: layers.analyze_exit(code, daily),
        {"holding": False, "state": "no_position", "desc": "数据不足", "entry": None})
    layer_info["exit"] = exit_info

    # 该股是否被"今日机会"推荐（买入视角）—— 被推荐的股票不应显示"规避"
    in_scan = None
    scan_detail = None
    try:
        for _s in scan_daily.load_signals().get("signals", []):
            if _s.get("code") == code:
                in_scan = _s.get("type", "trend")
                scan_detail = {
                    "type": in_scan,
                    "level": _s.get("level", 1),
                    "pats": _s.get("pats", []),
                    "tags": _s.get("tags", []),
                    "dd60": _s.get("dd60"),
                    "rating": _s.get("rating", ""),
                }
                break
    except Exception:
        pass

    # 该股是否在"我的持仓"（只有持仓股才有卖出提示）
    pos_info = _safe_call(lambda: positions.stock_position_info(code, daily), {"in_position": False})

    return jsonify({
        "ok": True,
        "code": code,
        "in_scan": in_scan,
        "scan_detail": scan_detail,
        "position": pos_info,
        "quote": quote,
        "trends": trends,
        "tech": tech,
        "fund": fund,
        "fundamentals": fundamentals,
        "conclusion": conclusion,
        "kline": kline_data,
        "kline_weekly": kline_weekly,
        "kline_monthly": kline_monthly,
        "fund_flow": fund_data,
        "financials": _fin_to_json(fin),
        "layers": layer_info,
    })


# ---------------------------------------------------------------
# 回测
# ---------------------------------------------------------------
@app.route("/api/backtest/<code>")
def api_backtest(code):
    code = str(code).strip()
    dk = _safe_call(lambda: _robust_kline(code, "daily", 1200), pd.DataFrame())
    if dk.empty or len(dk) < 200:
        return jsonify({"ok": False, "error": "历史数据不足"})
    rows = bt._rows_from_df(dk)
    res = bt.backtest_rows(rows)
    summ = bt.summarize(res)
    result = {it["key"]: {"rate": it["rate"], "hit": it["hit"], "total": it["total"]} for it in summ["items"]}
    return jsonify({
        "ok": True,
        "code": code,
        "name": _lookup_name(code),
        "n": len(dk),
        "range": f"{dk.iloc[0]['date']} ~ {dk.iloc[-1]['date']}",
        "result": result,
    })


# ---------------------------------------------------------------
# 形态可靠性测试：按具体形态统计出现后 3/6/10 日走势
# ---------------------------------------------------------------
@app.route("/api/pattern_test/<code>")
def api_pattern_test(code):
    code = str(code).strip()
    dk = _safe_call(lambda: _robust_kline(code, "daily", 1200), pd.DataFrame())
    if dk.empty or len(dk) < 200:
        return jsonify({"ok": False, "error": "历史数据不足"})
    rows = bt._rows_from_df(dk)
    res = bt.pattern_test_rows(rows)
    return jsonify({
        "ok": True,
        "code": code,
        "name": _lookup_name(code),
        "n": len(dk),
        "range": f"{dk.iloc[0]['date']} ~ {dk.iloc[-1]['date']}",
        "horizons": res["horizons"],
        "patterns": res["patterns"],
        "summary": res["summary"],
    })


# ---------------------------------------------------------------
# 层级趋势单独接口
# ---------------------------------------------------------------
@app.route("/api/layers/<code>")
def api_layers(code):
    code = str(code).strip()
    dk = _safe_call(lambda: _robust_kline(code, "daily", 300), pd.DataFrame())
    info = layers.analyze_layers(code, dk)
    return jsonify({"ok": True, "code": code, "layers": info})


# ---------------------------------------------------------------
# 静态
# ---------------------------------------------------------------
@app.route("/api/scan/run", methods=["POST"])
def api_scan_run():
    """立即扫描高适配池（后台异步执行，前端轮询进度）"""
    try:
        return jsonify(scan_daily.run_scan_async())
    except Exception as e:
        return jsonify({"ok": False, "msg": f"扫描异常: {e}"}), 500


@app.route("/api/scan/status")
def api_scan_status():
    d = scan_daily.load_signals()
    d["status"] = scan_daily.scan_status()
    return jsonify(d)


@app.route("/api/config", methods=["GET"])
def api_config_get():
    return jsonify(notify.load_config())


@app.route("/api/config", methods=["POST"])
def api_config_set():
    data = request.get_json(force=True) or {}
    cfg = notify.load_config()
    if "wechat" in data:
        w = cfg["wechat"]
        for k in ("enabled", "provider", "token"):
            if k in data["wechat"]:
                w[k] = data["wechat"][k]
        cfg["wechat"] = w
    if "sms" in data:
        cfg["sms"].update(data["sms"])
    if "market" in data:
        m = cfg["market"]
        for k in ("mode", "threshold", "min_pos", "pool_threshold"):
            if k in data["market"]:
                m[k] = data["market"][k]
        cfg["market"] = m
    notify.save_config(cfg)
    return jsonify({"ok": True})
# ---------------- 高适配池（参数化 + 动态更新） ----------------
@app.route("/api/pool/info")
def api_pool_info():
    return jsonify(pool_manager.pool_info())
@app.route("/api/pool/build", methods=["POST"])
def api_pool_build():
    """按市值门槛异步重建高适配池 + 行业指数（后台线程执行，立即返回）。
    阈值可选: {threshold: 30/50/100}，默认用配置值。会覆盖 highfit_pool.json。"""
    global POOL_BUILD_STATE
    data = request.get_json(silent=True) or {}
    threshold = data.get("threshold")
    if threshold is None:
        threshold = pool_manager.get_threshold()
    threshold = int(threshold)
    if threshold not in pool_manager.POOL_LIMIT:
        return jsonify({"ok": False, "msg": f"市值门槛仅支持 {sorted(pool_manager.POOL_LIMIT)} 亿"}), 400
    if POOL_BUILD_STATE["state"] == "running":
        return jsonify({"ok": True, "started": False, "running": True, "msg": "已有重建任务在进行中"})
    def _job():
        global POOL_BUILD_STATE
        try:
            POOL_BUILD_STATE = {"state": "running", "msg": "正在从全A股筛选高适配股票…", "progress": 5, "result": None}
            def _progress(stage, msg):
                POOL_BUILD_STATE["msg"] = msg
                # 从msg中提取进度，如"评估中 80/4500…"
                import re as _re
                _m = _re.search(r'(\d+)/(\d+)', msg or "")
                if _m:
                    POOL_BUILD_STATE["progress"] = min(95, int(int(_m.group(1)) / int(_m.group(2)) * 90) + 5)
                else:
                    POOL_BUILD_STATE["progress"] = 10
            r = pool_manager.build_pool(threshold, progress=_progress)
            pool_manager.set_threshold(threshold)
            POOL_BUILD_STATE = {"state": "done", "msg": "重建完成", "progress": 100, "result": r}
        except Exception as e:
            POOL_BUILD_STATE = {"state": "error", "msg": f"构建失败: {e}", "progress": 0, "result": None}
    threading.Thread(target=_job, daemon=True).start()
    return jsonify({"ok": True, "started": True, "msg": "已开始重建高适配池（约1-3分钟），请等待进度完成"})


@app.route("/api/pool/progress")
def api_pool_progress():
    """查询高适配池重建进度"""
    return jsonify(POOL_BUILD_STATE)


@app.route("/api/env", methods=["GET"])
def api_env():
    """当前市场环境评分与决策(供前端展示, 辅助用户牛熊判断)"""
    try:
        import env_judge
        d = env_judge.env_action()
        try:
            style, style_note = env_judge.exit_style()
            d["style"] = style
            d["exit_note"] = style_note
        except Exception:
            pass
        return jsonify(d)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route("/api/config/test", methods=["POST"])
def api_config_test():
    title = "趋势全景 · 推送测试"
    content = "推送配置成功！收到此消息说明微信/短信推送可用。\n(可关闭此推送或修改配置)"
    res = {"wechat": notify.send_wechat(title, content),
           "sms": notify.send_sms(title, content)}
    return jsonify(res)


@app.route("/api/update/check")
def api_update_check():
    """检查更新源是否有新版本"""
    return jsonify(updater.check_update())


# ---------------- 在线更新（异步后台执行，避免下载时阻塞服务导致浏览器卡死） ----------------
UPDATE_STATE = {"state": "idle", "msg": "", "progress": 0, "replaced": [], "restart": False}


def _auto_restart():
    """更新完成后自动重启：删除PID锁 -> 拉起新进程 -> 退出旧进程。
    避免用户每次都要手动关窗口再重新双击启动。"""
    import subprocess
    import sys
    _lock = os.path.join(BASE_DIR, ".app.pid")
    try:
        os.remove(_lock)
    except Exception:
        pass
    try:
        if os.name == "nt":
            # Windows：开新控制台窗口运行（与双击启动体验一致）
            subprocess.Popen([sys.executable, "app.py"], cwd=BASE_DIR,
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NEW_CONSOLE,
                             close_fds=True)
        else:
            # 非 Windows（如测试沙箱）：后台分离运行
            subprocess.Popen([sys.executable, "app.py"], cwd=BASE_DIR,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    except Exception:
        pass
    os._exit(0)

def _start_async_update(url):
    UPDATE_STATE["state"] = "running"
    UPDATE_STATE["progress"] = 5
    UPDATE_STATE["msg"] = "开始更新…"
    UPDATE_STATE["replaced"] = []
    def _job():
        try:
            UPDATE_STATE["progress"] = 15
            UPDATE_STATE["msg"] = "正在下载更新包（视网络约 10-90 秒，期间不影响其他功能）…"
            result = updater.apply_update(url)
            if result.get("ok"):
                UPDATE_STATE["state"] = "done"
                UPDATE_STATE["progress"] = 100
                UPDATE_STATE["msg"] = result.get("msg", "更新完成")
                UPDATE_STATE["replaced"] = result.get("replaced", [])
                UPDATE_STATE["restart"] = True
                # 3 秒后自动重启（让前端有时间提示用户）
                threading.Timer(3, _auto_restart).start()
            else:
                UPDATE_STATE["state"] = "error"
                UPDATE_STATE["msg"] = result.get("msg", "更新失败")
        except Exception as e:  # noqa
            UPDATE_STATE["state"] = "error"
            UPDATE_STATE["msg"] = "更新异常：" + str(e)
    threading.Thread(target=_job, daemon=True).start()

@app.route("/api/update/apply", methods=["POST"])
def api_update_apply():
    """下载并替换代码文件（后台线程执行，立即返回，不阻塞服务）"""
    data = request.get_json(force=True) or {}
    url = data.get("download", "")
    if UPDATE_STATE["state"] == "running":
        return jsonify({"ok": True, "started": True, "running": True})
    _start_async_update(url)
    return jsonify({"ok": True, "started": True})


@app.route("/api/update/progress")
def api_update_progress():
    """查询后台更新进度"""
    return jsonify(UPDATE_STATE)


@app.route("/api/restart", methods=["POST"])
def api_restart():
    """手动触发自动重启（备用：正常情况下更新完成后会自动重启）"""
    threading.Timer(1, _auto_restart).start()
    return jsonify({"ok": True, "msg": "正在自动重启…"})


# ---------------- 模拟持仓 ----------------
@app.route("/api/paper", methods=["GET"])
def api_paper():
    """模拟持仓状态（含今日刷新）"""
    return jsonify(paper_trade.status())


@app.route("/api/paper/import", methods=["POST"])
def api_paper_import():
    """从今日机会创建模拟持仓（覆盖重建）"""
    entry_date = (request.get_json(silent=True) or {}).get("entry_date")
    sigs = scan_daily.load_signals().get("signals", [])
    if not sigs:
        return jsonify({"ok": False, "msg": "今日机会为空"})
    d = paper_trade.create_from_signals(sigs, entry_date)
    return jsonify({"ok": True, "holdings": len(d["holdings"]), "capital": d["capital"]})


@app.route("/api/paper/refresh", methods=["POST"])
def api_paper_refresh():
    """手动刷新一次: 更新行情 + 执行离场规则"""
    r = paper_trade.refresh()
    return jsonify(r)


# ---------------- 我的持仓 ----------------
@app.route("/api/positions", methods=["GET"])
def api_positions():
    """我的持仓列表（含最新行情/收益/离场建议）"""
    return jsonify(positions.refresh())


@app.route("/api/positions", methods=["POST"])
def api_positions_add():
    """登记持仓: {code, buy_date, buy_price, qty}"""
    data = request.get_json(silent=True) or {}
    r = positions.add_position(data.get("code", ""), data.get("buy_date", ""),
                               data.get("buy_price", 0), data.get("qty", 0))
    return jsonify(r)


@app.route("/api/positions/<code>", methods=["DELETE"])
def api_positions_del(code):
    return jsonify(positions.remove_position(code))


@app.route("/api/version")
def api_version():
    """返回后端实际运行的版本号（用于前端展示，便于排查后端是否已更新）"""
    return jsonify({"ok": True, "version": _BACKEND_VERSION, "app": "趋势全景"})


@app.route("/")
def index():
    return app.send_static_file("index.html")


# ---------------- 行业轮动（行业趋势看板） ----------------
def _calc_ind_trend(rows):
    """计算行业趋势状态和强度（B标准：收盘>MA20 + MA20上行 + MA5>MA10）"""
    if not rows or len(rows) < 25:
        return "unknown", 0, 0, 0, 0
    closes = [r["close"] for r in rows]
    cur = closes[-1]
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    ma20_prev = sum(closes[-21:-1]) / 20 if len(closes) >= 21 else ma20
    ma20_slope = (ma20 - ma20_prev) / ma20_prev * 100 if ma20_prev else 0
    ret20 = (cur / closes[-20] - 1) * 100 if len(closes) >= 20 else 0
    is_up = cur > ma20 and ma20 > ma20_prev and ma5 > ma10
    is_down = cur < ma20 and ma20 < ma20_prev and ma5 < ma10
    if is_up:
        direction = "up"
    elif is_down:
        direction = "down"
    else:
        direction = "sideways"
    score = 0
    if direction == "up":
        score += min(ma20_slope * 50, 30)
        score += min((ma5 / ma10 - 1) * 200, 20) if ma10 else 0
        score += min(max(ret20, 0), 25)
        score += 25
        if ret20 > 30:
            score -= min((ret20 - 30) * 0.5, 15)
    elif direction == "down":
        score = -abs(ma20_slope) * 30 - 10
    strength = "strong" if score >= 50 else ("medium" if score >= 30 else "weak")
    return direction, strength, round(score, 1), round(ma20_slope, 3), round(ret20, 1)


@app.route("/api/industry/trend")
def api_industry_trend():
    """行业趋势看板：返回趋势向上的前20个行业，按强度排序"""
    ind_cache = layers._load_ind_cache()
    try:
        with open(os.path.join(BASE_DIR, "highfit_pool.json"), encoding="utf-8") as f:
            pool = json.load(f)
    except Exception:
        pool = []
    results = []
    for ind_name, rows in ind_cache.items():
        if not ind_name or ind_name == "未知" or len(rows) < 25:
            continue
        direction, strength, score, ma20_slope, ret20 = _calc_ind_trend(rows)
        count = sum(1 for s in pool if s.get("ind") == ind_name)
        results.append({
            "name": ind_name, "direction": direction, "strength": strength,
            "score": score, "ma20_slope": ma20_slope, "ret20": ret20,
            "stock_count": count, "latest_close": round(rows[-1]["close"], 2),
        })
    # 排序：up 排前面（按score降序），sideways 中间，down 排后面
    dir_rank = {"up": 0, "sideways": 1, "down": 2}
    results.sort(key=lambda x: (dir_rank.get(x["direction"], 3), -x["score"]))
    total_up = sum(1 for r in results if r["direction"] == "up")
    return jsonify({"ok": True, "total_up": total_up, "total_ind": len(results), "items": results})


@app.route("/api/industry/<ind_name>/stocks")
def api_industry_stocks(ind_name):
    """返回指定行业内全部高适配股票（有强信号的排前面，没信号的也显示）。
    并行拉取K线+信号检测，结果缓存10分钟。"""
    import urllib.parse
    ind_name = urllib.parse.unquote(ind_name)
    # 缓存命中（10分钟）
    _c = INDUSTRY_STOCKS_CACHE.get(ind_name)
    if _c and time.time() - _c[0] < 600:
        return jsonify(_c[1])
    try:
        with open(os.path.join(BASE_DIR, "highfit_pool.json"), encoding="utf-8") as f:
            pool = json.load(f)
    except Exception:
        return jsonify({"ok": False, "msg": "高适配池加载失败"}), 500
    ind_stocks = [s for s in pool if s.get("ind") == ind_name]
    if not ind_stocks:
        return jsonify({"ok": True, "industry": ind_name, "items": []})
    total_in_ind = len(ind_stocks)
    stocks = ind_stocks[:50]

    # 行业指数近30日序列（用于与个股走势对比）。
    # 关键修复：历史行业指数缓存口径过时（旧复权/旧成分），与个股 qfq 不一致，
    # 导致行业橙线系统性虚高、个股蓝线被压低。这里改用「当前市值Top15成分股
    # qfq K线」实时等权合成，与个股 spark_stock 完全同口径，对比才真实。
    ind_spark_cache = {}
    try:
        _ind_top = sorted([s for s in pool if s.get("ind") == ind_name],
                          key=lambda s: -s.get("mv", 0))[:15]
        _series = {}
        for _m in _ind_top:
            try:
                _dk = df.get_kline(_m.get("code"), "daily", 40, "qfq")
                if _dk is None or len(_dk) < 31:
                    continue
                for _, _row in _dk.iterrows():
                    _series.setdefault(str(_row["date"]), []).append(float(_row["close"]))
            except Exception:
                continue
        if _series:
            _rows = sorted([{"date": d, "close": sum(v) / len(v)}
                            for d, v in _series.items() if len(v) >= 5], key=lambda x: x["date"])
            ind_spark_cache = {r["date"]: r["close"] for r in _rows[-40:]}
    except Exception:
        pass
    ind_dates = sorted(ind_spark_cache.keys())

    def _ind_series(daily):
        """个股近30日close + 按日期对齐的实时行业指数close（同口径，缺失用最近前值）"""
        d30 = daily.tail(30)
        dates = d30["date"].tolist()
        stock = [float(x) for x in d30["close"].tolist()]
        ind = []
        last_val = None
        for dt in dates:
            val = ind_spark_cache.get(dt)
            if val is None:
                for rd in reversed(ind_dates):
                    if rd <= dt:
                        val = ind_spark_cache.get(rd)
                        if val is not None:
                            break
            if val is not None:
                last_val = val
            ind.append(round(last_val, 4) if last_val is not None else None)
        return stock, ind

    def _process(s):
        code = s.get("code")
        try:
            daily = df.get_kline(code, "daily", 120, "qfq")
            if daily is None or len(daily) < 30:
                return {
                    "code": code, "name": s.get("name"), "price": None,
                    "change_pct": None, "direction": "unknown", "strength": "weak",
                    "signals": [], "pats": [], "has_signal": False,
                    "spark_stock": [], "spark_ind": [],
                }
            pats = scan_daily.detect_bullish(daily)
            strong_pats = [p for p in pats if p[2]]
            trend = an.analyze_trend(daily, "日线")
            direction = trend.get("direction", "sideways")
            cur_price = float(daily.iloc[-1]["close"])
            signals = []
            if strong_pats:
                signals.append("强形态:" + strong_pats[0][0])
            if direction == "up" and trend.get("strength") == "strong":
                signals.append("上升趋势")
            if len(daily) >= 5:
                recent_high = max(daily["high"].tolist()[-20:])
                if cur_price >= recent_high * 0.99 and float(daily.iloc[-2]["close"]) < recent_high:
                    signals.append("突破阻力")
            sk, ik = _ind_series(daily)
            return {
                "code": code, "name": s.get("name"), "price": round(cur_price, 2),
                "change_pct": round((cur_price / float(daily.iloc[-2]["close"]) - 1) * 100, 2),
                "direction": direction, "strength": trend.get("strength", "weak"),
                "signals": signals, "pats": [p[0] for p in pats[:3]],
                "has_signal": len(signals) > 0,
                "spark_stock": sk, "spark_ind": ik,
            }
        except Exception:
            return {
                "code": code, "name": s.get("name"), "price": None,
                "change_pct": None, "direction": "unknown", "strength": "weak",
                "signals": [], "pats": [], "has_signal": False,
            }

    # 并行处理：最多12线程
    from concurrent.futures import ThreadPoolExecutor
    items = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(stocks)))) as ex:
        for it in ex.map(_process, stocks):
            items.append(it)
    # 排序：有信号的排前面（按信号数量降序），没信号的排后面
    items.sort(key=lambda x: (len(x["signals"]) > 0, len(x["signals"])), reverse=True)
    with_signal = sum(1 for it in items if it["has_signal"])
    result = {"ok": True, "industry": ind_name, "total_in_ind": total_in_ind,
              "with_signal": with_signal, "items": items[:50]}
    INDUSTRY_STOCKS_CACHE[ind_name] = (time.time(), result)
    return jsonify(result)


if __name__ == "__main__":
    import os as _os
    import socket as _socket
    _HOST = _os.environ.get("STOCK_HOST", "127.0.0.1")
    print(f"趋势全景 启动: http://{_HOST}:5000")
    # 单实例保护（PID锁文件 + 端口检测双保险）：
    # 防止重复启动导致双实例叠加 CPU/内存占用。
    _LOCK_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".app.pid")
    def _pid_alive(pid):
        try:
            _os.kill(pid, 0)  # 发送信号0仅探测存活
            return True
        except (OSError, ProcessLookupError):
            return False
        except Exception:
            return False
    _already_running = False
    # 1) PID 锁文件
    try:
        if _os.path.exists(_LOCK_FILE):
            with open(_LOCK_FILE, encoding="utf-8") as _lf:
                _old_pid = int(_lf.read().strip())
            if _pid_alive(_old_pid):
                _already_running = True
    except Exception:
        pass
    # 2) 端口探测
    if not _already_running:
        try:
            _probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            _probe.settimeout(0.5)
            _probe.connect((_HOST, 5000))
            _probe.close()
            _already_running = True
        except (OSError, Exception):
            pass
    if _already_running:
        print("检测到趋势全景已在运行，本次不再重复启动（避免双实例占用CPU）。")
        print("如需重启，请先双击 stop.bat 或关闭已运行的程序窗口，再重新打开。")
        _os._exit(0)
    # 写入当前 PID
    try:
        with open(_LOCK_FILE, "w", encoding="utf-8") as _lf:
            _lf.write(str(_os.getpid()))
    except Exception:
        pass
    # 自动确保大盘指数在自选股中（上证指数/深证成指）
    _wl = _load_watchlist()
    _added = False
    for _idx in ("sh000001", "sz399001"):
        if _idx not in _wl:
            _wl.append(_idx)
            _added = True
    if _added:
        _save_watchlist(_wl)
        print("已自动添加上证指数/深证成指到自选股")
    # 每日收盘后(15:35)自动扫描 + 启动时补扫
    scan_daily.schedule_daily(15, 35)
    threading.Timer(60, scan_daily.maybe_scan_on_startup).start()  # 延迟60秒扫描, 避免刚启动就看盘时CPU被吃满
    print("趋势全景股票分析服务启动: http://127.0.0.1:5000")
    # 优先使用 waitress（生产级服务器，比 Flask 开发服务器省 CPU）；未安装则回退 Flask
    try:
        from waitress import serve
        print("使用 waitress 服务器（更省CPU）")
        try:
            serve(app, host=_HOST, port=5000, threads=8)
        finally:
            try:
                if _os.path.exists(_LOCK_FILE):
                    _os.remove(_LOCK_FILE)
            except Exception:
                pass
    except ImportError:
        print("未安装 waitress，使用 Flask 内置服务器")
        try:
            app.run(host=_HOST, port=5000, debug=False, threaded=True)
        finally:
            try:
                if _os.path.exists(_LOCK_FILE):
                    _os.remove(_LOCK_FILE)
            except Exception:
                pass
