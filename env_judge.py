# -*- coding: utf-8 -*-
"""市场环境评分引擎 + 市场模式(牛熊判断)控制
====================================================================
用户可调"市场模式", 自己判断当前牛熊后选用不同策略:

  mode = auto  自动: 程序用环境评分自动判断(高分=好环境, 低分=观望)
  mode = bull  进取(用户判断牛市): 用"动态仓位"(方案1)
               环境差也保留最低仓位(min_pos), 不错过行情; 环境好加仓
  mode = bear  稳健(用户判断熊市): 用"环境过滤"(方案2)
               环境评分 < 阈值 则完全不推荐, 空仓避损

环境评分 0-6 分, 四因子:
  +2 大盘趋势(up/side/down)
  +1 大盘位置(距250日高回撤在 -15% ~ -1.5% 的健康区)
  +1 市场广度代理(行业缓存中近20日上涨行业占比>55% 且 近60日新高占比>6%)
  +1 大盘动量(20日涨幅在 0~6% 的健康上涨)
====================================================================
"""
import layers
import notify

DEFAULT_THRESHOLD = 4   # 环境评分阈值(稳健/自动模式低于此值则过滤)
DEFAULT_MIN_POS = 30    # 进取模式最低仓位%


# ---------------------------------------------------------------
# 环境评分
# ---------------------------------------------------------------
def env_score(asof=None):
    """返回 (score 0-6, det 明细dict) ; 数据不足返回 (None, None)
    asof: 历史回测用(截至该日数据); 默认用最新数据
    广度代理: 用行业缓存(127行业指数)算"近20日上涨占比+60日新高占比",
    运行时只读缓存文件, 不额外拉取任何数据, 秒级返回。
    """
    rows = layers.get_market_kline(400)
    if asof:
        rows = [r for r in rows if r["date"] <= asof]
    closes = [r["close"] for r in rows]
    if len(closes) < 260:
        return None, None
    c = closes[-1]
    # 1. 大盘趋势
    d = layers._direction(closes)
    s_trend = {"up": 2, "side": 1, "down": 0}[d]
    # 2. 大盘位置(距250日高)
    hi250 = max(closes[-250:])
    dd250 = (c / hi250 - 1) * 100
    s_pos = 1 if -15 <= dd250 <= -1.5 else 0
    # 3. 市场广度代理(行业缓存)
    ind_cache = layers._load_ind_cache()
    up_cnt = tot = new_cnt = 0
    for irows in ind_cache.values():
        if asof:
            irows = [r for r in irows if r["date"] <= asof]
        ic = [r["close"] for r in irows]
        if len(ic) < 65:
            continue
        tot += 1
        if ic[-1] > ic[-21]:
            up_cnt += 1
        if ic[-1] >= max(ic[-60:]):
            new_cnt += 1
    if tot == 0:
        return None, None
    b_up = up_cnt / tot * 100
    b_new = new_cnt / tot * 100
    s_breadth = 1 if (b_up > 55 and b_new > 6) else 0
    # 4. 大盘动量
    mom20 = (c / closes[-21] - 1) * 100
    s_mom = 1 if 0 <= mom20 <= 6 else 0
    score = s_trend + s_pos + s_breadth + s_mom
    det = {"trend": d, "s_trend": s_trend, "dd250": round(dd250, 1), "s_pos": s_pos,
           "b_up": round(b_up, 0), "b_new": round(b_new, 1), "s_breadth": s_breadth,
           "mom20": round(mom20, 1), "s_mom": s_mom, "score": score,
           "asof": (asof or "latest")}
    return score, det


# ---------------------------------------------------------------
# 用户配置
# ---------------------------------------------------------------
def get_mode():
    """市场模式: auto / bull(进取-牛市) / bear(稳健-熊市)"""
    try:
        return notify.load_config().get("market", {}).get("mode", "auto")
    except Exception:
        return "auto"


def get_threshold():
    try:
        return float(notify.load_config().get("market", {}).get("threshold", DEFAULT_THRESHOLD))
    except Exception:
        return DEFAULT_THRESHOLD


def get_min_pos():
    try:
        return float(notify.load_config().get("market", {}).get("min_pos", DEFAULT_MIN_POS))
    except Exception:
        return DEFAULT_MIN_POS


def position_pct(score):
    """动态仓位(方案1): 评分0-6 → 仓位 min_pos%~100%"""
    minp = get_min_pos()
    return max(minp, min(100, score / 6.0 * 100))


# ---------------------------------------------------------------
# 按模式决策
# ---------------------------------------------------------------
def env_action(asof=None):
    """返回决策 dict:
      action: hold_buy(允许/按仓位) / filter_out(过滤/空仓)
      score, det, mode, threshold, pos_pct, note
    """
    score, det = env_score(asof)
    mode = get_mode()
    th = get_threshold()
    base = {"score": score, "det": det, "mode": mode, "threshold": th,
            "min_pos": get_min_pos()}
    if score is None:
        return {**base, "action": "hold_buy", "pos_pct": 100,
                "note": "数据不足, 默认放行"}
    if mode == "bear":  # 稳健(熊市): 环境过滤
        if score >= th:
            return {**base, "action": "hold_buy", "pos_pct": position_pct(score),
                    "note": f"稳健模式: 环境评分{score}≥{th}, 按动态仓位 {position_pct(score):.0f}%"}
        return {**base, "action": "filter_out", "pos_pct": 0,
                "note": f"稳健模式: 环境评分{score}<{th}, 建议空仓观望"}
    if mode == "bull":  # 进取(牛市): 动态仓位, 永不清仓
        return {**base, "action": "hold_buy", "pos_pct": position_pct(score),
                "note": f"进取模式: 动态仓位 {position_pct(score):.0f}% (最低{get_min_pos():.0f}%)"}
    # auto: 自动判断
    if score >= th:
        return {**base, "action": "hold_buy", "pos_pct": position_pct(score),
                "note": f"自动模式: 环境良好({score}分), 动态仓位 {position_pct(score):.0f}%"}
    return {**base, "action": "filter_out", "pos_pct": 0,
            "note": f"自动模式: 环境偏弱({score}分<{th}), 建议观望"}


# ---------------------------------------------------------------
# 牛熊离场切换（回测实证的最优离场策略随牛熊切换）
# ---------------------------------------------------------------
def exit_style(asof=None):
    """决定当前离场策略风格。返回 (style, note)：
      style = "hold" : 破MA20 + 大盘转弱 双确认才离场（让利润奔跑，牛市更优，避免卖飞）
      style = "exit" : 破MA20 即离场（及时止盈，熊市/震荡更稳，控回撤）
    多起点回测：牛市(2024-09/2025-01起点) 双确认收益显著高于破线即走；
                熊市(2023-09起点) 破线即走更稳。故按牛熊模式自动切换。
    """
    mode = get_mode()
    th = get_threshold()
    if mode == "bull":
        return "hold", "牛市(进取)模式: 破MA20+大盘转弱双确认才离场，让利润奔跑"
    if mode == "bear":
        return "exit", "熊市(稳健)模式: 破MA20即离场，及时止盈控回撤"
    # auto: 按环境评分自动选择
    score, _det = env_score(asof)
    if score is None or score >= th:
        return "hold", f"自动模式: 环境评分{score}≥{th}, 用双确认离场（让利润奔跑）"
    return "exit", f"自动模式: 环境评分{score}<{th}, 用破线即走（控回撤）"
