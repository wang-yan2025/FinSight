"""备忘录确定性骨架：章节结构与事实内容由 Python 生成。

- 财务事实、同比状态、风险信号全部来自证据层（EvidenceBook），
  骨架不做任何财务计算，只组织与表述。
- 客观事实 / 分析推断 / 情景假设明确区分；LLM 仅填充定性章节
  （kind="llm"），未生成时状态标记待完善，不用伪造文本填充。
- 本备忘录为研究辅助产物，不构成确定性买卖建议。
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import datetime

import pandas as pd

from finsight.memo.evidence import EvidenceBook
from finsight.ui.view import (
    format_ratio,
    format_times,
    format_yoy_with_status,
    format_yuan,
)

DISCLAIMER = (
    "本备忘录由 FinSight 智研投基于东方财富结构化财务数据与规则引擎自动生成，"
    "属于研究辅助产物：所有信号均为风险提示（risk_hint），不构成财务造假或违规认定；"
    "未触发信号不代表不存在风险；本文不构成投资建议或确定性买卖依据。"
    "数据未经上市公司公告原文逐项核验，使用前请按待核实清单人工复核。"
)

SECTION_ORDER = ["summary", "company", "financials", "thesis", "risks",
                 "scenarios", "monitoring", "open_questions", "appendix",
                 "appendix_signals"]

SECTION_TITLES = {
    "summary": "一、研究摘要",
    "company": "二、公司概况",
    "financials": "三、财务表现",
    "thesis": "四、投资逻辑",
    "risks": "五、关键风险",
    "scenarios": "六、情景分析",
    "monitoring": "七、持续跟踪指标",
    "open_questions": "八、待核实问题",
    "appendix": "附录A、证据清单",
    "appendix_signals": "附录B、历史风险信号完整清单",
}

# 风险节默认突出窗口：目标报告期往前 8 个季度（仅展示分层，
# 不改变信号计算、证据登记与核验提取范围）
RISKS_RECENT_QUARTERS = 8


def _period_index(tag: str):
    """'2024Q3' → 2024*4+3；解析失败返回 None。"""
    import re
    m = re.match(r"^(20\d{2})Q([1-4])$", tag or "")
    return int(m.group(1)) * 4 + int(m.group(2)) if m else None


@dataclass
class MemoSection:
    key: str
    title: str
    kind: str                      # deterministic / llm / mixed
    facts: list[str] = dc_field(default_factory=list)      # 确定性内容（含 [E-...] 引用）
    llm_text: str | None = None                            # 校验后的 LLM 定性内容
    status: str = "ready"          # ready / llm_pending / llm_failed / degraded


@dataclass
class InvestmentMemo:
    code: str
    period: str                    # 目标报告期（如 2024Q4）
    generated_at: str
    llm_status: str = "未配置"     # 未配置 / 已生成 / 调用失败 / 输出无效
    llm_issues: list[str] = dc_field(default_factory=list)
    sections: list[MemoSection] = dc_field(default_factory=list)
    evidence: EvidenceBook | None = None

    def section(self, key: str) -> MemoSection | None:
        return next((s for s in self.sections if s.key == key), None)


def _fmt(v, unit: str) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "缺失"
    if unit == "元":
        return format_yuan(v)
    if unit == "%":
        return format_ratio(v)
    if unit == "次":
        return format_times(v)
    return f"{v:,.2f}"


def _status_cn(status_note: str) -> str:
    mapping = {"normal": "", "turn_to_profit": "（扭亏为盈）", "profit_to_loss": "（由盈转亏）",
               "loss_narrowing": "（亏损收窄）", "loss_widening": "（亏损扩大）",
               "undefined_prior_zero": "（上年同期为 0，无定义）"}
    for key, cn in mapping.items():
        if key in status_note:
            return cn
    return ""


def build_memo_skeleton(code: str, book: EvidenceBook) -> InvestmentMemo:
    """从证据集构建确定性备忘录骨架（不含 LLM 内容）。"""
    snapshot = book.snapshot
    period = snapshot.get("target_period", "")
    ev = book.evidences
    memo = InvestmentMemo(
        code=code, period=period,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        llm_status="未配置", evidence=book,
    )

    def val(eid_prefix, period_tag=None):
        """按前缀+期间取证据值（找不到返回 None）。"""
        tag = period_tag or period
        e = ev.get(f"E-{eid_prefix}-{tag}")
        return e.value if e else None

    _target_idx = _period_index(period)   # 风险展示分层窗口基准

    # ---- 一、研究摘要（事实部分确定性；判断部分留给 LLM）----
    summary = MemoSection("summary", SECTION_TITLES["summary"], "mixed", status="ready")
    for prefix, name in (("REV", "营业收入"), ("NP", "净利润")):
        v = val(prefix)
        if v is not None:
            summary.facts.append(
                f"{name}（{period}，累计）{format_yuan(v)} 元 [E-{prefix}-{period}]")
    for prefix, name in (("REVYOY", "营业收入同比"), ("NPYOY", "净利润同比")):
        e = ev.get(f"E-{prefix}-{period}")
        if e is not None:
            summary.facts.append(
                f"{name} {format_yoy_with_status(e.value, '')}{_status_cn(e.note)} "
                f"（口径：累计同比）[E-{prefix}-{period}]")
    e = ev.get(f"E-OCF-{snapshot.get('periods', {}).get('cashflow') or ''}")
    if e is not None:
        summary.facts.append(f"经营活动现金流净额 {format_yuan(e.value)} 元（累计）[{e.eid}]")
    n_high, n_med = snapshot.get("signals_high", 0), snapshot.get("signals_medium", 0)
    summary.facts.append(
        f"风险信号：共 {snapshot.get('signals_total', 0)} 条（较高关注 {n_high}，关注 {n_med}），"
        "均为规则触发的风险提示，详见第五节")
    summary.facts.append("初步投资判断：[待 LLM 生成或人工补充——确定性数据不构成投资判断]")
    summary.facts.append(f"主要不确定性：见第八节待核实清单（当前 {len(book.missing)} 项数据缺口/限制）")
    memo.sections.append(summary)

    # ---- 二、公司概况（无经营材料接入，如实标注）----
    company = MemoSection("company", SECTION_TITLES["company"], "llm", status="llm_pending")
    company.facts.append(
        "公司名称与业务材料未接入：当前数据源仅为结构化财务报表（东方财富），"
        "公司介绍、主营业务、行业地位等信息待补充，不以财务数据推測替代。")
    company.facts.append(f"标的代码：{code}｜目标报告期：{period}（利润表口径）")
    memo.sections.append(company)

    # ---- 三、财务表现（分组事实 + 证据引用）----
    financials = MemoSection("financials", SECTION_TITLES["financials"], "deterministic")
    groups = [
        ("规模与增长", ["REV", "REVYOY", "NP", "NPYOY", "PNP", "PNPYOY"]),
        ("盈利能力", ["GM", "NM"]),
        ("偿债与流动性（期末时点）", ["ASSETS", "LIAB", "DAR", "CR"]),
        ("营运效率（年化）", ["ART", "IVT"]),
        ("现金流与利润质量", ["OCF", "OCFNPR"]),
    ]
    period_map = {"OCF": snapshot.get("periods", {}).get("cashflow"),
                  "OCFNPR": snapshot.get("periods", {}).get("cashflow")}
    for group_name, prefixes in groups:
        lines = []
        for prefix in prefixes:
            tag = period_map.get(prefix) or period
            e = ev.get(f"E-{prefix}-{tag}")
            if e is None:
                continue
            # 次周转型指标的 _fmt 已含单位后缀，不再重复追加
            status_suffix = _status_cn(e.note)
            lines.append(f"- {e.label}：{_fmt(e.value, e.unit)}{status_suffix} [{e.eid}]")
        if lines:
            financials.facts.append(f"**{group_name}**")
            financials.facts.extend(lines)
    financials.facts.append(
        "口径说明：利润表/现金流量表为年初至报告期末累计值，资产负债表为期末时点值；"
        "同比为同期累计对齐（特殊状态已标注，不作普通增减解读）；周转率为年化值。")
    memo.sections.append(financials)

    # ---- 四、投资逻辑（正反两面：支持面=确定性财务事实；反面=已触发风险信号）----
    thesis = MemoSection("thesis", SECTION_TITLES["thesis"], "llm", status="llm_pending")
    thesis.facts.append("以下为客观财务事实层面的正反整理；完整投资逻辑表述 [待 LLM 生成或人工补充]。")
    e_rev, e_np = ev.get(f"E-REVYOY-{period}"), ev.get(f"E-NPYOY-{period}")
    if e_rev and e_rev.value is not None and e_rev.value > 0:
        thesis.facts.append(f"支持面（事实）：营业收入同比正增长 [E-REVYOY-{period}]")
    e_nm = ev.get(f"E-NM-{period}")
    if e_nm is not None:
        thesis.facts.append(f"支持面（事实）：净利率 {format_ratio(e_nm.value)} [E-NM-{period}]")
    sig_eids = [eid for eid in ev if eid.startswith("E-SIG-")]
    recent_eids = [eid for eid in sig_eids if _period_index(eid.split("-")[-1]) is not None
                   and _target_idx is not None
                   and _period_index(eid.split("-")[-1]) >= _target_idx - RISKS_RECENT_QUARTERS + 1]
    if sig_eids:
        shown = recent_eids if recent_eids else sig_eids[-5:]
        extra = f"等共 {len(sig_eids)} 条" if len(sig_eids) > len(shown) else ""
        thesis.facts.append("反面因素（规则信号，近期优先）：" + "；".join(f"[{e}]" for e in shown)
                            + (f"（{extra}，完整清单见附录B）" if extra else ""))
    else:
        thesis.facts.append("反面因素：当前数据下 R1—R4 均未触发（未触发≠无风险）")
    memo.sections.append(thesis)

    # ---- 五、关键风险（近期突出 + 完整历史移附录B，不改变核验提取范围）----
    risks = MemoSection("risks", SECTION_TITLES["risks"], "deterministic")
    sig_list = [e for eid, e in ev.items() if eid.startswith("E-SIG-")]

    def _sig_order(e):
        return e.eid

    recent, history = [], []
    for e in sig_list:
        idx = _period_index(e.eid.split("-")[-1])
        (recent if _target_idx is not None and idx is not None
                and idx >= _target_idx - RISKS_RECENT_QUARTERS + 1 else history).append(e)
    if sig_list:
        for e in sorted(recent, key=_sig_order):
            level_cn = "较高关注" if "high" in (e.note or "") else "关注"
            risks.facts.append(f"- [{level_cn}] {e.derivation} [{e.eid}]")
        if history:
            tags = [e.eid.split("-")[-1] for e in history if _period_index(e.eid.split("-")[-1])]
            scope = f"{min(tags)}–{max(tags)}" if tags else "历史各期"
            risks.facts.append(
                f"- 另有 {len(history)} 条更早期间（{scope}）的风险信号未在此展开，"
                "完整清单见附录B（信号计算与统计口径不受展示分层影响）")
        risks.facts.append(f"风险摘要时间范围：目标报告期 {period} 往前 {RISKS_RECENT_QUARTERS} 个季度；"
                           f"全历史共 {len(sig_list)} 条。")
    else:
        risks.facts.append("当前数据下 R1—R4 规则均未触发信号；未触发不代表不存在风险。")
    risks.facts.append("规则阈值说明：R1—R4 为第一版启发式阈值（详见引擎模块），不代表行业标准。")
    memo.sections.append(risks)

    # ---- 六、情景分析（第一版定性，不出估值数字）----
    scenarios = MemoSection("scenarios", SECTION_TITLES["scenarios"], "llm", status="llm_pending")
    scenarios.facts.append(
        "第一版情景分析为定性描述；未接入股价、估值与行业数据，不生成目标价、"
        "预期收益率或估值数字。情景假设与推导 [待 LLM 生成或人工补充]。")
    memo.sections.append(scenarios)

    # ---- 七、持续跟踪指标 ----
    monitoring = MemoSection("monitoring", SECTION_TITLES["monitoring"], "deterministic")
    tracking = [
        "营业收入同比增速（累计与单季口径分别跟踪）",
        "毛利率与净利率变动",
        "经营活动现金流净额/净利润（利润质量）",
        "应收账款增速与收入增速差（R4 口径）",
    ]
    monitoring.facts.extend(f"- {t}" for t in tracking)
    memo.sections.append(monitoring)

    # ---- 八、待核实问题 ----
    open_q = MemoSection("open_questions", SECTION_TITLES["open_questions"], "deterministic")
    if book.missing:
        open_q.facts.extend(f"- {m}" for m in book.missing)
    open_q.facts.extend([
        "- 公司主营业务、行业格局与竞争地位（未接入经营材料，待补充）",
        "- 股价、估值与市场预期（未接入行情数据，本备忘录不含）",
        "- 定期报告公告原文与会计附注核验（当前依据为东财结构化数据，非公告 PDF 页码）",
    ])
    memo.sections.append(open_q)

    # ---- 附录A、证据清单 ----
    appendix = MemoSection("appendix", SECTION_TITLES["appendix"], "deterministic")
    appendix.facts.append(
        f"数据快照：目标报告期 {period}；各表实际采用期间 "
        + "、".join(f"{k}={v}" for k, v in snapshot.get("periods", {}).items())
        + f"；数据来源：{snapshot.get('data_source', '')}（公司名称待补充）")
    memo.sections.append(appendix)

    # ---- 附录B、历史风险信号完整清单（完整文本保留：核验提取范围不受展示分层影响）----
    appendix_b = MemoSection("appendix_signals", SECTION_TITLES["appendix_signals"],
                             "deterministic")
    all_sigs = sorted((e for eid, e in ev.items() if eid.startswith("E-SIG-")),
                      key=lambda x: x.eid)
    if all_sigs:
        tags = [e.eid.split("-")[-1] for e in all_sigs if _period_index(e.eid.split("-")[-1])]
        scope = f"{min(tags)}–{max(tags)}" if tags else "各历史期间"
        appendix_b.facts.append(
            f"共 {len(all_sigs)} 条风险信号（{scope}）；本清单为完整历史，"
            "与第五节的近期摘要仅为展示分层，统计与核验口径一致。")
        for e in all_sigs:
            appendix_b.facts.append(f"- {e.derivation} [{e.eid}]")
    else:
        appendix_b.facts.append("无历史风险信号。")
    memo.sections.append(appendix_b)
    return memo
