# -*- coding: utf-8 -*-
"""新浪长历史全量拉取 → SQLite
方案B：高配股池(1626只)全量历史(约25年,带volume) + 按东财行业等权合成行业指数。
用法: python3 fetch_sina_history.py [--skip-stocks] [--limit N]
  --skip-stocks: 只合成行业指数（股票已拉完）
  --limit N:     只拉前N只（调试用）
断点续传：已入库的 code 自动跳过。
"""
import argparse, json, os, sqlite3, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.path.dirname(os.path.abspath(__file__))
HIGHFIT = os.path.join(BASE, "highfit_pool.json")
DB = os.path.join(BASE, "bt_data", "history.db")
U = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}


def to_sina(code):
    c = str(code)
    if c.startswith("6"):
        return "sh" + c
    if c.startswith(("0", "3")):
        return "sz" + c
    return ("bj" + c) if c.startswith(("4", "8")) else c


def fetch(code, datalen=6011):
    url = U + "?" + urllib.parse.urlencode({"symbol": code, "scale": 240, "ma": "no", "datalen": str(datalen)})
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers=H)
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read().decode("utf-8", "ignore")
            if raw.startswith("["):
                return json.loads(raw)
            return None
        except Exception:
            if i == 3:
                return None
            time.sleep(0.4)
    return None


def init_db(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS stock_daily(
        code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
        PRIMARY KEY(code, date))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS ind_daily(
        ind TEXT, date TEXT, close REAL, n REAL, PRIMARY KEY(ind, date))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS meta(
        k TEXT PRIMARY KEY, v TEXT)""")
    conn.commit()


def load_done(conn):
    try:
        return set(r[0] for r in conn.execute("SELECT DISTINCT code FROM stock_daily"))
    except Exception:
        return set()


def is_stale(mkt_sym="sh000001"):
    """数据库是否过期：db 指数最后日期 < 最新交易日（新浪实时取当日）。
    失败时保守返回 False（不误触发同步）。"""
    try:
        import sqlite3
        conn = sqlite3.connect(DB)
        last = conn.execute("SELECT MAX(date) FROM mkt_daily WHERE sym=?", (mkt_sym,)).fetchone()[0]
        conn.close()
        if not last:
            return True
        # 最新交易日：新浪实时K线（400根即可拿最后日期）
        url = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
        u = url + "?" + urllib.parse.urlencode({"symbol": mkt_sym, "scale": 240, "ma": "no", "datalen": "5"})
        req = urllib.request.Request(u, headers=H)
        with urllib.request.urlopen(req, timeout=15) as r:
            arr = json.loads(r.read().decode("utf-8", "ignore"))
        if not arr:
            return False
        latest = arr[-1]["day"]
        return last < latest
    except Exception:
        return False


def sync_stocks(limit=0, workers=10, cb=None):
    """全量拉取股票K线（幂等, INSERT OR REPLACE）。返回 (ok, fail)"""
    pool = json.load(open(HIGHFIT, encoding="utf-8"))
    if limit:
        pool = pool[:limit]
    conn = sqlite3.connect(DB)
    done = set(r[0] for r in conn.execute("SELECT DISTINCT code FROM stock_daily"))
    todo = [p for p in pool if str(p["code"]) not in done]
    if cb:
        cb(f"股票: 待拉 {len(todo)} / 已入库 {len(done)}")
    if not todo:
        conn.close()
        return 0, 0
    ok = fail = 0

    def one(p):
        arr = fetch(to_sina(p["code"]))
        if not arr:
            return (p, None)
        rows = [(str(p["code"]), r["day"], float(r["open"]), float(r["high"]),
                 float(r["low"]), float(r["close"]), float(r.get("volume", 0) or 0)) for r in arr]
        return (p, rows)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for p, rows in ex.map(one, todo):
            if rows:
                try:
                    conn.executemany("INSERT OR REPLACE INTO stock_daily VALUES(?,?,?,?,?,?,?)", rows)
                    conn.commit()
                    ok += 1
                except Exception:
                    fail += 1
            else:
                fail += 1
    conn.close()
    return ok, fail


def sync_inds(cb=None):
    """行业等权合成（全量重算, 幂等）。返回行业数"""
    import sqlite3
    conn = sqlite3.connect(DB)
    pool = json.load(open(HIGHFIT, encoding="utf-8"))
    ind_of = {str(p["code"]): p["ind"] for p in pool}
    inds = sorted(set(ind_of.values()))
    if cb:
        cb(f"行业合成: {len(inds)} 个…")
    tot = 0
    for ind in inds:
        codes = [c for c in ind_of if ind_of[c] == ind]
        ph = ",".join("?" * len(codes))
        q = f"SELECT code, date, close FROM stock_daily WHERE code IN ({ph}) AND close IS NOT NULL AND close>0 ORDER BY date"
        rows = conn.execute(q, codes).fetchall()
        agg, cnt = {}, {}
        for code, date, close in rows:
            agg[date] = agg.get(date, 0) + close
            cnt[date] = cnt.get(date, 0) + 1
        data = [(ind, d, agg[d] / cnt[d], cnt[d]) for d in sorted(agg)]
        if data:
            conn.executemany("INSERT OR REPLACE INTO ind_daily VALUES(?,?,?,?)", data)
            conn.commit()
            tot += len(data)
    conn.execute("INSERT OR REPLACE INTO meta VALUES('last_updated', ?)", (time.strftime("%Y-%m-%d %H:%M:%S"),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES('stock_count', ?)", (str(len(pool)),))
    conn.commit()
    conn.close()
    return len(inds)


def sync_indices(cb=None):
    """三大指数入库（上证/深成/上证50）"""
    import sqlite3
    conn = sqlite3.connect(DB)
    for sym in ["sh000001", "sz399001", "sh000016"]:
        arr = fetch(sym)
        if not arr:
            continue
        rows = [(sym, r["day"], float(r["open"]), float(r["high"]), float(r["low"]),
                 float(r["close"]), float(r.get("volume", 0) or 0)) for r in arr]
        conn.executemany("INSERT OR REPLACE INTO mkt_daily VALUES(?,?,?,?,?,?,?)", rows)
        conn.commit()
    conn.close()
    return 3


def sync_all(limit=0, workers=10, cb=None):
    """一键全量同步：股票 → 指数 → 行业合成。幂等可重复执行。"""
    t0 = time.time()
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    init_db(conn)
    conn.close()
    ok, fail = sync_stocks(limit, workers, cb)
    sync_indices(cb)
    nind = sync_inds(cb)
    if cb:
        cb(f"完成: 股票+{ok}/失败{fail}, 行业{nind}个, 耗时{time.time()-t0:.0f}s")
    return ok, fail, nind


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-stocks", action="store_true")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    init_db(conn)
    conn.close()
    print(f"池: {len(json.load(open(HIGHFIT, encoding='utf-8')))} 只")
    if args.skip_stocks:
        sync_indices()
        sync_inds(lambda m: print(m))
    else:
        sync_all(args.limit, args.workers, lambda m: print(m))


if __name__ == "__main__":
    main()
