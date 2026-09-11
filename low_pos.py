# -*- coding: utf-8 -*-
"""低位机会（深熊底进攻信号）—— 大盘开关 × 个股标的 统一框架落地
============================================================
回测依据（池内135只, 2022-2026, 60日后）:
  大盘=深熊底 才激活本信号（逆周期工具，非进攻区不触发）：
    - 超跌反转组：个股=深熊底   (大盘深熊底×行业健康牛×个股深熊底 71%/+17.4%)
    - 领先股组  ：个股=健康牛   (大盘深熊底×行业健康牛×个股健康牛 62%/+4.7%)
  行业=健康牛 优先标星（行业层过滤）
  无行业状态数据时给两层参考（61%/+9.7%、61%/+4.2%）
"""
import json, os, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from data_fetcher import get_kline
import layers

BASE = os.path.dirname(os.path.abspath(__file__))
HIGHFIT_FILE = os.path.join(BASE, "highfit_pool.json")
CACHE_FILE = os.path.join(BASE, "lowpos_signals.json")
LOCK = threading.Lock()

_last = {"running": False, "msg": "", "done": 0, "total": 0, "ok": False}

# 60日参考（来自回测表；ind=True 为行业健康牛前提，ind=False 为两层前提）
REF = {
    "over": {"ind": {"win": 71, "avg": "+17.4%"}, "no_ind": {"win": 61, "avg": "+9.7%"}},
    "lead": {"ind": {"win": 62, "avg": "+4.7%"},  "no_ind": {"win": 61, "avg": "+4.2%"}},
}


def check_regime():
    """只算大盘状态（不扫描全池），供页面初始展示"""
    mkt = layers.get_market_kline(400)
    closes = [r["close"] for r in mkt]
    return layers.env_regime(closes)


def get_status():
    with LOCK:
        return dict(_last)


def _ind_regime(ind):
    """行业五态（缓存≥311根才可判）"""
    rows = layers._load_ind_cache().get(ind)
    if not rows or len(rows) < 311:
        return None
    closes = [r["close"] for r in rows]
    r = layers.env_regime(closes)
    return r["key"] if r else None


def _one(item):
    code = item["code"]
    try:
        df = get_kline(code, period="daily", limit=400, adjust="qfq")
        if df is None or len(df) < 311:
            return None
        closes = [float(x) for x in df["close"].tolist()]
        st = layers.env_regime(closes)
        if not st:
            return None
        key = st["key"]
        if key not in ("bear_bottom", "bull_strong"):
            return None  # 只保留超跌与领先两组
        ind = item.get("ind", "")
        ind_key = _ind_regime(ind)
        return {
            "code": code,
            "name": item.get("name", ""),
            "ind": ind,
            "stk_key": key,
            "stk_state": st["state"],
            "days_above60": st["days_above60"],
            "ind_key": ind_key,
            "ind_healthy": ind_key == "bull_strong",
            "is_lead": key == "bull_strong",
        }
    except Exception:
        return None


def run_scan(limit=None, workers=6):
    """扫高适配池生成低位机会信号。大盘非深熊底时不触发（返回当前状态）。"""
    with LOCK:
        if _last["running"]:
            return {"ok": False, "msg": "扫描进行中"}
    # 大盘状态（进攻区判断）
    mkt = layers.get_market_kline(400)
    closes = [r["close"] for r in mkt]
    regime = layers.env_regime(closes)
    if not regime:
        return {"ok": False, "msg": "大盘数据不足，无法判断环境"}
    out = {
        "ok": True,
        "date": time.strftime("%Y-%m-%d"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "regime": regime,
        "active": regime["key"] == "bear_bottom",
        "over": [], "lead": [],
        "scanned": 0, "elapsed_sec": 0,
    }
    if regime["key"] != "bear_bottom":
        # 非进攻区：不触发（逆周期工具，牛市不强行找低位）
        with LOCK:
            json.dump(out, open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return out
    try:
        pool = json.load(open(HIGHFIT_FILE, encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "msg": f"高适配池读取失败: {e}"}
    if limit:
        pool = pool[:limit]
    with LOCK:
        _last.update(running=True, done=0, total=len(pool), msg=f"扫描 {len(pool)} 只高适配股…")
    results = []
    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_one, it) for it in pool]
            for f in as_completed(futs):
                try:
                    r = f.result()
                except Exception:
                    r = None
                with LOCK:
                    _last["done"] += 1
                if r:
                    results.append(r)
    finally:
        with LOCK:
            _last.update(running=False, ok=True, msg="完成")
    over = [r for r in results if not r["is_lead"]]
    lead = [r for r in results if r["is_lead"]]
    # 排序：行业健康牛优先；超跌组按站上年线天数少(跌得深)优先，领先组按天数多(趋势强)优先
    over.sort(key=lambda r: (0 if r["ind_healthy"] else 1, r["days_above60"]))
    lead.sort(key=lambda r: (0 if r["ind_healthy"] else 1, -r["days_above60"]))
    for r in over:
        ref = REF["over"]["ind"] if r["ind_healthy"] else REF["over"]["no_ind"]
        r["ref"] = ref
    for r in lead:
        ref = REF["lead"]["ind"] if r["ind_healthy"] else REF["lead"]["no_ind"]
        r["ref"] = ref
    out.update(over=over, lead=lead, scanned=len(pool),
               elapsed_sec=round(time.time() - t0, 1))
    with LOCK:
        json.dump(out, open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return out


def run_scan_async(limit=None, workers=6):
    if _last["running"]:
        return {"ok": False, "msg": "扫描进行中"}
    threading.Thread(target=run_scan, args=(limit, workers), daemon=True).start()
    return {"ok": True, "msg": "已开始后台扫描低位机会"}


def load_cache():
    try:
        return json.load(open(CACHE_FILE, encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    lim = int(sys.argv[1]) if len(sys.argv) > 1 else None
    r = run_scan(limit=lim, workers=6)
    print(json.dumps({k: v for k, v in r.items() if k != "over" and k != "lead"}, ensure_ascii=False))
    print("超跌:", len(r.get("over", [])), "领先:", len(r.get("lead", [])))
