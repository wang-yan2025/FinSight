"""声明（Claim）提取：从文本片段识别可核验的财务声明。

保守策略：只提取「指标词 + 数值（带单位）」结构清晰的内容；
识别不了的内容不生成声明、不自动判定正确。定性断言仅按关键词
启发式标记（如"竞争壁垒/行业领先"），核验状态保守处理。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field

from finsight.verify.extract import Fragment
from finsight.verify.reference import METRIC_REGISTRY

# 指标词表：正则 → (指标 key, 是否同比类)。
# 顺序敏感：更长/更具体的表述在前（如"应收账款周转率"先于"应收账款"）。
_METRIC_PATTERNS = [
    (r"经营现金流(?:净额)?/净利润", "ocf_np_ratio", False),
    (r"归[属母公]*\s*母公司(?:股东)?(?:的)?净利润|归母净利润", "parent_net_profit", False),
    (r"经营活动(?:产生的)?现金流(?:量)?净额|经营现金流净额", "operating_cash_flow", False),
    (r"营业收入同比(?:增长|增速|增长率|变化)?|营收同比(?:增长|增速)?|收入同比增长", "revenue_yoy", True),
    (r"净利润同比(?:增长|增速|增长率|变化)?", "net_profit_yoy", True),
    (r"归母净利润同比(?:增长|增速|增长率)?", "parent_net_profit_yoy", True),
    (r"营业收入|营收|营业总收入", "revenue", False),
    (r"净利润", "net_profit", False),
    (r"总资产|资产总额", "total_assets", False),
    (r"总负债|负债合计|负债总额", "total_liabilities", False),
    (r"应收账款周转率", "receivables_turnover", False),
    (r"存货周转率", "inventory_turnover", False),
    (r"应收账款", "accounts_receivable", False),
    (r"存货(?:余额)?", "inventory", False),
    (r"毛利率", "gross_margin", False),
    (r"净利率", "net_margin", False),
    (r"资产负债率", "debt_to_asset_ratio", False),
    (r"流动比率", "current_ratio", False),
]

# 数值 + 单位（金额或百分比/百分点/倍/次）
_VALUE_UNIT = re.compile(
    r"(-?[\d,]+(?:\.\d+)?)\s*(亿元|万元|元|个百分点|百分點|个百分点|pp|％|%|倍|次)")

# 期间表达
_PERIOD_PATTERNS = [
    (re.compile(r"(20\d{2})\s*年\s*(?:第?\s*一|1)\s*季度|(20\d{2})Q1", re.I), "Q1"),
    (re.compile(r"(20\d{2})\s*年\s*(?:第?\s*二|2)\s*季度|(20\d{2})Q2", re.I), "Q2"),
    (re.compile(r"(20\d{2})\s*年\s*(?:第?\s*三|3)\s*季度|(20\d{2})Q3", re.I), "Q3"),
    (re.compile(r"(20\d{2})\s*年\s*(?:第?\s*四|4)\s*季度|(20\d{2})Q4", re.I), "Q4"),
]

_QUARTER_CN = {"一": "Q1", "二": "Q2", "三": "Q3", "四": "Q4"}

_UNIT_SCALE = {"亿元": 1e8, "万元": 1e4, "元": 1.0}
_RATIO_UNITS = ("%", "％", "pp", "个百分点")

# 同比特殊状态词 → yoy_status
_STATUS_WORDS = {
    "扭亏为盈": "turn_to_profit",
    "扭亏": "turn_to_profit",
    "由盈转亏": "profit_to_loss",
    "亏损收窄": "loss_narrowing",
    "减亏": "loss_narrowing",
    "亏损扩大": "loss_widening",
    "增亏": "loss_widening",
}

# 定性断言关键词（V5 保守启发式）
_QUALITATIVE_KEYWORDS = ("竞争壁垒", "行业领先", "龙头", "核心优势", "竞争优势",
                         "护城河", "必将", "确定性高", "显著优于", "市场第一")


@dataclass
class Claim:
    """待核验声明。"""

    cid: str
    text: str                    # 原文片段（句子级）
    location: str
    section: str = ""
    metric: str = ""             # METRIC_REGISTRY key
    metric_raw: str = ""         # 原文指标表述
    period: str | None = None    # 声称期间标签，如 2024Q4 / 2024FY
    period_basis: str = ""       # quarter（明确单季）/ cumulative（明确累计）/ quarter_report（歧义）
    value: float | None = None   # 归一化后数值（金额→元；比率→小数；pp→数值）
    display: str = ""            # 原始数值+单位文本
    unit: str = ""               # 归一化单位：元 / % / 百分点 / 倍 / 次
    decimals: int = 0            # 声称数值的小数位（决定比较容差）
    evidence_ref: str | None = None
    kind: str = "numeric"        # numeric / status_word / qualitative


@dataclass
class ClaimSet:
    claims: list[Claim] = dc_field(default_factory=list)
    extraction_notes: list[str] = dc_field(default_factory=list)


def _parse_period(sentence: str) -> tuple[str | None, str]:
    """识别声称期间。返回 (期间标签, 口径)。

    口径三值：
    - "quarter"        明确单季表述（X季度/单季）→ 核验仅用单季口径；
    - "cumulative"     明确累计表述（年度/全年/年报/累计/上半年/前三季度）
                       → 核验仅用累计口径；
    - "quarter_report" 裸 Q 标签（如 2024Q4）且句内无消歧关键词
                       → 单季/累计歧义，核验按 needs_review 处理。
    """
    has_single_kw = bool(re.search(r"单季", sentence))
    has_cum_kw = bool(re.search(r"累计|全年|年报|年度|年初至", sentence))

    m = re.search(r"(20\d{2})\s*年\s*第?\s*([一二三四1-4])\s*季度", sentence)
    if m:
        q = _QUARTER_CN.get(m.group(2), f"Q{m.group(2)}")
        basis = "cumulative" if (has_cum_kw and not has_single_kw) else "quarter"
        return f"{m.group(1)}{q}", basis
    m = re.search(r"(20\d{2})\s*Q([1-4])", sentence, re.I)
    if m:
        if has_single_kw:
            return f"{m.group(1)}Q{m.group(2)}", "quarter"
        if has_cum_kw:
            return f"{m.group(1)}Q{m.group(2)}", "cumulative"
        return f"{m.group(1)}Q{m.group(2)}", "quarter_report"
    m = re.search(r"(20\d{2})\s*年\s*上半年|(20\d{2})H1", sentence, re.I)
    if m:
        year = m.group(1) or m.group(2)
        return f"{year}Q2", "cumulative"
    m = re.search(r"(20\d{2})\s*年前三季度", sentence)
    if m:
        return f"{m.group(1)}Q3", "cumulative"
    m = re.search(r"(20\d{2})\s*年(?:度|全年)?", sentence)
    if m:
        return f"{m.group(1)}Q4", "cumulative"
    return None, ""


def _decimals_of(number_text: str) -> int:
    return len(number_text.split(".")[1]) if "." in number_text else 0


def _split_sentences(fragment_text: str) -> list[str]:
    parts = re.split(r"[。；;\n]", fragment_text)
    return [p.strip() for p in parts if p.strip()]


def extract_claims(fragments: list[Fragment]) -> ClaimSet:
    """从片段提取声明（保守：识别不出不生成）。"""
    claim_set = ClaimSet()
    cid = 0
    for frag in fragments:
        for sentence in _split_sentences(frag.text):
            evid = re.search(r"\[(E-[A-Z0-9-]+)\]", sentence)
            period, basis = _parse_period(sentence)

            # ---- 同比特殊状态词声明（如"净利润由盈转亏"）----
            for word, status in _STATUS_WORDS.items():
                if word in sentence:
                    prefix = sentence[:sentence.index(word)]
                    best = None  # 取状态词之前【距离最近】的指标词，避免归属错位
                    for pat, metric, _is_yoy in _METRIC_PATTERNS:
                        m = re.search(pat, prefix)
                        if m and (best is None or m.end() > best[0].end()):
                            best = (m, metric)
                    if best is not None:
                        m, metric = best
                        target = metric if metric.endswith("_yoy") else (
                            f"{metric}_yoy" if metric in ("net_profit", "parent_net_profit",
                                                          "revenue", "operating_cash_flow")
                            else metric)
                        cid += 1
                        claim_set.claims.append(Claim(
                            cid=f"C{cid}", text=sentence, location=frag.location,
                            section=frag.section, metric=target,
                            metric_raw=m.group(0), period=period, period_basis=basis or "cumulative",
                            value=None, display=word, unit="status",
                            evidence_ref=evid.group(1) if evid else None,
                            kind="status_word"))
                    break

            # ---- 数值声明：指标词 + 数值单位 ----
            for pat, metric, is_yoy in _METRIC_PATTERNS:
                m = re.search(pat, sentence)
                if not m:
                    continue
                # 剥离科学计数法数字（如 2.301e+08），避免误截 "08 元" 之类假数值
                window = re.sub(r"-?\d+(?:\.\d+)?e[+-]?\d+", " ", sentence[m.end():m.end() + 40])
                vm = _VALUE_UNIT.search(window)
                if not vm:
                    continue
                number_text, unit_raw = vm.group(1), vm.group(2)
                if unit_raw == "百分點":
                    unit_raw = "个百分点"
                value_raw = float(number_text.replace(",", ""))
                decimals = _decimals_of(number_text)
                if unit_raw in _UNIT_SCALE:            # 金额 → 元
                    value, unit = value_raw * _UNIT_SCALE[unit_raw], "元"
                elif unit_raw in ("%", "％"):           # 百分比 → 小数
                    value, unit = value_raw / 100.0, "%"
                elif unit_raw == "个百分点":
                    value, unit = value_raw, "百分点"
                else:                                   # 倍 / 次
                    value, unit = value_raw, unit_raw
                # 单位类别守卫（保守跳过，不误判）：
                # 金额类指标 + %/百分点/倍/次（多为增速或比率表述，非金额声明）、
                # 比率/同比类指标 + 元 —— 均不生成数值声明
                metric_kind = METRIC_REGISTRY.get(metric, (None,))[0]
                if metric_kind in ("flow", "point") and unit in ("%", "百分点", "倍", "次"):
                    continue
                if metric_kind in ("ratio", "yoy") and unit == "元":
                    continue
                cid += 1
                claim_set.claims.append(Claim(
                    cid=f"C{cid}", text=sentence, location=frag.location, section=frag.section,
                    metric=metric, metric_raw=m.group(0), period=period,
                    period_basis=basis or ("cumulative" if period else ""),
                    value=value, display=f"{number_text}{unit_raw}", unit=unit,
                    decimals=decimals, evidence_ref=evid.group(1) if evid else None,
                    kind="numeric"))
                break  # 每句取第一个指标+数值对（保守）

            # ---- 定性断言（V5 保守启发式）----
            if any(kw in sentence for kw in _QUALITATIVE_KEYWORDS):
                cid += 1
                claim_set.claims.append(Claim(
                    cid=f"C{cid}", text=sentence, location=frag.location, section=frag.section,
                    metric="", period=period, value=None, unit="-",
                    evidence_ref=evid.group(1) if evid else None, kind="qualitative"))
    if not claim_set.claims:
        claim_set.extraction_notes.append("未识别到可核验的财务声明（保守提取：仅识别指标词+数值结构）")
    return claim_set
