# -*- coding: utf-8 -*-
"""模拟持仓跟踪（纸上交易）
用途: 将"今日机会"虚拟买入, 每日跟踪, 按既定离场规则决定卖出, 20个交易日后结算。

离场规则（复用用户实证规则, 针对模拟持仓逐日执行）:
  1. 移动止损: 当前收盘 <= 买入以来最高收盘 * 0.90  -> 卖出（回撤10%止盈/止损）
  2. 到期离场: 持有满20个交易日 -> 卖出
  3. 双确认离场: 个股破线(跌破买入时上升趋势线) 且 大盘转弱 -> 卖出
  4. 趋势完好则继续持有
每只票虚拟资金固定 10 万元, 买入价 = 导入日收盘价。
"""
import json, os, time, threading
from datetime import datetime, date

from data_fetcher import get_kline
import layers
import analysis as an

BASE = os.path.dirname(os.path.abspath(__file__))
PAPER_FILE = os.path.join(BASE, "paper_trades.json")
LOCK = threading.Lock()
PER_STOCK = 100000  # 每只10万虚拟资金

EMPTY = {
    "created_at": "",
    "entry_date": "",
    "capital": 0,
    "holdings": [],      # 当前持仓
    "closed": [],        # 已卖出
    "summary": {},
}


def _load():
    try:
        return json.load(open(PAPER_FILE, encoding="utf-8"))
    except Exception:
        return dict(EMPTY)


def _save(d):
    with LOCK:
        json.dump(d, open(PAPER_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def create_from_signals(signals, entry_date=None):
    """从今日机会列表创建模拟持仓（全量虚拟买入）"""
    if not entry_date:
        entry_date = date.today().strftime("%Y-%m-%d")
    holdings = []
    for s in signals:
        holdings.append({
            "code": s["code"], "name": s["name"], "ind": s.get("ind", ""),
            "type": s.get("type", "trend"),
            "entry_date": entry_date,
            "entry_price": s.get("price", 0),
            "qty": int(PER_STOCK / s["price"]) if s.get("price") else 0,
            "cost": PER_STOCK,
            "high_since": s.get("price", 0),   # 买入以来最高收盘
            "cur_price": s.get("price", 0),
            "days": 0,
            "tags": s.get("tags", []),
            "status": "holding",   # holding / stop / expired / confirm / manual
            "exit_date": None, "exit_price": None, "ret": None, "reason": "",
        })
    d = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "entry_date": entry_date,
        "capital": len(holdings) * PER_STOCK,
        "holdings": holdings,
        "closed": [],
        "summary": {},
    }
    _save(d)
    return d


def refresh():
    """拉取最新行情, 逐日更新持仓, 执行离场规则。返回汇总"""
    d = _load()
    if not d.get("holdings"):
        return {"ok": False, "msg": "无模拟持仓"}
    mkt_rows = layers.get_market_kline(300)
    mkt_dir = layers._direction([r["close"] for r in mkt_rows]) if mkt_rows else "unknown"
    t = time.time()
    new_hold = []
    for h in d["holdings"]:
        h["days"] += 1
        try:
            df = get_kline(h["code"], "daily", 300, "qfq")
        except Exception:
            df = None
        if df is None or len(df) < 2:
            new_hold.append(h)  # 拉不到数据暂跳过
            continue
        last = df.iloc[-1]
        px = float(last["close"])
        h["cur_price"] = round(px, 2)
        # 更新买入以来最高收盘
        h["high_since"] = max(h["high_since"], px)
        # 离场判断（基于模拟持仓自身买入价, 不复用参考建仓的离场状态机）
        exit_price, reason = None, ""
        # 1. 移动止损: 从买入以来最高收盘回撤>=10% (止盈/止损兼顾)
        if h["high_since"] > 0 and px <= h["high_since"] * 0.90:
            exit_price, reason = px, f"移动止损(自最高回撤{(px/h['high_since']-1)*100:.1f}%)"
        # 2. 到期: 满20个交易日
        elif h["days"] >= 20:
            exit_price, reason = px, "20日到期离场"
        # 3. 双确认: 个股跌破MA20 + 大盘转弱
        else:
            try:
                c20 = float(df["close"].iloc[-21:-1].mean())
                broken = px < c20
                if broken and mkt_dir == "down":
                    exit_price, reason = px, "破MA20+大盘转弱双确认离场"
            except Exception:
                pass
        if exit_price is not None:
            ret = exit_price / h["entry_price"] - 1 if h["entry_price"] else 0
            h.update(status="closed", exit_date=date.today().strftime("%Y-%m-%d"),
                     exit_price=round(exit_price, 2), ret=round(ret * 100, 2), reason=reason)
            d["closed"].append(h)
        else:
            new_hold.append(h)
    d["holdings"] = new_hold
    d["summary"] = _summarize(d, mkt_dir)
    _save(d)
    return {"ok": True, "summary": d["summary"], "closed_today": [h for h in d["closed"] if h.get("exit_date") == date.today().strftime("%Y-%m-%d")]}


def _summarize(d, mkt_dir):
    """汇总: 市值/收益/胜率/状态分布"""
    hold = d.get("holdings", [])
    closed = d.get("closed", [])
    # 持仓市值: 用当前价? 简化按成本计算投入, 已实现收益用 closed
    total_cost = len(hold) * PER_STOCK + len(closed) * PER_STOCK
    realized = sum(h.get("ret", 0) / 100.0 * PER_STOCK for h in closed)
    # 持仓浮动按成本计0（每日更新时如需实时市值, 由前端拉行情; 此处记录状态）
    wins = sum(1 for h in closed if (h.get("ret") or 0) > 0)
    return {
        "mkt_dir": mkt_dir,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(hold) + len(closed),
        "holding": len(hold),
        "closed": len(closed),
        "wins": wins,
        "realized_ret_pct": round(realized / (len(closed) * PER_STOCK) * 100, 2) if closed else 0,
        "capital": d.get("capital", 0),
    }


def status():
    d = _load()
    d["summary"] = _summarize(d, "unknown")
    return d
