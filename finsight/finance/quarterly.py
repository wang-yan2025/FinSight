"""单季度值还原（阶段2·第二步之一，纯计算）。

口径与规则
==========
- 输入为 standardize_statement 输出的标准化报表（单位：元，已在
  field_mapping 阶段按数值量级与公开年报交叉核对确认；利润表/现金流量表
  为年初至报告期末累计值）。
- 财年 = 自然年（A 股口径）。报告期以 report_date 的月日判定：
  03-31=Q1、06-30=Q2、09-30=Q3、12-31=Q4；非标准季末日期不参与还原。
- 单季还原公式（同一公司、同一财年内按（财年, 报告期）查表对齐，
  绝不对相邻日历日期做位置差分）：
      Q1 单季 = Q1 累计
      Qn 单季 = Qn 累计 − Q(n−1) 累计   (n = 2, 3, 4)
- 累计值不保证单调（单季可为负，经营现金流尤甚），单季为负属正常业务
  现象，不判定为数据错误。
- 缺少任一侧累计值时该期单季值置 NaN，并在 notes 中说明原因；
  不猜测、不插补。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field

import pandas as pd

QUARTER_ENDS = {(3, 31): 1, (6, 30): 2, (9, 30): 3, (12, 31): 4}


def period_of(report_date: pd.Timestamp) -> tuple[int, int] | None:
    """报告期归类：返回 (财年, 季度)；非标准季末或空值返回 None。"""
    if pd.isna(report_date):
        return None
    quarter = QUARTER_ENDS.get((report_date.month, report_date.day))
    if quarter is None:
        return None
    return (report_date.year, quarter)


def quarter_months(quarter: int) -> int:
    """报告期覆盖的自然月数：Q1=3、H1=6、Q3=9、FY=12。"""
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"quarter 必须是 1-4，收到 {quarter!r}")
    return 3 * quarter


@dataclass
class DerivationResult:
    """单季还原结果。

    frame: 与输入同行的 year / quarter / <列名>__sq（单季值，元）
    notes: 缺失与跳过原因（人读），每条一处
    """

    frame: pd.DataFrame
    notes: list[str] = dc_field(default_factory=list)


def derive_single_quarters(std: pd.DataFrame, columns: list[str]) -> DerivationResult:
    """把累计值列还原为单季度值列（输出列名 = 原列名 + "__sq"）。

    仅适用于累计口径报表（利润表 / 现金流量表）的标准字段列。
    """
    for col in columns:
        if col not in std.columns:
            raise KeyError(f"列 {col!r} 不在标准化报表中")

    periods = [period_of(d) for d in std["report_date"]]
    frame = pd.DataFrame({
        "report_date": std["report_date"].to_numpy(),
        "year": [p[0] if p else math.nan for p in periods],
        "quarter": [p[1] if p else math.nan for p in periods],
    })
    notes: list[str] = []

    for col in columns:
        cum: dict[tuple[int, int], float] = {}
        for p, v in zip(periods, std[col]):
            if p is not None and pd.notna(v):
                cum[p] = float(v)

        singles: list[float] = []
        for i, p in enumerate(periods):
            if p is None:
                notes.append(f"{col}: 报告期 {std['report_date'].iloc[i]:%Y-%m-%d} 非标准季末，跳过单季还原")
                singles.append(math.nan)
                continue
            year, quarter = p
            current = cum.get((year, quarter))
            if quarter == 1:
                if current is None:
                    notes.append(f"{col}: {year}Q1 当期累计值缺失")
                singles.append(current if current is not None else math.nan)
                continue
            prev = cum.get((year, quarter - 1))
            if current is None:
                notes.append(f"{col}: {year}Q{quarter} 当期累计值缺失")
                singles.append(math.nan)
            elif prev is None:
                notes.append(f"{col}: {year}Q{quarter} 单季未还原——缺少 {year}Q{quarter - 1} 累计值")
                singles.append(math.nan)
            else:
                singles.append(current - prev)
        frame[f"{col}__sq"] = singles

    return DerivationResult(frame=frame, notes=notes)
