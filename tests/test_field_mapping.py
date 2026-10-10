"""字段映射与标准化测试：全部基于本地样例（tests/fixtures/），不依赖网络。

样例来源与裁剪方式见 tests/fixtures/README.md；
累计/时点口径结论的验证记录见 finsight/finance/field_mapping.py 模块 docstring。
"""
from pathlib import Path

import pandas as pd
import pytest

from finsight.finance.field_mapping import (
    CUMULATIVE_KINDS,
    FIELD_MAP,
    FIELD_SPECS,
    REPORT_DATE_FIELD,
    STATEMENT_VALUE_BASIS,
    fields_for,
    standardize_statement,
)

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_FILES = {
    "balance": "SH600519_balance_sample.csv",
    "income": "SH600519_income_sample.csv",
    "cashflow": "SH600519_cashflow_sample.csv",
}

# 比赛要求的必须覆盖字段（标准中文名）
REQUIRED_NAMES = {
    "总资产", "总负债", "流动资产", "流动负债", "存货", "应收账款", "股东权益",
    "营业收入", "营业成本", "净利润", "归母净利润", "经营活动产生的现金流量净额",
}


@pytest.fixture(scope="module")
def raw_statements():
    return {
        kind: pd.read_csv(FIXTURES / name, encoding="utf-8-sig")
        for kind, name in FIXTURE_FILES.items()
    }


# ---------- 登记表自身的一致性（纯逻辑，不依赖样例） ----------

def test_spec_integrity():
    codes = [s.code for s in FIELD_SPECS]
    assert len(codes) == len(set(codes)), "标准字段代码必须唯一"
    for s in FIELD_SPECS:
        assert s.status in ("mapped", "missing", "uncertain")
        assert s.statement in FIXTURE_FILES, f"{s.code} 的 statement 非法"
        assert s.unit == "元"
        if s.status == "mapped":
            assert s.original_field, f"{s.code} mapped 但缺少原始列名"
        else:
            assert s.note, f"{s.code} 状态为 {s.status}，必须填写说明"


def test_required_fields_covered():
    names = {s.standard_name for s in FIELD_SPECS}
    missing = REQUIRED_NAMES - names
    assert not missing, f"要求的字段未登记: {missing}"


def test_value_basis_documented():
    for kind in FIXTURE_FILES:
        assert kind in STATEMENT_VALUE_BASIS
    assert set(CUMULATIVE_KINDS) == {"income", "cashflow"}


# ---------- 映射与本地样例的一致性 ----------

def test_mapped_original_fields_present(raw_statements):
    for kind, df in raw_statements.items():
        for spec in fields_for(kind):
            if spec.status == "mapped":
                assert spec.original_field in df.columns, (
                    f"{spec.code} 声明映射到 {spec.original_field}，但样例中不存在"
                )


def test_standardize_balance(raw_statements):
    std = standardize_statement("balance", raw_statements["balance"])
    expected_cols = ["report_date", "report_type"] + [s.code for s in fields_for("balance")]
    assert list(std.columns) == expected_cols
    assert std["report_date"].notna().all()
    assert std["report_date"].is_monotonic_increasing
    assert not std["report_date"].duplicated().any()
    for spec in fields_for("balance"):
        assert std[spec.code].notna().any(), f"{spec.code} 不应全为 NaN"
        assert pd.api.types.is_float_dtype(std[spec.code])


def test_standardize_preserves_values_not_positions(raw_statements):
    """样例原始数据为报告期倒序；标准化必须按报告期对齐数值而非按行位置。"""
    raw = raw_statements["income"]
    std = standardize_statement("income", raw).set_index("report_date")
    raw_date = pd.to_datetime(raw[REPORT_DATE_FIELD])
    for i in range(len(raw)):
        row = std.loc[raw_date.iloc[i]]
        expected = float(raw["OPERATE_INCOME"].iloc[i])
        assert row["revenue"] == pytest.approx(expected), f"报告期 {raw_date.iloc[i].date()} 数值错位"


def test_balance_accounting_identity(raw_statements):
    """新会计准则（2007 起）：资产 = 负债 + 股东权益，浮点精度内成立。"""
    std = standardize_statement("balance", raw_statements["balance"])
    m = std["report_date"] >= pd.Timestamp("2007-01-01")
    rel = (
        (std.loc[m, "total_assets"] - std.loc[m, "total_liabilities"] - std.loc[m, "total_equity"]).abs()
        / std.loc[m, "total_assets"].abs()
    )
    assert rel.max() < 1e-9, f"会计恒等式相对偏差过大: {rel.max()}"


@pytest.mark.parametrize("kind,col", [
    ("income", "revenue"),
    ("income", "parent_net_profit"),
    ("cashflow", "operating_cash_flow"),
])
def test_cumulative_basis_within_year(raw_statements, kind, col):
    """利润表/现金流量表为累计值的实证检验：样例各年度内数值不下降。

    600519 样例期间（2024-2026）各季收入/利润/经营现金流均为正，
    若数据是单季度值，年内序列不会呈现此种单调累计形态。
    """
    std = standardize_statement(kind, raw_statements[kind])
    s = std.set_index("report_date")[col]
    for year in sorted({d.year for d in s.index}):
        within = s[s.index.year == year]
        diffs = within.diff().dropna()
        assert (diffs >= -1e-6).all(), (
            f"{kind}.{col} 在 {year} 年内出现下降，累计口径存疑: {within.to_dict()}"
        )


# ---------- 缺失与容错 ----------

def test_standardize_missing_columns_yield_nan():
    df = pd.DataFrame({
        REPORT_DATE_FIELD: ["2024-12-31 00:00:00", "2024-09-30 00:00:00"],
    })
    std = standardize_statement("cashflow", df)
    assert len(std) == 2
    assert std["operating_cash_flow"].isna().all(), "缺失字段必须显式置 NaN"


def test_standardize_without_report_date_raises():
    with pytest.raises(KeyError):
        standardize_statement("balance", pd.DataFrame({"TOTAL_ASSETS": [1.0]}))


def test_fields_for_invalid_kind():
    with pytest.raises(ValueError):
        fields_for("cash")


def test_field_map_lookup():
    spec = FIELD_MAP["revenue"]
    assert spec.standard_name == "营业收入"
    assert spec.original_field == "OPERATE_INCOME"
