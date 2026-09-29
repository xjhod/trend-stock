# -*- coding: utf-8 -*-
"""批量抓取高适配池股票的营收/净利同比增速（新浪财务指标页）。
将 rev_g(营收增速%) / np_g(净利增速%) 写回 highfit_pool.json。
用法: python3 fetch_fundamentals.py [--limit N] [--workers 8]
"""
import json, os, re, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
POOL_FILE = os.path.join(BASE, "highfit_pool.json")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://finance.sina.com.cn/"}

def _fetch_page(code, year):
    url = (f"https://money.finance.sina.com.cn/corp/go.php/vFD_FinancialGuideLine/"
           f"stockid/{code}/ctrl/{year}/displaytype/4.phtml")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("gb2312", "ignore")

def _parse_growth(html):
    """解析主营业务收入增长率/净利润增长率：最新报告期优先, 取首个非'--'值"""
    idx = html.find("报告日期")
    if idx < 0:
        return None, None
    seg = html[idx:idx + 30000]
    txt = re.sub(r"<[^>]+>", "|", seg)
    txt = re.sub(r"\|+", "|", txt)
    pos = txt.find("成长能力")
    if pos < 0:
        return None, None
    part = txt[pos:pos + 3000]
    m1 = re.search(r"主营业务收入增长率\(%\)\|([^|]+)", part)
    m2 = re.search(r"净利润增长率\(%\)\|([^|]+)", part)
    def _first(v):
        if v is None:
            return None
        parts = v.split("|")
        for p in parts:
            p = p.strip()
            if p and p != "--":
                try:
                    return round(float(p), 2)
                except Exception:
                    continue
        return None
    return _first(m1.group(1) if m1 else None), _first(m2.group(1) if m2 else None)

def get_fundamental(code):
    for year in (2026, 2025):
        try:
            html = _fetch_page(code, year)
            rev, np_ = _parse_growth(html)
            if rev is not None or np_ is not None:
                return rev, np_, year
            time.sleep(0.5)
        except Exception:
            time.sleep(1.0)
    return None, None, None

def main():
    import sys
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 8
    pool = json.load(open(POOL_FILE, encoding="utf-8"))
    todo = pool[:limit] if limit else pool
    done = {"ok": 0, "miss": 0}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(get_fundamental, s["code"]): s for s in todo}
        for fut in as_completed(futs):
            s = futs[fut]
            rev, np_, yr = fut.result()
            if rev is not None or np_ is not None:
                s["rev_g"] = rev
                s["np_g"] = np_
                s["fg_year"] = yr
                done["ok"] += 1
            else:
                done["miss"] += 1
            done["count"] = done.get("count", 0) + 1
            if done["count"] % 100 == 0:
                el = time.time() - t0
                print(f"进度 {done['count']}/{len(todo)}  ok={done['ok']} miss={done['miss']} 耗时{el:.0f}s", flush=True)
    json.dump(pool, open(POOL_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    both = sum(1 for s in pool if (s.get("rev_g") or -999) > 0 and (s.get("np_g") or -999) > 0)
    miss = sum(1 for s in pool if s.get("rev_g") is None)
    print(f"完成: 共{len(pool)}只, 有数据{done['ok']}, 缺失{done['miss']}, 营收净利双正{both}只, 耗时{time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
