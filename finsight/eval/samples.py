"""扩展评测样本集：31 条人工标注预期（独立于待测实现编写）。

数据说明：
- 参考数据为【人工构造的固定财务数据】（非真实公司数据），量级真实：
  2023 单季收入/净利 100/50 亿；2024 单季收入 120/110/90/95 → FY 415 亿、
  净利 10/−5/−10/−15 → FY −20 亿；毛利率 FY=(415−247)/415≈40.5%；
  收入同比 FY=+3.75%、H1=+15%、Q2 单季=+10%；净利 FY=−110%（由盈转亏）、
  H1=−95%；总资产 1000 亿、总负债 600 亿、资产负债率 60%、流动比率 1.25；
  应收账款 2024Q2=150 亿、2024Q4=130 亿；经营现金流 FY=110 亿。
- 每条样本标注：类别 / 是否可提取（预期）/ 预期核验状态 / 预期定位键。
- "not_extractable" 的样本用于统计提取覆盖缺口（不默认通过）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    sid: str
    category: str
    text: str
    expect_extracted: bool            # 人工预期：保守提取器能否提取
    expect_status: str | None         # 预期核验状态（None=预期不产生声明）
    # 定位键（metric, period, display）——人工按数据手写，独立于实现
    key: tuple | None = None
    note: str = ""


SAMPLES: list[Sample] = [
    # ---- 正确财务事实（6）----
    Sample("S01", "正确", "2024年营业收入为415亿元。", True, "pass", ("revenue", "2024Q4", "415亿元")),
    Sample("S02", "正确", "2024年上半年净利润为5亿元。", True, "pass", ("net_profit", "2024Q2", "5亿元")),
    Sample("S03", "正确", "2024年第四季度营业收入为95亿元。", True, "pass", ("revenue", "2024Q4", "95亿元")),
    Sample("S04", "正确状态", "2024年净利润由盈转亏。", True, "pass", ("net_profit_yoy", None, "由盈转亏")),
    Sample("S05", "正确", "2024年资产负债率为60%。", True, "pass", ("debt_to_asset_ratio", "2024Q4", "60%")),
    Sample("S06", "正确", "2024年经营活动现金流净额为110亿元。", True, "pass", ("operating_cash_flow", "2024Q4", "110亿元")),
    # ---- 金额错误（3）----
    Sample("S07", "金额错误", "2024年营业收入为480亿元。", True, "fail", ("revenue", "2024Q4", "480亿元")),
    Sample("S08", "期间错误", "2023年营业收入为415亿元。", True, "fail", ("revenue", "2023Q4", "415亿元")),
    Sample("S09", "正负号错误", "2024年净利润为20亿元。", True, "fail", ("net_profit", "2024Q4", "20亿元")),
    # ---- 单位错误（2）----
    Sample("S10", "单位错误", "2024年营业收入为415万元。", True, "fail", ("revenue", "2024Q4", "415万元")),
    Sample("S11", "pp/百分比混淆", "2024年营业收入同比增长3.75个百分点。", True, "fail", ("revenue_yoy", "2024Q4", "3.75个百分点")),
    # ---- 指标错配（2）----
    Sample("S12", "指标错配", "2024年净利润为415亿元。", True, "fail", ("net_profit", "2024Q4", "415亿元")),
    Sample("S13", "指标错配", "2024年总负债为1000亿元。", True, "fail", ("total_liabilities", "2024Q4", "1000亿元")),
    # ---- 证据引用错误（3）----
    Sample("S14", "引用不存在", "2024年营业收入为415亿元 [E-FAKE-2024Q4]。", True, "fail", ("revenue", "2024Q4", "415亿元")),
    Sample("S15", "跨公司引用", "2024年营业收入为415亿元 [E-OTHER-2024Q4]。", True, "fail", ("revenue", "2024Q4", "415亿元")),
    Sample("S16", "引用字段不符", "2024年净利润为-20亿元 [E-REV-2024Q4]。", True, "fail", ("net_profit", "2024Q4", "-20亿元")),
    # ---- 口径混淆（2）----
    Sample("S17", "累计误作单季", "2024年第二季度营业收入为230亿元。", True, "fail", ("revenue", "2024Q2", "230亿元")),
    Sample("S18", "期间标签歧义", "2024Q4营业收入同比增长3.75%。", True, "needs_review", ("revenue_yoy", "2024Q4", "3.75%")),
    # ---- 状态词错误（1）----
    Sample("S19", "状态词错误", "2024年净利润同比扭亏为盈。", True, "fail", ("net_profit_yoy", None, "扭亏为盈")),
    # ---- 数据缺失/歧义（3）----
    Sample("S20", "超出数据范围", "2022年营业收入为300亿元。", True, "insufficient_evidence", ("revenue", "2022Q4", "300亿元")),
    Sample("S21", "pp基期歧义", "2024年营业收入同比上升5个百分点。", True, "needs_review", ("revenue_yoy", "2024Q4", "5个百分点")),
    Sample("S22", "无资产负债表", "2024年资产负债率为60%。", True, "insufficient_evidence", ("debt_to_asset_ratio", "2024Q4", "60%"), "仅在 income-only 参考下核验"),
    # ---- 定性结论（2）----
    Sample("S23", "定性无引用", "公司拥有行业领先的竞争壁垒。", True, "insufficient_evidence", (None, None, None)),
    Sample("S24", "定性带无关引用", "公司毛利率优势显著 [E-REV-2024Q4]。", True, "needs_review", (None, None, None)),
    # ---- 提取困难样本（4，预期不可提取或部分提取）----
    Sample("S25", "隐含主语", "同比增长10%。", False, None, None, "无指标词：保守不提取"),
    Sample("S26", "复合句", "2024年营业收入和净利润分别为415亿元和-20亿元。", True, "pass", ("revenue", "2024Q4", "415亿元"), "仅提取首个指标-数值对；净利值不计为已核验"),
    Sample("S27", "中文数字", "2024年营业收入为一千二百亿元。", False, None, None, "中文数字：保守不提取"),
    Sample("S28", "科学计数法", "经营活动现金流净额为2.301e+08元。", False, None, None, "科学计数法：保守不提取"),
    # ---- 修订场景（3，人工构造编辑，预期结果独立标注）----
    Sample("S29", "修订成功", "2024年营业收入为415亿元。", True, "pass", ("revenue", "2024Q4", "415亿元"), "由 S07 经确定性修订（480→415）后再核验"),
    Sample("S30", "修订致提取丢失", "营业收入表现良好。", False, None, None, "人工构造：修订删除数值后声明不可提取→coverage_lost，不计修复"),
    Sample("S31", "修订引入错误", "2024年营业收入为480亿元。", True, "fail", ("revenue", "2024Q4", "480亿元"), "人工构造：把正确值改为错误值→regression 必须被发现"),
]

# 证据登记（用于 S14—S16 引用核验；含一个他公司条目）
EVIDENCE_REGISTRY = {
    "E-REV-2024Q4": ("BENCH", "2024Q4", "revenue", 415e8),
    "E-NP-2024Q4": ("BENCH", "2024Q4", "net_profit", -20e8),
    "E-AR-2024Q2": ("BENCH", "2024Q2", "accounts_receivable", 150e8),
    "E-OTHER-2024Q4": ("SH999999", "2024Q4", "revenue", 415e8),
}

# 统计分母（人工固定）：S01–S24 中预期可提取 = 24 条；
# S25/S27/S28 预期不可提取（提取覆盖缺口统计）；S26 部分提取。
ANNOTATED_EXTRACTABLE_MAIN = 24
ANNOTATED_NOT_EXTRACTABLE = 3          # S25/S27/S28
