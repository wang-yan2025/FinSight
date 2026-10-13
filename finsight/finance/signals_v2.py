"""S1–S7 财务风险预警指标体系（PRD V2.0 表格 3 严格实现）。

信号定义（量化判定标准）：
  S1 营收增速大幅下滑   | 营收同比增速较上年下降≥5个百分点，或营收同比负增长
  S2 毛利率持续下滑     | 连续2年毛利率下降≥3个百分点
  S3 增收不增利（扣非） | 营收同比增长，但扣非净利润同比下降
  S4 经营现金流长期弱于利润 | 连续2年经营现金流净额 < 扣非净利润×70%
  S5 应收账款异常       | 应收账款余额增速 > 营收增速 + 10个百分点
  S6 存货异常           | 存货余额增速 > 营收增速 + 10个百分点
  S7 大额资产减值风险   | 商誉+在建工程合计占归母净资产比重 > 30%

预警分级：
  1 个信号触发  → 黄色关注（单项异常迹象，误判概率较高）
  2 个信号同时触发 → 橙色预警（多项指标恶化，风险明显上升）
  ≥3 个信号同时触发 → 红色高危（多维度异常共振，舞弊/暴雷可能性显著提高）
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

import pandas as pd

from finsight.finance.indicators import yoy_growth
from finsight.finance.quarterly import period_of

SIGNAL_CN = {
    "S1": "营收增速大幅下滑",
    "S2": "毛利率持续下滑",
    "S3": "增收不增利（扣非口径）",
    "S4": "经营现金流长期弱于利润",
    "S5": "应收账款异常",
    "S6": "存货异常",
    "S7": "大额资产减值风险",
}

SIGNAL_DIMENSION = {
    "S1": "成长性", "S2": "盈利水平", "S3": "盈利水平",
    "S4": "盈利质量", "S5": "资产与收入确认质量",
    "S6": "资产与收入确认质量", "S7": "资产与收入确认质量",
}

LEVEL_CN = {"yellow": "黄色关注", "orange": "橙色预警", "red": "红色高危", "none": "无预警"}
LEVEL_COLOR = {"yellow": "#E8B44F", "orange": "#E67E22", "red": "#E05B5B", "none": "#15B8A6"}


@dataclass
class SignalV2:
    sid: str
    name: str
    dimension: str
    triggered: bool
    detail: str = ""           # 量化判定说明
    evidence: str = ""         # 涉及字段与数值


@dataclass
class RiskAssessment:
    """S1–S7 评估结果 + 预警分级。"""
    signals: list[SignalV2] = dc_field(default_factory=list)
    triggered_count: int = 0
    level: str = "none"        # yellow / orange / red / none
    level_cn: str = "无预警"
    summary: str = ""

    @property
    def triggered_sids(self) -> list[str]:
        return [s.sid for s in self.signals if s.triggered]


def _annual_value_map(std: pd.DataFrame, col: str) -> dict[int, float]:
    """{年份: 年报期末（或累计）原始值}。"""
    out: dict[int, float] = {}
    for _, r in std.iterrows():
        p = period_of(r["report_date"])
        if p and p[1] == 4 and pd.notna(r.get(col)):
            out[p[0]] = float(r[col])
    return out


def _yoy_from_annual(annual: dict[int, float]) -> dict[int, float]:
    """{年份: 同比增长率}，从年度原始值计算。"""
    out: dict[int, float] = {}
    for y, v in annual.items():
        prev = annual.get(y - 1)
        if prev is not None and prev != 0:
            out[y] = (v - prev) / abs(prev)
    return out


def _gross_margin(std: pd.DataFrame) -> dict[int, float]:
    """{年份: 年报毛利率}。"""
    out: dict[int, float] = {}
    for _, r in std.iterrows():
        p = period_of(r["report_date"])
        if p and p[1] == 4 and pd.notna(r.get("revenue")) and pd.notna(r.get("operate_cost")) \
                and r["revenue"] > 0:
            out[p[0]] = (r["revenue"] - r["operate_cost"]) / r["revenue"]
    return out


def assess_risk(std_income, std_balance, std_cashflow) -> RiskAssessment:
    """执行 S1–S7 全量评估（纯函数，输入标准化三表）。"""
    result = RiskAssessment()
    inc_y = std_income
    bal_y = std_balance
    cf_y = std_cashflow

    def _make(sid: str, triggered: bool, detail: str, evidence: str = ""):
        return SignalV2(sid=sid, name=SIGNAL_CN[sid],
                        dimension=SIGNAL_DIMENSION[sid],
                        triggered=triggered, detail=detail, evidence=evidence)

    # ---- S1 营收增速大幅下滑 ----
    rev_raw = _annual_value_map(inc_y, "revenue")
    rev_yoy = _yoy_from_annual(rev_raw)
    s1 = False
    s1_detail = "未触发"
    for y in sorted(rev_yoy):
        cur = rev_yoy[y]
        if cur < 0:
            s1 = True
            s1_detail = f"{y} 年营收同比 {cur:+.1%}（负增长）"
            break
        prev = rev_yoy.get(y - 1)
        if prev is not None and (prev - cur) >= 0.05:
            s1 = True
            s1_detail = (f"{y} 年营收同比 {cur:+.1%}，"
                         f"较上年 {prev:+.1%} 下降 {(prev - cur) * 100:.1f} 个百分点")
            break
    result.signals.append(_make("S1", s1, s1_detail,
                                f"营收同比序列: { {y: f'{v:+.1%}' for y, v in rev_yoy.items()} }"))

    # ---- S2 毛利率持续下滑 ----
    gm_annual = _gross_margin(inc_y)
    gm_years = sorted(gm_annual)
    s2 = False
    s2_detail = "未触发"
    for i in range(len(gm_years) - 1, 0, -1):
        y, y_prev = gm_years[i], gm_years[i - 1]
        if y_prev in gm_annual and (gm_annual[y_prev] - gm_annual[y]) >= 0.03:
            y_prev2 = gm_years[i - 2] if i >= 2 else None
            if y_prev2 is not None and y_prev2 in gm_annual and \
                    (gm_annual[y_prev2] - gm_annual[y_prev]) >= 0.03:
                s2 = True
                s2_detail = (f"毛利率连续 2 年下降≥3pp："
                             f"{y_prev2} {gm_annual[y_prev2]:.1%} → {y_prev} {gm_annual[y_prev]:.1%}"
                             f" → {y} {gm_annual[y]:.1%}")
                break
    result.signals.append(_make("S2", s2, s2_detail,
                                f"毛利率序列: { {y: f'{v:.1%}' for y, v in gm_annual.items()} }"))

    # ---- S3 增收不增利（扣非口径） ----
    dnp_annual = _annual_value_map(inc_y, "deducted_parent_net_profit")
    s3 = False
    s3_detail = "未触发（或扣非数据缺失）"
    for y in sorted(dnp_annual, reverse=True):
        if y - 1 in dnp_annual and y in rev_yoy:
            rev_positive = rev_yoy[y] > 0
            dnp_down = dnp_annual[y] < dnp_annual[y - 1] if dnp_annual[y - 1] != 0 else False
            if rev_positive and dnp_down:
                s3 = True
                s3_detail = (f"{y} 年营收同比 {rev_yoy[y]:+.1%}（增长），"
                             f"但扣非归母净利 {dnp_annual[y] / 1e8:.1f} 亿 < 上年 {dnp_annual[y - 1] / 1e8:.1f} 亿")
                break
    result.signals.append(_make("S3", s3, s3_detail,
                                f"扣非归母净利序列(亿): { {y: round(v / 1e8, 1) for y, v in dnp_annual.items()} }"))

    # ---- S4 经营现金流长期弱于利润 ----
    if cf_y is not None and not cf_y.empty:
        ocf_annual = _annual_value_map(cf_y, "operating_cash_flow")
        s4_count = 0
        s4_detail_parts = []
        for y in sorted(ocf_annual):
            if y in dnp_annual and dnp_annual[y] > 0:
                ratio = ocf_annual[y] / dnp_annual[y]
                if ratio < 0.7:
                    s4_count += 1
                    s4_detail_parts.append(f"{y}: OCF/扣非净利 = {ratio:.0%}")
                else:
                    s4_count = 0
                    s4_detail_parts = []
        s4 = s4_count >= 2
        s4_detail = f"连续 {s4_count} 年 OCF < 扣非净利×70%：{'；'.join(s4_detail_parts)}" if s4 else "未触发"
        result.signals.append(_make("S4", s4, s4_detail,
                                    f"OCF/扣非净利序列: {s4_detail_parts}"))
    else:
        result.signals.append(_make("S4", False, "现金流量表不可用，无法评估"))

    # ---- S5 应收账款异常 / S6 存货异常 ----
    if bal_y is not None and not bal_y.empty:
        ar_annual = _annual_value_map(bal_y, "accounts_receivable")
        inv_annual = _annual_value_map(bal_y, "inventory")
        for sid, col_map, name in (("S5", ar_annual, "应收账款"), ("S6", inv_annual, "存货")):
            triggered = False
            detail = "未触发"
            for y in sorted(col_map, reverse=True):
                if y - 1 in col_map and y in rev_raw and col_map[y - 1] != 0 and rev_raw[y - 1] != 0:
                    bal_growth = (col_map[y] - col_map[y - 1]) / abs(col_map[y - 1])
                    rev_growth = (rev_raw[y] - rev_raw[y - 1]) / abs(rev_raw[y - 1])
                    if bal_growth > rev_growth + 0.10:
                        triggered = True
                        detail = (f"{y} 年{name}增速 {bal_growth:+.1%} > "
                                  f"营收增速 {rev_growth:+.1%} + 10pp")
                        break
            result.signals.append(_make(sid, triggered, detail,
                                        f"{name}序列(亿): { {y: round(v / 1e8, 1) for y, v in col_map.items()} }"))

        # ---- S7 大额资产减值风险 ----
        gw_annual = _annual_value_map(bal_y, "goodwill")
        cip_annual = _annual_value_map(bal_y, "cip")
        eq_annual = _annual_value_map(bal_y, "total_equity")
        s7 = False
        s7_detail = "未触发"
        for y in sorted(gw_annual, reverse=True):
            if y in eq_annual and eq_annual[y] > 0:
                ratio = ((gw_annual.get(y, 0) + cip_annual.get(y, 0)) / eq_annual[y])
                if ratio > 0.30:
                    s7 = True
                    gw_b = gw_annual.get(y, 0) / 1e8
                    cip_b = cip_annual.get(y, 0) / 1e8
                    eq_b = eq_annual[y] / 1e8
                    s7_detail = (f"{y} 年商誉 {gw_b:.1f} 亿 + 在建工程 {cip_b:.1f} 亿"
                                 f" = {(gw_b + cip_b):.1f} 亿，占净资产 {eq_b:.1f} 亿的 {ratio:.0%} > 30%")
                    break
        result.signals.append(_make("S7", s7, s7_detail,
                                    f"商誉/在建/净资产序列(亿): "
                                    f"GW={ {y: round(v / 1e8, 1) for y, v in gw_annual.items()} }, "
                                    f"CIP={ {y: round(v / 1e8, 1) for y, v in cip_annual.items()} }"))
    else:
        for sid in ("S5", "S6", "S7"):
            result.signals.append(_make(sid, False, "资产负债表不可用，无法评估"))

    # ---- 预警分级 ----
    count = sum(1 for s in result.signals if s.triggered)
    result.triggered_count = count
    if count >= 3:
        result.level = "red"
        result.level_cn = LEVEL_CN["red"]
        result.summary = f"红色高危：{count} 个信号同时触发（{', '.join(result.triggered_sids)}），多维度财务异常共振"
    elif count == 2:
        result.level = "orange"
        result.level_cn = LEVEL_CN["orange"]
        result.summary = f"橙色预警：{count} 个信号同时触发（{', '.join(result.triggered_sids)}），多项指标恶化"
    elif count == 1:
        result.level = "yellow"
        result.level_cn = LEVEL_CN["yellow"]
        result.summary = f"黄色关注：仅 {result.triggered_sids[0]} 触发，单项异常迹象，误判概率较高，需进一步核查"
    else:
        result.level = "none"
        result.level_cn = LEVEL_CN["none"]
        result.summary = "S1–S7 全部未触发"

    return result
