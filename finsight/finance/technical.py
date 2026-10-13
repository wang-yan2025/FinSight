"""技术面分析引擎：均线/MACD/RSI/KDJ/量价/支撑压力位。

输入为日 K 线 DataFrame（date/open/high/low/close/volume），
全部纯函数计算，输出结构化技术面观点。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field

import pandas as pd


# ---------------- 指标计算（纯函数） ----------------

def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """返回 (dif, dea, hist)。"""
    dif = ema(close, fast) - ema(close, slow)
    dea = ema(dif, signal)
    hist = (dif - dea) * 2
    return dif, dea, hist


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, math.nan)
    return 100 - 100 / (1 + rs)


def kdj(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9):
    """返回 (K, D, J)。"""
    llv = low.rolling(n, min_periods=1).min()
    hhv = high.rolling(n, min_periods=1).max()
    rsv = (close - llv) / (hhv - llv).replace(0, math.nan) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def support_resistance(df: pd.DataFrame, window: int = 60) -> dict:
    """基于近 N 日高低点计算支撑/压力位。"""
    if len(df) < 10:
        return {}
    recent = df.tail(window)
    return {
        "支撑位": round(float(recent["low"].quantile(0.05)), 2),
        "强支撑": round(float(recent["low"].min()), 2),
        "压力位": round(float(recent["high"].quantile(0.95)), 2),
        "强压力": round(float(recent["high"].max()), 2),
    }


# ---------------- 结构化输出 ----------------

@dataclass
class TechnicalView:
    """技术面观点（全部由程序计算，不含 LLM）。"""
    latest_close: float = 0.0
    ma5: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    trend: str = ""            # 上升趋势 / 下降趋势 / 震荡
    macd_dif: float | None = None
    macd_dea: float | None = None
    macd_signal: str = ""      # 金叉 / 死叉 / 多头 / 空头 / 中性
    rsi14: float | None = None
    rsi_signal: str = ""       # 超买 / 超卖 / 中性
    kdj_k: float | None = None
    kdj_d: float | None = None
    kdj_j: float | None = None
    kdj_signal: str = ""
    volume_ratio: float | None = None   # 量比（近5日均量 / 前20日均量）
    support: float | None = None
    resistance: float | None = None
    strong_support: float | None = None
    strong_resistance: float | None = None
    summary: str = ""          # 一句话技术面小结


def analyze_technical(kline: pd.DataFrame) -> TechnicalView:
    """基于日 K 线生成技术面观点。"""
    if kline is None or len(kline) < 30:
        return TechnicalView(summary="K 线数据不足（需≥30 根），技术面分析不可用")

    close = kline["close"]
    view = TechnicalView(latest_close=float(close.iloc[-1]))

    # 均线
    view.ma5 = round(float(sma(close, 5).iloc[-1]), 2) if len(close) >= 5 else None
    view.ma20 = round(float(sma(close, 20).iloc[-1]), 2) if len(close) >= 20 else None
    view.ma60 = round(float(sma(close, 60).iloc[-1]), 2) if len(close) >= 60 else None
    if view.ma5 and view.ma20 and view.ma60:
        if close.iloc[-1] > view.ma5 > view.ma20 > view.ma60:
            view.trend = "上升趋势（多头排列）"
        elif close.iloc[-1] < view.ma5 < view.ma20 < view.ma60:
            view.trend = "下降趋势（空头排列）"
        else:
            view.trend = "震荡（均线交织）"
    elif view.ma5 and view.ma20:
        view.trend = "短期震荡" if abs(view.ma5 - view.ma20) / view.ma20 < 0.02 else (
            "短期偏强" if view.ma5 > view.ma20 else "短期偏弱")

    # MACD
    dif, dea, hist = macd(close)
    view.macd_dif = round(float(dif.iloc[-1]), 4) if pd.notna(dif.iloc[-1]) else None
    view.macd_dea = round(float(dea.iloc[-1]), 4) if pd.notna(dea.iloc[-1]) else None
    if view.macd_dif is not None and view.macd_dea is not None:
        if view.macd_dif > view.macd_dea:
            view.macd_signal = "多头（DIF>DEA）"
            if len(dif) >= 2 and dif.iloc[-2] <= dea.iloc[-2]:
                view.macd_signal = "金叉（刚上穿）"
        else:
            view.macd_signal = "空头（DIF<DEA）"
            if len(dif) >= 2 and dif.iloc[-2] >= dea.iloc[-2]:
                view.macd_signal = "死叉（刚下穿）"

    # RSI
    r = rsi(close)
    view.rsi14 = round(float(r.iloc[-1]), 1) if pd.notna(r.iloc[-1]) else None
    if view.rsi14 is not None:
        if view.rsi14 > 70:
            view.rsi_signal = "超买（>70）"
        elif view.rsi14 < 30:
            view.rsi_signal = "超卖（<30）"
        else:
            view.rsi_signal = "中性"

    # KDJ
    k, d, j = kdj(kline["high"], kline["low"], close)
    view.kdj_k = round(float(k.iloc[-1]), 1) if pd.notna(k.iloc[-1]) else None
    view.kdj_d = round(float(d.iloc[-1]), 1) if pd.notna(d.iloc[-1]) else None
    view.kdj_j = round(float(j.iloc[-1]), 1) if pd.notna(j.iloc[-1]) else None
    if view.kdj_k is not None and view.kdj_d is not None:
        if view.kdj_k > view.kdj_d:
            view.kdj_signal = "K>D 偏多"
            if view.kdj_j is not None and view.kdj_j > 100:
                view.kdj_signal += "（J 值超买）"
        else:
            view.kdj_signal = "K<D 偏空"
            if view.kdj_j is not None and view.kdj_j < 0:
                view.kdj_signal += "（J 值超卖）"

    # 量比
    vol = kline["volume"]
    if len(vol) >= 25:
        recent_vol = vol.tail(5).mean()
        base_vol = vol.iloc[-25:-5].mean()
        if base_vol > 0:
            view.volume_ratio = round(float(recent_vol / base_vol), 2)

    # 支撑/压力
    sr = support_resistance(kline)
    view.support = sr.get("支撑位")
    view.resistance = sr.get("压力位")
    view.strong_support = sr.get("强支撑")
    view.strong_resistance = sr.get("强压力")

    # 汇总
    parts = []
    if view.trend:
        parts.append(f"均线{view.trend}")
    if view.macd_signal:
        parts.append(f"MACD {view.macd_signal}")
    if view.rsi_signal and view.rsi_signal != "中性":
        parts.append(f"RSI {view.rsi_signal}")
    if view.volume_ratio and view.volume_ratio > 1.5:
        parts.append(f"量比{view.volume_ratio}（放量）")
    elif view.volume_ratio and view.volume_ratio < 0.5:
        parts.append(f"量比{view.volume_ratio}（缩量）")
    view.summary = "；".join(parts) if parts else "各项技术指标处于中性区域"

    return view
