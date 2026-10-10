"""财务字段映射与标准化（阶段2·第一步，不含指标计算）。

数据口径结论（基于 600519 本地缓存全量 103 期实测验证，2026-10-10）
================================================================

1. 数值基准（单季 vs 累计）——已实测确认：
   - 资产负债表：**时点值**（各报告期末余额）。
   - 利润表 / 现金流量表：**年初至报告期末的累计值**，非单季度值。
     验证：29 个年度营业收入年内 0 次下降；2024 年报营业收入 1,709.0 亿、
     营业总收入 1,741.4 亿、归母净利润 862.3 亿、经营现金流净额 924.6 亿，
     均与公开年报一致。经营现金流净额 27 个年度中 2 个年度出现年内下降
     （单季经营现金流为负所致，不改变累计口径结论）。

2. 后续指标计算引擎必须遵守的处理要求：
   - 同比：相同报告期类型跨年直接比较（Q1 vs Q1、H1 vs H1、FY vs FY）。
   - 环比 / 单季分析：必须先还原单季值
     （Q单季 = 当期累计 - 年内上一报告期累计；Q1 单季 = Q1 累计值），
     严禁对累计值直接做环比。
   - 报告期类型可用 REPORT_TYPE 列（一季报/中报/三季报/年报）或
     report_date 的月日（03-31 / 06-30 / 09-30 / 12-31）判断。

3. 单位：元（人民币）。东财接口不随行附带单位字段，按数值量级与公开
   年报交叉核对确认（600519 最新期总资产 3.09e11 元 ≈ 3,091 亿，量级吻合）。

4. 会计恒等式校验：2007 年（新会计准则实施）起
   资产 = 负债 + 股东权益 在浮点精度内成立（相对偏差 < 1e-15）；
   2000-2006 年旧准则下少数股东权益单列于权益之外，恒等式最大偏差约
   0.86%。指标计算如使用 2007 年前历史数据需注意该口径差异。

5. 口径注意：OPERATE_INCOME = 营业收入（与公开年报一致）；
   TOTAL_OPERATE_INCOME = 营业总收入（另含利息收入等，两者差额为
   财务公司类业务收入）。本模块按用户要求映射"营业收入"取 OPERATE_INCOME。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from finsight.datasources.em_statements import STATEMENT_KINDS

logger = logging.getLogger(__name__)

REPORT_DATE_FIELD = "REPORT_DATE"
REPORT_TYPE_FIELD = "REPORT_TYPE"

# 各报表数值基准，供计算引擎读取（勿凭记忆硬编码）。
STATEMENT_VALUE_BASIS: dict[str, str] = {
    "balance": "时点值（报告期末余额）",
    "income": "累计值（年初至报告期末）",
    "cashflow": "累计值（年初至报告期末）",
}
CUMULATIVE_KINDS = ("income", "cashflow")

VALID_STATUS = ("mapped", "missing", "uncertain")


@dataclass(frozen=True)
class FieldSpec:
    """单个标准字段的映射登记项。

    status 含义：
    - mapped    原始列存在且口径已实测确认
    - missing   东财当前接口中不存在对应列
    - uncertain 列存在但含义/口径未确认，使用前需人工核验
    """

    code: str            # 标准字段代码（供程序引用）
    standard_name: str   # 标准中文名称
    original_field: str  # 东财原始列名（missing 时为空）
    statement: str       # balance / income / cashflow
    unit: str            # 计量单位
    status: str
    note: str = ""


FIELD_SPECS: list[FieldSpec] = [
    # ---- 资产负债表（时点值）----
    FieldSpec("total_assets", "总资产", "TOTAL_ASSETS", "balance", "元", "mapped",
              "合并口径，报告期末时点值"),
    FieldSpec("total_liabilities", "总负债", "TOTAL_LIABILITIES", "balance", "元", "mapped",
              "负债合计；2007 年前旧准则时期口径见模块说明第 4 条"),
    FieldSpec("total_current_assets", "流动资产", "TOTAL_CURRENT_ASSETS", "balance", "元", "mapped",
              "流动资产合计"),
    FieldSpec("total_current_liabilities", "流动负债", "TOTAL_CURRENT_LIAB", "balance", "元", "mapped",
              "流动负债合计；注意东财列名为 TOTAL_CURRENT_LIAB（无 -ILITIES 后缀，实测确认）"),
    FieldSpec("inventory", "存货", "INVENTORY", "balance", "元", "mapped", ""),
    FieldSpec("accounts_receivable", "应收账款", "ACCOUNTS_RECE", "balance", "元", "mapped", ""),
    FieldSpec("total_equity", "股东权益", "TOTAL_EQUITY", "balance", "元", "mapped",
              "所有者权益合计（含少数股东权益）；归母口径可用 TOTAL_PARENT_EQUITY 列，未列入正式映射"),
    # ---- 利润表（累计值）----
    FieldSpec("revenue", "营业收入", "OPERATE_INCOME", "income", "元", "mapped",
              "营业收入口径（2024 年报 1,709.0 亿与公开年报一致）；累计值；"
              "TOTAL_OPERATE_INCOME 为营业总收入（含利息收入等），非本字段"),
    FieldSpec("operate_cost", "营业成本", "OPERATE_COST", "income", "元", "mapped", "累计值"),
    FieldSpec("net_profit", "净利润", "NETPROFIT", "income", "元", "mapped",
              "净利润（含少数股东损益）；累计值"),
    FieldSpec("parent_net_profit", "归母净利润", "PARENT_NETPROFIT", "income", "元", "mapped",
              "归属于母公司股东的净利润；累计值"),
    # ---- 现金流量表（累计值）----
    FieldSpec("operating_cash_flow", "经营活动产生的现金流量净额", "NETCASH_OPERATE",
              "cashflow", "元", "mapped", "累计值；单季可能为负，年内累计值可能下降"),
]

FIELD_MAP: dict[str, FieldSpec] = {s.code: s for s in FIELD_SPECS}


def fields_for(kind: str) -> list[FieldSpec]:
    """返回某张报表的全部字段登记项。"""
    if kind not in STATEMENT_KINDS:
        raise ValueError(f"kind 必须是 {STATEMENT_KINDS} 之一，收到: {kind!r}")
    return [s for s in FIELD_SPECS if s.statement == kind]


def standardize_statement(kind: str, df: pd.DataFrame) -> pd.DataFrame:
    """把东财原始报表转为标准化 DataFrame。

    输出列：report_date（datetime，升序、去重）+ report_type（存在时透传）
    + 各标准字段 code 列（float，单位元）。
    映射状态非 mapped、或原始列缺失的字段整列置 NaN 并记录告警——
    缺失显式暴露，不猜测、不插补。
    """
    if REPORT_DATE_FIELD not in df.columns:
        raise KeyError(f"{kind} 原始数据缺少 {REPORT_DATE_FIELD} 列，无法标准化")

    # 先在原始索引上完成全部字段对齐，最后才排序去重，避免位置错位。
    out = pd.DataFrame({
        "report_date": pd.to_datetime(df[REPORT_DATE_FIELD], errors="coerce"),
    })
    if REPORT_TYPE_FIELD in df.columns:
        out["report_type"] = df[REPORT_TYPE_FIELD].astype("object")

    for spec in fields_for(kind):
        if spec.status == "mapped" and spec.original_field in df.columns:
            out[spec.code] = pd.to_numeric(df[spec.original_field], errors="coerce")
        else:
            if spec.status != "mapped":
                reason = f"映射状态为 {spec.status}（{spec.note or '无说明'}）"
            else:
                reason = f"原始列 {spec.original_field} 不在数据中"
            logger.warning("字段 %s(%s) 置为 NaN：%s", spec.standard_name, spec.code, reason)
            out[spec.code] = float("nan")

    n_nat = int(out["report_date"].isna().sum())
    if n_nat:
        logger.warning("%s 有 %d 行报告期无法解析，已剔除", kind, n_nat)
        out = out[out["report_date"].notna()]

    out = (out.sort_values("report_date", kind="stable")
              .drop_duplicates("report_date", keep="last")
              .reset_index(drop=True))
    return out
