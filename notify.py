# -*- coding: utf-8 -*-
"""推送适配器: 扫描出机会时主动推送
渠道:
  1) 微信 - Server酱 (https://sct.ftqq.com)  填 SENDKEY 即可推送到微信
  2) 微信 - PushPlus (https://www.pushplus.plus)  填 token 即可
  3) 短信 - 预留 (需用户提供短信服务商密钥, 默认关闭)
配置保存在 config.json, 由前端配置界面写入。
"""
import json, os, urllib.request, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE, "config.json")

DEFAULT_CONFIG = {
    "wechat": {"enabled": False, "provider": "serverchan", "token": ""},
    "sms": {"enabled": False, "provider": "aliyun", "access_key": "", "access_secret": "",
            "sign_name": "", "template": "", "phone": ""},
}


def load_config():
    try:
        d = json.load(open(CONFIG_FILE, encoding="utf-8"))
    except Exception:
        d = {}
    merged = DEFAULT_CONFIG.copy()
    for k in DEFAULT_CONFIG:
        if isinstance(DEFAULT_CONFIG[k], dict):
            merged[k].update(d.get(k) or {})
        else:
            merged[k] = d.get(k, DEFAULT_CONFIG[k])
    return merged


def save_config(cfg):
    json.dump(cfg, open(CONFIG_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _http_get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:
        return f"ERR:{e}"


def _http_post(url, data, timeout=10):
    try:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode("utf-8"),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:
        return f"ERR:{e}"


def send_wechat(title, content):
    """微信推送。provider: serverchan / pushplus"""
    cfg = load_config()["wechat"]
    if not cfg.get("enabled") or not cfg.get("token"):
        return "微信未启用或未填 token"
    token = cfg["token"].strip()
    provider = cfg.get("provider", "serverchan")
    if provider == "pushplus":
        return _http_post("https://www.pushplus.plus/send",
                          {"token": token, "title": title, "content": content, "template": "txt"})
    # Server酱 (sct.ftqq.com)
    url = f"https://sctapi.ftqq.com/{token}.send?" + urllib.parse.urlencode({"title": title, "desp": content})
    return _http_get(url)


def send_sms(title, content):
    """短信(预留): 需要短信服务商密钥与 SDK。当前返回提示, 不实际发送。"""
    return "短信推送需配置短信服务商(如阿里云短信)密钥, 当前为预留接口。"


def build_message(signals):
    """把信号列表组合成推送内容。仅推送 level>=2 的强机会。"""
    strong = [s for s in signals if s.get("level", 1) >= 2]
    if not strong:
        return "今日无机会", "高适配池扫描完成, 今日未发现符合条件的强机会(三层共振/放量形态)。"
    date = signals[0] if False else ""
    lines = []
    for s in strong:
        tags = "、".join(s.get("tags", []))
        stars = "★" * s.get("level", 1)
        lines.append(f"{s['name']}({s['code']}) {s['price']} {s['change_pct']:+.2f}%  {stars} {tags}")
    title = f"趋势全景 · 今日机会 {len(strong)} 只"
    content = "\n".join(lines)
    content += "\n\n—— 点击软件首页「今日机会」查看单股图形 ——"
    return title, content


def notify_signals(signals):
    """扫描完成后推送。返回各渠道结果。"""
    if not signals:
        return {"wechat": "无信号, 不推送", "sms": "无信号, 不推送"}
    title, content = build_message(signals)
    result = {}
    cfg = load_config()
    if cfg["wechat"].get("enabled"):
        result["wechat"] = send_wechat(title, content)
    else:
        result["wechat"] = "未启用"
    if cfg["sms"].get("enabled"):
        result["sms"] = send_sms(title, content)
    else:
        result["sms"] = "未启用"
    return result
