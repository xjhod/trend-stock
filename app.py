# -*- coding: utf-8 -*-
"""趋势全景 · 股票分析 Flask 服务
接口: /api/stock /api/watchlist /api/highfit /api/search /api/backtest /api/layers
"""
import json
import os
import threading

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
    qmap = {q["code"]: q for q in quotes}
    ordered = []
    for c in codes:
        if c in qmap:
            ordered.append(qmap[c])
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

    kline_data = _kline_to_json(daily.tail(180))
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
    """立即扫描高适配池"""
    try:
        return jsonify(scan_daily.run_scan())
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
    """按市值门槛重建高适配池 + 行业指数（动态更新）。
    阈值可选: {threshold: 30/50/100}，默认用配置值。会覆盖 highfit_pool.json。"""
    data = request.get_json(silent=True) or {}
    threshold = data.get("threshold")
    if threshold is None:
        threshold = pool_manager.get_threshold()
    threshold = int(threshold)
    if threshold not in pool_manager.POOL_LIMIT:
        return jsonify({"ok": False, "msg": f"市值门槛仅支持 {sorted(pool_manager.POOL_LIMIT)} 亿"}), 400
    try:
        r = pool_manager.build_pool(threshold)
        pool_manager.set_threshold(threshold)
        return jsonify(r)
    except Exception as e:
        return jsonify({"ok": False, "msg": f"构建失败: {e}"}), 500


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
UPDATE_STATE = {"state": "idle", "msg": "", "progress": 0, "replaced": []}

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


@app.route("/")
def index():
    return app.send_static_file("index.html")


if __name__ == "__main__":
    import os as _os
    _HOST = _os.environ.get("STOCK_HOST", "127.0.0.1")
    print(f"趋势全景 启动: http://{_HOST}:5000")
    # 每日收盘后(15:35)自动扫描 + 启动时补扫
    scan_daily.schedule_daily(15, 35)
    threading.Timer(3, scan_daily.maybe_scan_on_startup).start()
    print("趋势全景股票分析服务启动: http://127.0.0.1:5000")
    app.run(host=_HOST, port=5000, debug=False, threaded=True)
