# -*- coding: utf-8 -*-
"""高适配池动态构建（池子参数化 + 动态更新）
====================================================================
把"高适配池"从写死的文件升级为可按市值门槛动态重建的模块：

  1. 市值门槛参数化：默认100亿；可选50/30亿，用户可在"推送设置"里调节
  2. 池子成员过滤（研究回测验证过的条件）：
       市值 ≥ 门槛（亿）
       近60日日均成交额 ≥ 1亿元（保证流动性，能真实成交）
       近60日年化波动率 0.10 ~ 0.70（波动适中，太妖/太死都不纳入形态研究）
       剔除 ST / *ST / 退市 / 次新（上市不足120日）
  3. 重建行业等权指数（bt_data/ind_idx_cache.json）：池内行业取市值Top15成员合成，
     供"大盘→行业→个股"层级趋势、行业门卫使用
  4. 动态更新 = （可选重拉全A列表） + 重筛池子 + 重建行业指数

回测实证：市值门槛从100亿放宽到30亿是收益的决定性改进（+19%→+356%），
池子越大越容易覆盖大牛股，但扫描耗时会增加，故按门槛限制候选池上限。
====================================================================
"""
import json, os, statistics, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from data_fetcher import get_kline
import notify

BASE = os.path.dirname(os.path.abspath(__file__))
ALL_A_FILE = os.path.join(BASE, "bt_data", "all_a.json")
IND_CACHE_FILE = os.path.join(BASE, "bt_data", "ind_idx_cache.json")
HIGHFIT_FILE = os.path.join(BASE, "highfit_pool.json")

# 市值门槛(亿) -> 候选池上限（按市值排序取前N，控制扫描与K线拉取耗时）
POOL_LIMIT = {100: 260, 50: 460, 30: 760}
DEFAULT_THRESHOLD = 100

_LOCK = threading.Lock()


def get_threshold():
    """读取用户配置的市值门槛（亿）"""
    try:
        v = int(notify.load_config().get("market", {}).get("pool_threshold", DEFAULT_THRESHOLD))
        return v if v in POOL_LIMIT else DEFAULT_THRESHOLD
    except Exception:
        return DEFAULT_THRESHOLD


def set_threshold(threshold):
    """保存市值门槛（亿）到配置"""
    try:
        t = int(threshold)
        t = t if t in POOL_LIMIT else DEFAULT_THRESHOLD
        cfg = notify.load_config()
        cfg.setdefault("market", {})["pool_threshold"] = t
        notify.save_config(cfg)
        return t
    except Exception:
        return DEFAULT_THRESHOLD


def _ensure_bt_data_dir():
    """确保 bt_data 目录存在"""
    d = os.path.join(BASE, "bt_data")
    os.makedirs(d, exist_ok=True)
    return d


def _load_all_a():
    _ensure_bt_data_dir()
    try:
        return json.load(open(ALL_A_FILE, encoding="utf-8"))
    except Exception:
        # 文件不存在，自动从网络拉取全A列表
        try:
            import fetch_all_a
            fetch_all_a.main()
            return json.load(open(ALL_A_FILE, encoding="utf-8"))
        except Exception:
            return []


def _stock_candidate(all_a, threshold):
    """按当前市值筛候选池（沪深A股，剔除ST/退市）"""
    thr = float(threshold) * 1e8
    out = []
    for x in all_a:
        code = str(x.get("code", ""))
        if len(code) != 6 or code[0] not in "603":
            continue
        name = str(x.get("name", ""))
        if "ST" in name.upper() or "退" in name or "PT" in name.upper():
            continue
        mv = x.get("mv") or 0
        if mv < thr:
            continue
        out.append({"code": code, "name": name, "ind": x.get("ind", ""), "mv": mv})
    out.sort(key=lambda r: -r["mv"])
    limit = POOL_LIMIT.get(int(threshold), POOL_LIMIT[DEFAULT_THRESHOLD])
    return out[:limit]


def _fetch_one(code):
    """拉日线并计算过滤特征。返回 (df, 特征dict) 或 None"""
    try:
        df = get_kline(code, "daily", 200, "qfq")
        if df is None or len(df) < 120:
            return None
        rows = df.to_dict("records")
        w60 = rows[-60:]
        amt60 = sum(float(r["close"]) * float(r.get("volume", 0)) for r in w60) / 60
        if amt60 < 1e8:
            return None
        c60 = [float(r["close"]) for r in w60]
        rets = [c60[i] / c60[i - 1] - 1 for i in range(1, len(c60))]
        if len(rets) < 20:
            return None
        vol60 = statistics.pstdev(rets) * 15.8  # 年化波动率近似
        if not (0.10 <= vol60 <= 0.70):
            return None
        feat = {"amt60": amt60, "vol60": vol60, "last": float(c60[-1])}
        return df, feat
    except Exception:
        return None


def build_pool(threshold=None, progress=None):
    """重建高适配池 + 行业指数。
    threshold: 市值门槛（亿），None 用配置值
    progress: 可选回调 fn(stage, msg) 用于前端展示进度
    返回 dict：{ok, threshold, pool_size, ind_count, skipped, elapsed}
    """
    t0 = time.time()
    threshold = int(threshold) if threshold else get_threshold()
    all_a = _load_all_a()
    cands = _stock_candidate(all_a, threshold)
    if progress:
        progress("filter", f"市值≥{threshold}亿候选 {len(cands)} 只，评估流动性/波动…")

    # 并行拉K线过滤
    klines = {}
    done = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(_fetch_one, c["code"]): c for c in cands}
        for fut in as_completed(futs):
            done += 1
            if done % 80 == 0 and progress:
                progress("filter", f"评估中 {done}/{len(cands)}…")
            try:
                r = fut.result()
            except Exception:
                r = None
            if r is not None:
                klines[futs[fut]["code"]] = r

    # 组装池子
    pool = []
    for c in cands:
        if c["code"] in klines:
            pool.append({"code": c["code"], "name": c["name"], "ind": c["ind"],
                         "mv": round(c["mv"], 0)})
    pool.sort(key=lambda r: -r["mv"])
    with _LOCK:
        with open(HIGHFIT_FILE, "w", encoding="utf-8") as f:
            json.dump(pool, f, ensure_ascii=False, indent=1)

    # 重建行业指数（池内行业市值Top15等权合成，用已拉K线）
    # 策略：保留原 ind_idx_cache 的全部历史行业指数（长历史），
    #       仅对"新池涉及但原文件缺失"的行业用新K线补建，避免行业门卫失数据。
    ind_cache = _merge_ind_cache(_build_ind_cache(pool, klines))
    with _LOCK:
        with open(IND_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(ind_cache, f, ensure_ascii=False)
    try:
        import layers
        layers._ind_cache = None  # 使缓存失效
    except Exception:
        pass
    return {"ok": True, "threshold": threshold, "pool_size": len(pool),
            "ind_count": len(ind_cache), "skipped": len(cands) - len(pool),
            "elapsed": round(time.time() - t0, 1)}


def _build_ind_cache(pool, klines):
    """对池内行业：取市值Top15成员，等权合成行业日线指数。
    klines: code -> (df, feat)，df 含 date/close
    返回 ind -> [ {date, close}, ... ]
    """
    ind_members = {}
    for r in pool:
        ind_members.setdefault(r["ind"], []).append(r)
    ind_cache = {}
    for ind, ms in ind_members.items():
        ms = sorted(ms, key=lambda r: -r["mv"])[:15]
        series = {}
        ok = 0
        for m in ms:
            r = klines.get(m["code"])
            if not r:
                continue
            df = r[0]
            try:
                for _, row in df.iterrows():
                    series.setdefault(str(row["date"]), []).append(float(row["close"]))
                ok += 1
            except Exception:
                continue
        if ok < 5:
            continue
        rows = [{"date": d, "close": sum(v) / len(v)}
                for d, v in series.items() if len(v) >= 5]
        rows.sort(key=lambda x: x["date"])
        if len(rows) >= 100:
            ind_cache[ind] = rows
    return ind_cache


def _merge_ind_cache(new_ind):
    """合并行业指数：以原 ind_idx_cache 为基础，仅补充新池中缺失的行业"""
    base = {}
    try:
        base = json.load(open(IND_CACHE_FILE, encoding="utf-8"))
    except Exception:
        base = {}
    for ind, rows in new_ind.items():
        if ind not in base and rows:
            base[ind] = rows
    return base


def pool_info():
    """各市值门槛候选数量（供前端展示，不拉K线）"""
    all_a = _load_all_a()
    info = {}
    for thr in sorted(POOL_LIMIT):
        n = sum(1 for x in all_a
                if len(str(x.get("code", ""))) == 6 and str(x["code"])[0] in "603"
                and "ST" not in str(x.get("name", "")).upper()
                and "退" not in str(x.get("name", ""))
                and (x.get("mv") or 0) >= thr * 1e8)
        info[str(thr)] = n
    try:
        with open(HIGHFIT_FILE, encoding="utf-8") as f:
            cur = len(json.load(f))
    except Exception:
        cur = 0
    return {"threshold": get_threshold(), "cand_counts": info, "current_pool": cur}
