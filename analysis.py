# -*- coding: utf-8 -*-
"""
趋势分析逻辑：多周期趋势 / 技术指标 / 资金流 / 基本面趋势 / 综合结论
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------
# 工具
# ---------------------------------------------------------------
def _ma(series, n):
    return series.rolling(n).mean()


def _ema(series, n):
    return series.ewm(span=n, adjust=False).mean()


def _slope(series, n=20):
    """最近 n 个点的线性回归斜率（归一化为每期百分比变化）"""
    s = series.dropna().tail(n)
    if len(s) < 5:
        return 0.0
    x = np.arange(len(s), dtype=float)
    y = s.values.astype(float)
    if y[-1] == 0:
        return 0.0
    k = np.polyfit(x, y, 1)[0]
    return k / abs(y[-1]) * 100.0  # 每期相对当前价的百分比


# ---------------------------------------------------------------
# 1. 多周期趋势判断
# ---------------------------------------------------------------
def analyze_trend(kline_df, period_name="日线"):
    """
    输入：某个周期的K线 DataFrame（需含 close 列）
    返回：{direction, strength, score, desc}
    direction: up / down / sideways
    strength: strong / medium / weak
    """
    if kline_df is None or len(kline_df) < 30:
        return {"direction": "unknown", "strength": "weak",
                "score": 0, "desc": "数据不足"}
    close = kline_df["close"]
    ma5, ma10, ma20, ma60 = _ma(close, 5), _ma(close, 10), _ma(close, 20), _ma(close, 60)
    last = close.iloc[-1]
    m5, m10, m20, m60 = ma5.iloc[-1], ma10.iloc[-1], ma20.iloc[-1], ma60.iloc[-1]

    # --- 方向判断 ---
    bull_align = (m5 > m10 > m20) and (last > m20)
    bear_align = (m5 < m10 < m20) and (last < m20)
    slope20 = _slope(close, 20)
    slope60 = _slope(close, 60)

    score = 0.0
    if bull_align:
        score += 2.0
    elif bear_align:
        score -= 2.0
    if last > m20:
        score += 1.0
    else:
        score -= 1.0
    score += np.clip(slope20, -3, 3) / 3.0
    score += np.clip(slope60, -3, 3) / 6.0

    if score >= 1.2:
        direction = "up"
    elif score <= -1.2:
        direction = "down"
    else:
        direction = "sideways"

    # --- 强度判断 ---
    spread = (m5 - m20) / m20 * 100 if m20 else 0  # 均线乖离
    abs_score = abs(score)
    if direction == "sideways":
        strength = "weak"
    elif abs_score >= 2.5 or (direction != "sideways" and abs(spread) > 3):
        strength = "strong"
    elif abs_score >= 1.5:
        strength = "medium"
    else:
        strength = "weak"

    # --- 描述 ---
    dmap = {"up": "上升", "down": "下降", "sideways": "震荡"}
    smap = {"strong": "强", "medium": "中", "weak": "弱"}
    pos = "上方" if last > m20 else "下方"
    desc = (f"{dmap[direction]}趋势（{smap[strength]}），"
            f"价格位于20日线{pos}，MA5/10/20 呈"
            f"{'多头排列' if m5 > m10 > m20 else '空头排列' if m5 < m10 < m20 else '交织状态'}")

    return {
        "period": period_name,
        "direction": direction,
        "strength": strength,
        "score": round(score, 2),
        "desc": desc,
        "price": round(float(last), 2),
        "ma5": round(float(m5), 2) if m5 == m5 else None,
        "ma10": round(float(m10), 2) if m10 == m10 else None,
        "ma20": round(float(m20), 2) if m20 == m20 else None,
        "ma60": round(float(m60), 2) if m60 == m60 else None,
        "slope20": round(float(slope20), 3),
    }


# ---------------------------------------------------------------
# 2. 技术指标（MACD / RSI / 量能）
# ---------------------------------------------------------------
def calc_indicators(kline_df):
    """返回 MACD/RSI/量能结论 dict"""
    if kline_df is None or len(kline_df) < 30:
        return {"macd": {}, "rsi": {}, "volume": {}, "desc": "数据不足"}
    close = kline_df["close"]
    volume = kline_df["volume"] if "volume" in kline_df.columns else pd.Series(dtype=float)

    # MACD
    ema12, ema26 = _ema(close, 12), _ema(close, 26)
    dif = ema12 - ema26
    dea = _ema(dif, 9)
    macd_bar = (dif - dea) * 2
    dif_v, dea_v, bar_v = dif.iloc[-1], dea.iloc[-1], macd_bar.iloc[-1]
    dif_p, dea_p, bar_p = dif.iloc[-2], dea.iloc[-2], macd_bar.iloc[-2]
    if dif_p <= dea_p and dif_v > dea_v:
        macd_state = "金叉"
    elif dif_p >= dea_p and dif_v < dea_v:
        macd_state = "死叉"
    elif dif_v > dea_v and bar_v >= bar_p:
        macd_state = "多头增强"
    elif dif_v > dea_v:
        macd_state = "多头减弱"
    elif dif_v < dea_v and bar_v <= bar_p:
        macd_state = "空头增强"
    else:
        macd_state = "空头减弱"

    # RSI(14)
    diff = close.diff()
    gain = diff.clip(lower=0).rolling(14).mean()
    loss = (-diff.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).iloc[-1]
    rsi = float(rsi) if rsi == rsi else 50.0
    if rsi >= 70:
        rsi_state = "超买"
    elif rsi <= 30:
        rsi_state = "超卖"
    elif rsi >= 55:
        rsi_state = "偏强"
    elif rsi <= 45:
        rsi_state = "偏弱"
    else:
        rsi_state = "中性"

    # 量能
    vol_state = ""
    if len(volume.dropna()) >= 20:
        v5 = volume.tail(5).mean()
        v20 = volume.tail(20).mean()
        ratio = v5 / v20 if v20 else 1.0
        if ratio >= 1.2:
            vol_state = "温和放量"
        elif ratio >= 1.5:
            vol_state = "显著放量"
        elif ratio <= 0.7:
            vol_state = "明显缩量"
        else:
            vol_state = "量能平稳"
    else:
        vol_state = "量能不足"

    return {
        "macd": {
            "dif": round(float(dif_v), 3),
            "dea": round(float(dea_v), 3),
            "bar": round(float(bar_v), 3),
            "state": macd_state,
        },
        "rsi": {"value": round(rsi, 1), "state": rsi_state},
        "volume": {"state": vol_state},
    }


# ---------------------------------------------------------------
# 3. 资金流趋势
# ---------------------------------------------------------------
def analyze_fund_flow(ff_df):
    """主力资金趋势，返回 {5日, 20日, desc}"""
    if ff_df is None or len(ff_df) < 5:
        return {"d5": None, "d20": None, "d5_pct": None, "desc": "资金数据不足"}
    m5 = ff_df["main_net"].tail(5).sum()
    m20 = ff_df["main_net"].tail(20).sum()
    p5 = ff_df["main_pct"].tail(5).mean()
    w5, w20 = m5 / 1e8, m20 / 1e8  # 亿元
    if m5 > 0 and m20 > 0:
        desc = f"主力近5日净流入 {w5:.2f} 亿，近20日净流入 {w20:.2f} 亿，资金持续做多"
    elif m5 < 0 and m20 < 0:
        desc = f"主力近5日净流出 {abs(w5):.2f} 亿，近20日净流出 {abs(w20):.2f} 亿，资金持续撤离"
    elif m5 > 0:
        desc = f"主力近5日净流入 {w5:.2f} 亿，但近20日仍净流出，短期资金回流待确认"
    else:
        desc = f"主力近5日净流出 {abs(w5):.2f} 亿，近20日净流入 {w20:.2f} 亿，短期资金有获利了结迹象"
    return {
        "d5": round(m5 / 1e8, 2),
        "d20": round(m20 / 1e8, 2),
        "d5_pct": round(float(p5), 2) if p5 == p5 else None,
        "desc": desc,
    }


# ---------------------------------------------------------------
# 4. 基本面趋势
# ---------------------------------------------------------------
def analyze_fundamentals(fin_df):
    """财务趋势，返回 {方向, desc, 数据点}"""
    if fin_df is None or len(fin_df) < 2:
        return {"direction": "unknown", "desc": "财务数据不足", "points": []}

    df = fin_df.sort_values("REPORT_DATE").copy()
    # 营收同比、净利同比、毛利率、ROE
    rev_yoy = pd.to_numeric(df["TOTALOPERATEREVETZ"], errors="coerce")
    np_yoy = pd.to_numeric(df["PARENTNETPROFITTZ"], errors="coerce")
    gm = pd.to_numeric(df["XSMLL"], errors="coerce")
    roe = pd.to_numeric(df["ROEJQ"], errors="coerce")

    last_rev_yoy = _last(rev_yoy)
    last_np_yoy = _last(np_yoy)
    last_gm = _last(gm)
    last_roe = _last(roe)
    gm_prev = _last(gm, 1)

    # 综合方向：看最近报告期营收/净利同比 + 毛利率变化
    score = 0.0
    if last_rev_yoy is not None:
        score += 1 if last_rev_yoy > 0 else -1
    if last_np_yoy is not None:
        score += 1 if last_np_yoy > 0 else -1
    if last_gm is not None and gm_prev is not None:
        score += 1 if last_gm > gm_prev else -1

    if score >= 2:
        direction = "up"
    elif score <= -2:
        direction = "down"
    else:
        direction = "sideways"

    dmap = {"up": "向好", "down": "走弱", "sideways": "平稳", "unknown": "未知"}
    parts = []
    if last_rev_yoy is not None:
        parts.append(f"营收同比{'+' if last_rev_yoy>0 else ''}{last_rev_yoy:.1f}%")
    if last_np_yoy is not None:
        parts.append(f"净利同比{'+' if last_np_yoy>0 else ''}{last_np_yoy:.1f}%")
    if last_gm is not None:
        parts.append(f"毛利率{last_gm:.1f}%")
    if last_roe is not None:
        parts.append(f"ROE {last_roe:.1f}%")
    desc = f"基本面{dmap[direction]}：" + "，".join(parts)

    # 供前端画迷你趋势图的数据点
    points = []
    for i in range(len(df)):
        points.append({
            "date": str(df["REPORT_DATE"].iloc[i])[:10],
            "rev_yoy": _last(rev_yoy, len(df) - 1 - i),
            "np_yoy": _last(np_yoy, len(df) - 1 - i),
            "gm": _last(gm, len(df) - 1 - i),
        })
    points.reverse()

    return {
        "direction": direction,
        "desc": desc,
        "points": points,
        "latest": {
            "rev_yoy": last_rev_yoy, "np_yoy": last_np_yoy,
            "gm": last_gm, "roe": last_roe,
        },
    }


def _last(series, back=0):
    s = series.dropna()
    if len(s) <= back:
        return None
    v = s.iloc[-1 - back]
    return float(v) if v == v else None


# ---------------------------------------------------------------
# 5. 综合结论
# ---------------------------------------------------------------
def generate_conclusion(trends, tech, fund, fundamentals, quote=None):
    """综合价格趋势+技术+资金+基本面，生成一句话结论和评级"""
    if not trends:
        return {"rating": "unknown", "sentence": "数据不足，无法判断"}

    d = trends.get("daily", {}).get("direction")
    w = trends.get("weekly", {}).get("direction")
    m = trends.get("monthly", {}).get("direction")
    d_strength = trends.get("daily", {}).get("strength", "weak")
    w_strength = trends.get("weekly", {}).get("strength", "weak")
    f_dir = fundamentals.get("direction") if fundamentals else "unknown"
    fund_d5 = fund.get("d5") if fund else 0
    rsi_state = tech.get("rsi", {}).get("state", "") if tech else ""
    macd_state = tech.get("macd", {}).get("state", "") if tech else ""

    # 价格趋势打分
    score = 0.0
    dir_score = {"up": 1, "sideways": 0, "down": -1, "unknown": 0}
    str_score = {"strong": 1.0, "medium": 0.5, "weak": 0.2}
    score += dir_score.get(d, 0) * str_score.get(d_strength, 0.5)
    score += dir_score.get(w, 0) * str_score.get(w_strength, 0.5) * 1.2
    score += dir_score.get(m, 0) * 0.8
    # 基本面
    score += {"up": 1.0, "sideways": 0, "down": -1.0}.get(f_dir, 0)
    # 资金
    if fund_d5 is not None:
        score += 0.6 if fund_d5 > 0 else -0.6
    # 技术
    if "超卖" in rsi_state:
        score += 0.3
    if "超买" in rsi_state:
        score -= 0.3

    if score >= 2.0:
        rating, label = "偏多", "趋势偏多"
    elif score >= 0.8:
        rating, label = "谨慎偏多", "趋势中性偏多"
    elif score <= -2.0:
        rating, label = "偏空", "趋势偏空"
    elif score <= -0.8:
        rating, label = "谨慎偏空", "趋势中性偏空"
    else:
        rating, label = "中性", "趋势中性"

    # 组装句子
    segs = []
    segs.append(f"日线{dmap.get(d, '不明')}{'（强）' if d_strength=='strong' else ''}")
    segs.append(f"周线{dmap.get(w, '不明')}{'（强）' if w_strength=='strong' else ''}")
    segs.append(f"月线{dmap.get(m, '不明')}")
    price_part = "、".join(segs[:3])
    pieces = [price_part, f"基本面{dmap.get(f_dir, '不明')}"]
    if fund_d5 is not None:
        pieces.append("资金" + ("净流入" if fund_d5 > 0 else "净流出"))
    tech_part = ""
    if macd_state:
        tech_part = f"MACD{macd_state}"
    if rsi_state:
        tech_part += f"·RSI{rsi_state}"
    if tech_part:
        pieces.append(tech_part)
    sentence = "，".join(pieces) + f"。综合判断：{label}。"

    return {"rating": rating, "label": label, "sentence": sentence, "score": round(score, 2)}


dmap = {"up": "向上", "down": "向下", "sideways": "震荡", "unknown": "不明"}
