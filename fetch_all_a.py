# -*- coding: utf-8 -*-
"""拉取全A列表(代码/名称/市值/行业)并落盘到 bt_data/"""
import urllib.request, json, time, os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bt_data")
os.makedirs(DATA, exist_ok=True)

def fetch_page(pn, pz=200):
    url = (f"https://push2delay.eastmoney.com/api/qt/clist/get?pn={pn}&pz={pz}&po=1&np=1&fltt=2&invt=2&fid=f20"
           "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f20,f100")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    for _ in range(3):
        try:
            return json.load(urllib.request.urlopen(req, timeout=15))
        except Exception as e:
            time.sleep(1.5)
    return None

def main():
    out = []
    pn = 1
    total = None
    while True:
        d = fetch_page(pn)
        if d is None:
            print("页请求失败:", pn); time.sleep(2); continue
        diff = d.get("data", {}).get("diff")
        if isinstance(diff, dict): diff = [diff]
        if not diff: break
        for it in diff:
            try: mv = float(it.get("f20"))
            except (TypeError, ValueError): mv = 0.0
            out.append({"code": str(it["f12"]), "name": it["f14"], "mv": mv, "ind": it.get("f100")})
        if total is None: total = d.get("data", {}).get("total", 0)
        print(f"  已拉取 {len(out)}/{total}")
        if len(out) >= total: break
        pn += 1
        time.sleep(0.25)
    json.dump(out, open(os.path.join(DATA, "all_a.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print("落盘 all_a.json:", len(out))

if __name__ == "__main__":
    main()
