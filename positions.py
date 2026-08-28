# -*- coding: utf-8 -*-
"""我的持仓管理：用户手动登记真实持仓（买入日期/价格/数量）。
设计原则（用户确认）：
  - 推荐/今日机会 = 买入视角，面向无持仓者，不产生任何"卖出/止损"信号
  - 只有登记在"我的持仓"里的股票，才基于真实买入价/日期给出离场/卖出建议
离场建议（针对持仓股，基于真实买入价）：
  1. 移动止损: 现价 <= 买入以来最高收盘 * 0.90 -> 建议止损卖出
  2. 破线+大盘转弱双确认: 现价跌破MA20 且 大盘转弱 -> 建议离场
  3. 其余: 持有
"""
import json, os, threading
from datetime import datetime

from data_fetcher import get_kline
import layers

BASE = os.path.dirname(os.path.abspath(__file__))
POS_FILE = os.path.join(BASE, "my_positions.json")
LOCK = threading.Lock()


def _load():
    try:
        return json.load(open(POS_FILE, encoding="utf-8"))
    except Exception:
        return {"positions": []}


def _save(d):
    with LOCK:
        json.dump(d, open(POS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _find_name(code):
    """尽量从高适配池/机会列表里找名称"""
    try:
        pool = json.load(open(os.path.join(BASE, "highfit_pool.json"), encoding="utf-8"))
        for it in pool:
            if it.get("code") == code:
                return it.get("name", "")
    except Exception:
        pass
    try:
        import scan_daily
        for it in scan_daily.load_signals().get("signals", []):
            if it["code"] == code:
                return it.get("name", "")
    except Exception:
        pass
    return ""


def add_position(code, buy_date, buy_price, qty=0):
    code = str(code).strip()
    d = _load()
    for p in d["positions"]:
        if p["code"] == code:
            return {"ok": False, "msg": "该股票已在持仓中"}
    name = _find_name(code)
    d["positions"].append({
        "code": code, "name": name,
        "buy_date": str(buy_date or "")[:10],
        "buy_price": float(buy_price or 0),
        "qty": int(qty or 0),
    })
    _save(d)
    return {"ok": True, "msg": f"已登记持仓 {name or code}"}


def remove_position(code):
    d = _load()
    before = len(d["positions"])
    d["positions"] = [p for p in d["positions"] if p["code"] != str(code).strip()]
    _save(d)
    return {"ok": True, "msg": "已删除" if len(d["positions"]) < before else "未找到该持仓"}


def _in_position(codes):
    d = _load()
    return {p["code"]: p for p in d["positions"] if p["code"] in codes}


def refresh():
    """拉最新行情, 为每只持仓计算现价/收益/离场建议。返回持仓列表+汇总"""
    d = _load()
    if not d["positions"]:
        return {"ok": True, "positions": [], "summary": {}}
    mkt_rows = layers.get_market_kline(300)
    mkt_dir = layers._direction([r["close"] for r in mkt_rows]) if mkt_rows else "unknown"
    out = []
    for p in d["positions"]:
        item = dict(p)
        try:
            df = get_kline(p["code"], "daily", 300, "")
            if df is None or len(df) < 30:
                item["status"] = "数据不足"; out.append(item); continue
            rows = df.to_dict("records")
            closes = [r["close"] for r in rows]
            cur = float(closes[-1])
            # 买入日之后的部分（用于"买入以来最高"）
            if p.get("buy_date"):
                dates = [str(r["date"]) for r in rows]
                start = 0
                for i, dt in enumerate(dates):
                    if dt >= p["buy_date"]:
                        start = i
                        break
            else:
                start = 0
            high_since = max(closes[start:]) if start < len(closes) else cur
            ret = (cur / p["buy_price"] - 1) * 100 if p.get("buy_price") else 0
            item["cur_price"] = round(cur, 2)
            item["ret_pct"] = round(ret, 2)
            item["high_since"] = round(high_since, 2)
            # 离场建议
            advice, note = "持有", ""
            if p.get("buy_price") and cur <= high_since * 0.90:
                advice = "建议止损"
                note = f"自买入以来最高 {high_since} 回撤已超10%"
            else:
                # 破MA20 + 大盘转弱
                try:
                    ma20 = float(df["close"].iloc[-21:-1].mean())
                    if cur < ma20 and mkt_dir == "down":
                        advice = "建议离场"
                        note = f"跌破MA20({ma20:.2f}) + 大盘转弱"
                except Exception:
                    pass
            item["advice"] = advice
            item["note"] = note
            item["mkt_dir"] = mkt_dir
        except Exception as e:
            item["status"] = f"获取失败:{e}"
        out.append(item)
    wins = sum(1 for x in out if (x.get("ret_pct") or 0) > 0)
    sell = [x for x in out if x.get("advice") in ("建议止损", "建议离场")]
    summary = {
        "total": len(out),
        "wins": wins,
        "sell_count": len(sell),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mkt_dir": mkt_dir,
    }
    return {"ok": True, "positions": out, "summary": summary}


def positions_dict():
    """code -> position dict（供 /api/stock 判断是否持仓）"""
    return {p["code"]: p for p in _load()["positions"]}


def stock_position_info(code, daily_df=None):
    """给 /api/stock 用：返回该股持仓信息 + 离场建议（不在持仓则 in_position=False）"""
    d = _load()
    for p in d["positions"]:
        if p["code"] == str(code).strip():
            info = {
                "in_position": True,
                "buy_date": p.get("buy_date", ""),
                "buy_price": p.get("buy_price", 0),
                "qty": p.get("qty", 0),
                "advice": "持有",
                "note": "",
                "ret_pct": None,
                "cur_price": None,
            }
            if daily_df is not None and len(daily_df) >= 30 and p.get("buy_price"):
                try:
                    # 现价取不复权真实价（与用户成本口径一致）
                    cur = float(closes[-1]) if False else None
                    try:
                        _df_raw = get_kline(code, "daily", 5, "")
                        cur = float(_df_raw["close"].iloc[-1])
                    except Exception:
                        cur = float(daily_df["close"].iloc[-1])
                    rows = daily_df.to_dict("records")
                    closes = [r["close"] for r in rows]
                    if p.get("buy_date"):
                        dates = [str(r["date"]) for r in rows]
                        start = 0
                        for i, dt in enumerate(dates):
                            if dt >= p["buy_date"]:
                                start = i; break
                    else:
                        start = 0
                    high_since = max(closes[start:]) if start < len(closes) else cur
                    info["cur_price"] = round(cur, 2)
                    info["ret_pct"] = round((cur / p["buy_price"] - 1) * 100, 2)
                    if cur <= high_since * 0.90:
                        info["advice"] = "建议止损"
                        info["note"] = f"自买入以来最高 {round(high_since,2)} 回撤已超10%"
                    else:
                        try:
                            ma20 = float(daily_df["close"].iloc[-21:-1].mean())
                            mkt_rows = layers.get_market_kline(300)
                            mkt_dir = layers._direction([r["close"] for r in mkt_rows]) if mkt_rows else "unknown"
                            if cur < ma20 and mkt_dir == "down":
                                info["advice"] = "建议离场"
                                info["note"] = f"跌破MA20({round(ma20,2)}) + 大盘转弱"
                        except Exception:
                            pass
                except Exception:
                    pass
            return info
    return {"in_position": False}
