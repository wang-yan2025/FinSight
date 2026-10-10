"""股票代码规范化的纯逻辑单元测试。"""
import pytest

from finsight.datasources.symbols import to_em_symbol


@pytest.mark.parametrize("raw,expected", [
    ("600519", "SH600519"),
    ("sh600519", "SH600519"),
    ("688981", "SH688981"),
    ("000001", "SZ000001"),
    ("300750", "SZ300750"),
    ("830799", "BJ830799"),
    ("430047", "BJ430047"),
    ("SH600519", "SH600519"),
])
def test_to_em_symbol_valid(raw, expected):
    assert to_em_symbol(raw) == expected


@pytest.mark.parametrize("bad", ["", "6005", "1234567", "abc123", "60051a", "123456"])
def test_to_em_symbol_invalid(bad):
    with pytest.raises(ValueError):
        to_em_symbol(bad)
