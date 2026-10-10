"""股票代码规范化。"""


def to_em_symbol(code: str) -> str:
    """将常见写法的 A 股代码转为东财格式。

    '600519' -> 'SH600519'；'000001' -> 'SZ000001'；'830799' -> 'BJ830799'。
    已带 SH/SZ/BJ 前缀的（不区分大小写）原样规范化返回。
    """
    code = code.strip().upper()
    if code.startswith(("SH", "SZ", "BJ")):
        return code
    if not code.isdigit() or len(code) != 6:
        raise ValueError(f"无法识别的股票代码: {code!r}")
    if code.startswith("6"):
        return f"SH{code}"
    if code.startswith(("0", "3")):
        return f"SZ{code}"
    if code.startswith(("4", "8")):
        return f"BJ{code}"
    raise ValueError(f"无法识别的股票代码: {code!r}")
