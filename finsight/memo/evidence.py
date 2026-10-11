"""备忘录证据层：结构化财务事实与证据 ID（全部由既有引擎计算，本层只组织）。

证据溯源边界（如实声明）：
- 数据来源为东方财富结构化财务报表（经 akshare 拉取、本地缓存快照）；
- 证据的"来源字段"指东财报表原始列名与报告期，**不是**上市公司公告 PDF
  的页码引用；公告原文、研报与经营材料均未接入，不做虚构。
- 计算类证据在 derivation 中记录公式与涉及字段，可复算核验。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field

import pandas as pd

from finsight.finance.indicators import (
    apply_indicator,
    current_ratio,
    debt_to_asset_ratio,
    gross_margin,
    inventory_turnover_series,
    net_margin,
    receivables_turnover_series,
    yoy_series,
)
from finsight.finance.quarterly import period_of
from finsight.ui.signals_view import evaluate_available

DATA_SOURCE = "东方财富三大报表（经 akshare，本地缓存快照）"

_STATEMENT_CN = {"balance": "资产负债表", "income": "利润表", "cashflow": "现金流量表"}


@dataclass(frozen=True)
class Evidence:
    """单条证据：稳定 ID + 来源 + 数值/口径 + 计算依据。"""

    eid: str                 # 如 E-REV-2024Q4
    label: str               # 人读描述
    statement: str           # income/balance/cashflow/derived
    source_field: str        # 东财原始列名，或"计算"标记
    report_date: str         # YYYY-MM-DD
    value: float | None
    unit: str                # 元 / % / 次 / 倍 / -
    basis: str               # 累计值 / 期末时点值 / 同比 / 年化比率 等
    derivation: str          # 原始依据或公式说明
    source: str = DATA_SOURCE
    note: str = ""


@dataclass
class EvidenceBook:
    """证据集 + 缺失与口径限制清单 + 数据快照信息。"""

    evidences: dict[str, Evidence] = dc_field(default_factory=dict)
    missing: list[str] = dc_field(default_factory=list)
    snapshot: dict = dc_field(default_factory=dict)

    def get(self, eid: str) -> Evidence | None:
        return self.evidences.get(eid)

    def ids(self) -> list[str]:
        return sorted(self.evidences)

    def add(self, ev: Evidence) -> None:
        self.evidences[ev.eid] = ev


def _period_tag(date: pd.Timestamp) -> str:
    p = period_of(date)
    return f"{p[0]}Q{p[1]}" if p else str(date)[:10]


def _period_at_or_before(std: pd.DataFrame | None, target: pd.Timestamp):
    """该表 <= target 的最新报告期；表缺失或无更早期间返回 (None, 说明)。"""
    if std is None or std.empty:
        return None, "报表不可用"
    dates = std.loc[std["report_date"] <= target, "report_date"]
    if dates.empty:
        return None, f"该表无 {target:%Y-%m-%d} 及之前的报告期"
    return dates.max(), ""


def _row_at(std: pd.DataFrame, date: pd.Timestamp) -> "pd.Series":
    return std.loc[std["report_date"] == date].iloc[0]


def _add_field_evidence(book: EvidenceBook, eid_prefix: str, label: str, statement: str,
                        source_field: str, date, value, unit: str, basis: str, note: str = ""):
    if value is None or pd.isna(value):
        book.missing.append(f"{label}（{_period_tag(date)}）缺失——{source_field} 无有效值")
        return None
    book.add(Evidence(
        eid=f"E-{eid_prefix}-{_period_tag(date)}", label=f"{label}（{_period_tag(date)}，{basis}）",
        statement=statement, source_field=source_field, report_date=f"{date:%Y-%m-%d}",
        value=float(value), unit=unit, basis=basis,
        derivation=f"来源字段 {source_field}（{_STATEMENT_CN.get(statement, statement)}，{basis}）",
        note=note,
    ))
    return float(value)


def build_evidence_book(code: str, std_income, std_balance, std_cashflow,
                        sources: dict | None = None, target_date=None) -> EvidenceBook:
    """为目标报告期构建证据集（income 必需，balance/cashflow 可缺失并降级）。

    target_date 缺省取利润表最新报告期；各表独立取 <= target 的最新报告期，
    不一致时在 missing 中标明实际采用期间，不默默混合。
    """
    book = EvidenceBook()
    if std_income is None or std_income.empty:
        book.missing.append("利润表不可用：无法构建备忘录财务证据")
        book.snapshot = {"code": code, "income_period": None}
        return book

    target = pd.Timestamp(target_date) if target_date is not None else std_income["report_date"].max()

    used_periods, per_source = {}, dict(sources or {})
    p_inc, _ = _period_at_or_before(std_income, target)
    p_bal, bal_note = _period_at_or_before(std_balance, target)
    p_cf, cf_note = _period_at_or_before(std_cashflow, target)

    if p_bal is not None and bal_note == "" and p_bal != p_inc:
        book.missing.append(f"资产负债表实际采用 {_period_tag(p_bal)}（无 {target:%Y-%m-%d} 期数据）")
    if p_cf is not None and cf_note == "" and p_cf != p_inc:
        book.missing.append(f"现金流量表实际采用 {_period_tag(p_cf)}（无 {target:%Y-%m-%d} 期数据）")
    if p_bal is None and std_balance is not None:
        book.missing.append(f"资产负债表：{bal_note}")
    if p_cf is None and std_cashflow is not None:
        book.missing.append(f"现金流量表：{cf_note}")

    used_periods = {"income": p_inc, "balance": p_bal, "cashflow": p_cf}
    book.snapshot = {
        "code": code, "target_period": _period_tag(target), "target_date": f"{target:%Y-%m-%d}",
        "periods": {k: (_period_tag(v) if v is not None else None) for k, v in used_periods.items()},
        "sources": per_source, "company_name": None,  # 公司名称未接入，待补充
        "data_source": DATA_SOURCE,
    }

    inc = _row_at(std_income, p_inc)
    tag = _period_tag(p_inc)

    # ---- 基础字段证据（利润表，累计口径）----
    rev = _add_field_evidence(book, "REV", "营业收入", "income", "OPERATE_INCOME",
                              p_inc, inc.get("revenue"), "元", "累计值")
    np_ = _add_field_evidence(book, "NP", "净利润", "income", "NETPROFIT",
                              p_inc, inc.get("net_profit"), "元", "累计值")
    _add_field_evidence(book, "PNP", "归母净利润", "income", "PARENT_NETPROFIT",
                        p_inc, inc.get("parent_net_profit"), "元", "累计值")
    cost = _add_field_evidence(book, "COST", "营业成本", "income", "OPERATE_COST",
                               p_inc, inc.get("operate_cost"), "元", "累计值")

    # ---- 同比证据（复用 yoy_series，累计口径；含状态分类）----
    yoy_frames = {}
    for col, prefix, name in (("revenue", "REVYOY", "营业收入同比"),
                              ("net_profit", "NPYOY", "净利润同比"),
                              ("parent_net_profit", "PNPYOY", "归母净利润同比")):
        out = yoy_series(std_income, col, basis="cumulative")
        row = out.loc[out["report_date"] == p_inc]
        if row.empty:
            book.missing.append(f"{name}（{tag}）不可得：无同比序列")
            continue
        r = row.iloc[0]
        yoy_frames[col] = r
        if pd.isna(r["yoy"]):
            book.missing.append(f"{name}（{tag}）不可得——{r['reason'] or '上年同期数据缺失'}")
        else:
            book.add(Evidence(
                eid=f"E-{prefix}-{tag}", label=f"{name}（{tag}，累计口径）",
                statement="derived", source_field="计算",
                report_date=f"{p_inc:%Y-%m-%d}", value=float(r["yoy"]), unit="%",
                basis="同比（同期累计对齐）",
                derivation=f"({tag} 累计值 − 上年同期累计值)/|上年同期累计值|；"
                           f"状态分类 {r['status']}（yoy_status）",
                note=f"状态：{r['status']}",
            ))

    # ---- 比率证据（复用标量指标 + apply_indicator）----
    def _ratio_evidence(prefix, label, stmt, df, fn, col_map, date, unit, derivation):
        if df is None or df.empty:
            return
        out = apply_indicator(df, fn, col_map)
        row = out.loc[out["report_date"] == date]
        if row.empty:
            return
        r = row.iloc[0]
        if pd.isna(r["value"]):
            book.missing.append(f"{label}（{_period_tag(date)}）未计算——{r['reason']}")
            return
        book.add(Evidence(
            eid=f"E-{prefix}-{_period_tag(date)}", label=f"{label}（{_period_tag(date)}）",
            statement=stmt, source_field="计算", report_date=f"{date:%Y-%m-%d}",
            value=float(r["value"]), unit=unit, basis="比率", derivation=derivation,
        ))

    if rev is not None and cost is not None:
        _ratio_evidence("GM", "毛利率", "income", std_income, gross_margin,
                        {"revenue": "revenue", "operate_cost": "operate_cost"}, p_inc, "%",
                        "(营业收入 − 营业成本)/营业收入（同期累计口径）")
    _ratio_evidence("NM", "净利率", "income", std_income, net_margin,
                    {"net_profit": "net_profit", "revenue": "revenue"}, p_inc, "%",
                    "净利润/营业收入（同期累计口径）")

    # ---- 资产负债表证据（期末时点）----
    if p_bal is not None:
        bal = _row_at(std_balance, p_bal)
        _add_field_evidence(book, "ASSETS", "总资产", "balance", "TOTAL_ASSETS",
                            p_bal, bal.get("total_assets"), "元", "期末时点值")
        _add_field_evidence(book, "LIAB", "总负债", "balance", "TOTAL_LIABILITIES",
                            p_bal, bal.get("total_liabilities"), "元", "期末时点值")
        _add_field_evidence(book, "AR", "应收账款", "balance", "ACCOUNTS_RECE",
                            p_bal, bal.get("accounts_receivable"), "元", "期末时点值")
        _ratio_evidence("DAR", "资产负债率", "balance", std_balance, debt_to_asset_ratio,
                        {"total_liabilities": "total_liabilities", "total_assets": "total_assets"},
                        p_bal, "%", "总负债/总资产（期末时点）")
        _ratio_evidence("CR", "流动比率", "balance", std_balance, current_ratio,
                        {"total_current_assets": "total_current_assets",
                         "total_current_liabilities": "total_current_liabilities"},
                        p_bal, "倍", "流动资产/流动负债（期末时点）")
        # 周转率（年化，累计口径）
        for prefix, label, series in (("ART", "应收账款周转率", receivables_turnover_series(std_balance, std_income)),
                                      ("IVT", "存货周转率", inventory_turnover_series(std_balance, std_income))):
            row = series.loc[series["report_date"] == p_inc]
            if row.empty:
                continue
            r = row.iloc[0]
            if pd.isna(r["value"]):
                book.missing.append(f"{label}（{tag}）未计算——{r['reason']}")
            else:
                book.add(Evidence(
                    eid=f"E-{prefix}-{tag}", label=f"{label}（{tag}，年化）",
                    statement="derived", source_field="计算", report_date=f"{p_inc:%Y-%m-%d}",
                    value=float(r["value"]), unit="次", basis="年化周转率（累计口径）",
                    derivation=f"期间流量×12/期间月数/平均余额（期初=年初余额）；平均余额 "
                               f"{r['average_balance']:.0f} 元（期初 {r['begin_balance']:.0f}，期末 {r['end_balance']:.0f}）",
                ))

    # ---- 现金流量表证据（累计）----
    if p_cf is not None:
        cf = _row_at(std_cashflow, p_cf)
        ocf = _add_field_evidence(book, "OCF", "经营活动现金流净额", "cashflow", "NETCASH_OPERATE",
                                  p_cf, cf.get("operating_cash_flow"), "元", "累计值")
        if ocf is not None and np_ is not None and np_ > 0:
            book.add(Evidence(
                eid=f"E-OCFNPR-{tag}", label=f"经营现金流/净利润（{tag}）",
                statement="derived", source_field="计算", report_date=f"{p_cf:%Y-%m-%d}",
                value=ocf / np_, unit="倍", basis="利润质量比率",
                derivation="经营活动现金流净额/净利润（同报告期累计口径；分母为正时计算）",
            ))

    # ---- 风险信号证据（复用规则引擎）----
    analysis = evaluate_available(std_income, std_cashflow, std_balance, company=code)
    for s in analysis.signals:
        # 口径标注：使用信号引擎实际输出（R1/R2/R3 的 metrics["口径"]；
        # R4 无该键，其营业收入对照固定为累计口径），不统一假设。
        raw_basis = s.metrics.get("口径")
        if not raw_basis:
            raw_basis = "累计" if any("（累计口径）" in k for k in s.metrics) else ""
        basis_label = ("单季口径" if "单季" in str(raw_basis)
                       else "累计口径" if "累计" in str(raw_basis) else "口径未标注")
        derivation = f"{s.period}（{basis_label}）：{s.condition}"
        book.add(Evidence(
            eid=f"E-SIG-{s.rule_id}-{s.period}", label=f"风险信号 {s.rule_id} {s.name}（{s.period}）",
            statement="derived", source_field="规则引擎",
            report_date=f"{s.report_date:%Y-%m-%d}" if not pd.isna(s.report_date) else "",
            value=None, unit="-", basis="规则触发（risk_hint）",
            derivation=derivation, note=f"级别 {s.level}；口径 {basis_label}；{s.name}",
        ))
    book.snapshot["signals_high"] = sum(1 for s in analysis.signals if s.level == "high")
    book.snapshot["signals_medium"] = sum(1 for s in analysis.signals if s.level == "medium")
    book.snapshot["signals_total"] = len(analysis.signals)
    book.missing.extend(analysis.unevaluated)
    return book
