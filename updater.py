# -*- coding: utf-8 -*-
"""在线更新模块：从 GitHub 更新源检查新版本 -> 下载 -> 校验 -> 替换代码文件。

安全设计：
- 只更新「代码文件」（.py/.html/.js/.css/.txt/.md/.bat/.sh 及 VERSION）
- 用户数据文件（watchlist.json / daily_signals.json / highfit_pool.json /
  config.json / update_config.json / bt_data / research 缓存）一律保留
- 解压时做路径穿越防护（拒绝 .. 与绝对路径）
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

# 更新源模板（发布到 GitHub 后填写自己的仓库地址）
DEFAULT_CONFIG = {"update_url": ""}

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
        return {"update_url": str(cfg.get("update_url", "")).strip()}
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _ver_tuple(v):
    """'1.3.0' -> (1,3,0)，比较版本大小"""
    parts = re.findall(r"\d+", str(v))
    return tuple(int(x) for x in parts[:3]) or (0,)


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
    """请求更新源上的 latest.json，返回版本对比结果"""
    cfg = load_config()
    url = cfg.get("update_url", "").strip()
    if not url:
        return {"ok": False, "msg": "未配置更新源。请在 update_config.json 里填写 GitHub 仓库的 latest.json 地址。"}
    try:
        import requests
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        meta = r.json()
        cur = current_version()
        latest = str(meta.get("version", "")).strip()
        has_update = bool(latest) and _ver_tuple(latest) > _ver_tuple(cur)
        return {
            "ok": True,
            "has_update": has_update,
            "current": cur,
            "latest": latest,
            "note": meta.get("note", ""),
            "download": meta.get("download", ""),
        }
    except Exception as e:
        return {"ok": False, "msg": f"检查更新失败：{e}"}


def apply_update(download_url):
    """下载更新包 -> 校验 -> 只替换代码文件。返回 (ok, msg, replaced)"""
    if not download_url:
        return {"ok": False, "msg": "缺少下载地址"}
    try:
        import requests
        r = requests.get(download_url, timeout=90)
        r.raise_for_status()
        data = r.content
        if not data:
            return {"ok": False, "msg": "下载内容为空"}

        zf = zipfile.ZipFile(io.BytesIO(data))
        bad = zf.testzip()
        if bad:
            return {"ok": False, "msg": f"更新包损坏：{bad}"}
        names = zf.namelist()
        root = _zip_root(names)
        replaced = []
        skipped = []
        for name in names:
            if name.endswith("/"):
                continue
            # 路径穿越防护：拒绝任何含 .. 路径段的原始路径
            if any(seg == ".." for seg in name.replace("\\", "/").split("/")):
                skipped.append(name)
                continue
            if root:
                rel = os.path.relpath(name, root)
            else:
                rel = name
            rel = rel.replace("\\", "/")
            if rel.startswith("..") or os.path.isabs(rel):
                skipped.append(rel)
                continue  # 二次防护
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
        return {"ok": False, "msg": f"更新失败：{e}"}
