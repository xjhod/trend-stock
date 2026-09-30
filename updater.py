# -*- coding: utf-8 -*-
"""在线更新模块：从多个更新源（GitHub 直连 + 国内加速代理）检查新版本 -> 下载 -> 校验 -> 替换代码文件。
安全设计：
- 只更新「代码文件」（.py/.html/.js/.css/.txt/.md/.bat/.sh 及 VERSION）
- 用户数据文件（watchlist.json / daily_signals.json / highfit_pool.json /
  config.json / update_config.json / bt_data / research 缓存）一律保留
- 解压时做路径穿越防护（拒绝 .. 与绝对路径）
- 多源回退：raw.githubusercontent 直连不通时，自动尝试 ghfast.top / gh-proxy.com 等国内加速源
"""
import io
import json
import os
import re
import shutil
import zipfile
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(BASE_DIR, "VERSION")
CONFIG_FILE = os.path.join(BASE_DIR, "update_config.json")

# 默认更新源（发布后对应自己的仓库）
DEFAULT_CONFIG = {
    "owner": "xjhod",
    "repo": "trend-stock",
    "branch": "main",
    "sources": [
        {"name": "GitHub直连", "prefix": ""},
        {"name": "ghfast加速", "prefix": "https://ghfast.top/"},
        {"name": "ghproxy加速", "prefix": "https://gh-proxy.com/"},
        {"name": "ghproxy.net加速", "prefix": "https://ghproxy.net/"},
    ],
}

# 用户数据文件：绝不更新
DATA_FILES = {
    "watchlist.json", "daily_signals.json", "highfit_pool.json",
    "config.json", "update_config.json",
}
DATA_DIRS = ("bt_data", "research")


def current_version():
    """读取本地 VERSION 文件"""
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip() or "0.0.0"
    except Exception:
        return "0.0.0"


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    owner = str(cfg.get("owner") or DEFAULT_CONFIG["owner"]).strip()
    repo = str(cfg.get("repo") or DEFAULT_CONFIG["repo"]).strip()
    branch = str(cfg.get("branch") or DEFAULT_CONFIG["branch"]).strip()
    sources = cfg.get("sources") or DEFAULT_CONFIG["sources"]
    parsed = []
    for s in sources:
        if isinstance(s, dict):
            parsed.append({"name": str(s.get("name", "源")), "prefix": str(s.get("prefix", "")).strip()})
        else:
            parsed.append({"name": "源", "prefix": str(s).strip()})
    direct_url = str(cfg.get("direct_url", "") or "").strip()
    return {"owner": owner, "repo": repo, "branch": branch, "sources": parsed, "direct_url": direct_url}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _ver_tuple(v):
    """'1.3.0' -> (1,3,0)，比较版本大小"""
    parts = re.findall(r"\d+", str(v))
    return tuple(int(x) for x in parts[:3]) or (0,)


def _raw_url(cfg, kind):
    """kind: 'latest' -> raw latest.json; 'zip' -> github zip 下载地址"""
    o, r, b = cfg["owner"], cfg["repo"], cfg["branch"]
    if kind == "latest":
        return f"https://raw.githubusercontent.com/{o}/{r}/{b}/latest.json"
    return f"https://github.com/{o}/{r}/archive/refs/heads/{b}.zip"


def _bust(url):
    """给 raw 请求加时间戳参数，绕过 GitHub CDN 缓存（刚推送后立刻能查到新版）"""
    import time as _t
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}t={int(_t.time() * 1000)}"


def _zip_version_from_url(url, timeout=30):
    """从 zip 直链下载（内存中）并读取包内 VERSION，返回 (版本号, zip内容)。
    直链（如 aka.doubaocdn.com）下载快且无需 GitHub 域名，作为首选更新源。"""
    import requests
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"直链下载失败: {e}")
    data = r.content
    if not data:
        raise RuntimeError("直链下载内容为空")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise RuntimeError("直链内容不是有效zip")
    bad = zf.testzip()
    if bad:
        raise RuntimeError(f"直链更新包损坏: {bad}")
    names = zf.namelist()
    root = _zip_root(names)
    for name in names:
        if os.path.basename(name) == "VERSION":
            with zf.open(name) as f:
                ver = f.read().decode("utf-8", "ignore").strip()
            return ver, data
    raise RuntimeError("直链更新包内无VERSION")


def _should_update(rel):
    """是否是可更新的代码文件（数据文件一律保留）"""
    rel = rel.replace("\\", "/")
    base = os.path.basename(rel)
    if rel == "VERSION":
        return True
    if base in DATA_FILES:
        return False
    if any(rel == d or rel.startswith(d + "/") for d in DATA_DIRS):
        return False
    if base.endswith(".json"):
        return False  # 数据 json 一律不更新
    if base.endswith((".py", ".html", ".js", ".css", ".txt", ".md", ".bat", ".sh")):
        return True
    return False


def _zip_root(names):
    """GitHub 下载的 zip 通常带一层根目录（如 repo-main/），找出该前缀"""
    dirs = set()
    for n in names:
        parts = n.split("/")
        if len(parts) > 1 and parts[0]:
            dirs.add(parts[0])
    return (next(iter(dirs)) + "/") if len(dirs) == 1 else ""


def check_update():
    """GitHub latest.json 优先（固定路径, 可自动发现新版本）-> 直链兜底。
    返回：{ok, has_update, current, latest, note, download, source}"""
    import requests
    cfg = load_config()
    cur0 = current_version()
    tried = []
    last_err = None
    # 1) GitHub 版本清单（raw + jsdelivr 镜像, 取版本高者, 自动发现新版）
    for s in cfg["sources"]:
        prefix = str(s.get("prefix", ""))
        if not prefix:
            candidates = [
                _bust(f"https://raw.githubusercontent.com/{cfg['owner']}/{cfg['repo']}/{cfg['branch']}/latest.json"),
                _bust(f"https://cdn.jsdelivr.net/gh/{cfg['owner']}/{cfg['repo']}@{cfg['branch']}/latest.json"),
            ]
            metas = []
            for u in candidates:
                try:
                    rr = requests.get(u, timeout=8)
                    rr.raise_for_status()
                    j = rr.json()
                    if isinstance(j, str):
                        j = {"version": j.strip()}
                    if isinstance(j, dict) and j.get("version"):
                        metas.append(j)
                except Exception:
                    continue
            if metas:
                meta = max(metas, key=lambda m: _ver_tuple(str(m.get("version", "") or "")))
                latest = str(meta.get("version", "") or "").strip()
                has_update = bool(latest) and _ver_tuple(latest) > _ver_tuple(cur0)
                dl = str(meta.get("download", "") or "").strip()
                return {"ok": True, "has_update": has_update, "current": cur0, "latest": latest,
                        "note": str(meta.get("note", "") or ""),
                        "download": dl, "source": "GitHub(自动发现)"}
            tried.append(s["name"])
            continue
        # 代理源（ghfast/ghproxy）: 同样读 latest.json
        try:
            url = _bust(prefix + _raw_url(cfg, "latest"))
            r = requests.get(url, timeout=8)
            r.raise_for_status()
            j = r.json()
            if isinstance(j, str):
                j = {"version": j.strip()}
            if not (isinstance(j, dict) and j.get("version")):
                raise RuntimeError("清单格式异常")
            latest = str(j.get("version", "") or "").strip()
            has_update = bool(latest) and _ver_tuple(latest) > _ver_tuple(cur0)
            dl = str(j.get("download", "") or "").strip()
            return {"ok": True, "has_update": has_update, "current": cur0, "latest": latest,
                    "note": str(j.get("note", "") or ""),
                    "download": dl, "source": s["name"]}
        except Exception as e:
            last_err = e
            tried.append(s["name"])
            continue
    # 2) 兜底：直链(aka) 读自身 zip 内版本
    direct = str(cfg.get("direct_url", "") or "").strip()
    if direct:
        try:
            ver, _ = _zip_version_from_url(direct, timeout=25)
            has_up = bool(ver) and _ver_tuple(ver) > _ver_tuple(cur0)
            return {"ok": True, "has_update": has_up, "current": cur0, "latest": ver,
                    "note": "直连更新源(aka)", "download": direct, "source": "直连(aka)"}
        except Exception as e:
            last_err = f"直连源失败：{e}"
    return {"ok": False, "msg": f"检查更新失败（已尝试 {len(tried)} 个源：{'、'.join(tried)}）：{last_err}"}


def apply_update(download_url):
    """多源回退下载更新包 -> 校验 -> 只替换代码文件。返回 (ok, msg, replaced)"""
    import requests
    cfg = load_config()
    raw_zip = _raw_url(cfg, "zip")
    # 候选下载地址：download_url(清单指定的最新链接) 优先 -> 直链(aka) -> 国内加速源
    candidates = []
    if download_url:
        candidates.append(download_url)
    direct = str(cfg.get("direct_url", "") or "").strip()
    if direct:
        candidates.append(direct)
    for s in cfg["sources"]:
        if s["prefix"]:
            candidates.append(s["prefix"] + raw_zip)
    candidates = list(dict.fromkeys(candidates))  # 去重保序
    if not candidates:
        return {"ok": False, "msg": "缺少下载地址"}
    last_err = None
    for url in candidates:
        try:
            r = requests.get(url, timeout=40)
            r.raise_for_status()
            data = r.content
            if not data:
                last_err = "下载内容为空"
                continue
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                last_err = "下载内容不是有效的zip"
                continue
            bad = zf.testzip()
            if bad:
                last_err = f"更新包损坏：{bad}"
                continue
            names = zf.namelist()
            root = _zip_root(names)
            replaced = []
            skipped = []
            for name in names:
                if name.endswith("/"):
                    continue
                if any(seg == ".." for seg in name.replace("\\", "/").split("/")):
                    skipped.append(name)
                    continue
                rel = os.path.relpath(name, root) if root else name
                rel = rel.replace("\\", "/")
                if rel.startswith("..") or os.path.isabs(rel):
                    skipped.append(rel)
                    continue
                if not _should_update(rel):
                    continue
                target = os.path.join(BASE_DIR, rel)
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with zf.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                replaced.append(rel)
            return {"ok": True, "msg": f"更新完成，已更新 {len(replaced)} 个文件", "replaced": replaced}
        except Exception as e:
            last_err = e
            continue
    return {"ok": False, "msg": f"更新失败（{len(candidates)}个源均不可用）：{last_err}"}
