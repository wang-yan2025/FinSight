"""股票代码规范化与名称搜索。

支持输入格式：
- 纯数字代码：600519、000001、300750
- 带前缀：SH600519、sz000001
- 带后缀：600519.SH、000001.SZ、600519.ss
- 股票名称（模糊搜索）：茅台、贵州茅台、宁德时代
- 全角数字自动转半角
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# 全角 → 半角数字映射
_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

# 交易所后缀 → 前缀映射
_SUFFIX_MAP = {".SH": "SH", ".SS": "SH", ".SZ": "SZ", ".BJ": "BJ"}

_CACHE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "cache" / "_stock_list.json"


def _normalize_input(raw: str) -> str:
    """清理用户输入：去空白、全角转半角、统一大写。"""
    return raw.strip().translate(_FW_DIGITS).upper()


def _parse_prefixed(code: str) -> str | None:
    """解析已带 SH/SZ/BJ 前缀的代码。"""
    m = re.match(r"^(SH|SZ|BJ)(\d{6})$", code)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return None


def _parse_suffixed(code: str) -> str | None:
    """解析带 .SH/.SS/.SZ/.BJ 后缀的代码（如 600519.SH）。"""
    for suffix, prefix in _SUFFIX_MAP.items():
        if code.endswith(suffix):
            digits = code[:-len(suffix)]
            if digits.isdigit() and len(digits) == 6:
                return f"{prefix}{digits}"
    return None


def _parse_bare(code: str) -> str | None:
    """解析纯 6 位数字代码，按首位判交易所。"""
    if not code.isdigit() or len(code) != 6:
        return None
    if code.startswith("6"):
        return f"SH{code}"
    if code.startswith(("0", "3")):
        return f"SZ{code}"
    if code.startswith(("4", "8")):
        return f"BJ{code}"
    return None


def to_em_symbol(code: str) -> str:
    """将常见写法的 A 股代码/名称转为东财格式。

    支持格式见模块 docstring；无法识别时抛 ValueError（含提示）。
    """
    normalized = _normalize_input(code)

    # 依次尝试各种解析路径
    for parser in (_parse_prefixed, _parse_suffixed, _parse_bare):
        result = parser(normalized)
        if result:
            return result

    # 非代码格式 → 尝试按股票名称搜索
    if not normalized.isdigit():
        match = search_by_name(normalized)
        if match:
            return match[0][1]  # 返回第一个匹配的 EM 代码

    # 所有解析路径失败 → 给出有用的错误信息
    examples = "600519 / SH600519 / 600519.SH / 茅台"
    raise ValueError(
        f"无法识别的股票代码或名称: '{code.strip()}'（支持格式：{examples}）")


def _load_stock_list() -> list[dict]:
    """加载 A 股代码-名称对照表（分页拉取 + 24h 缓存）。"""
    import time as _time
    from finsight.config import CACHE_DIR, ensure_dirs
    ensure_dirs()
    if _CACHE_FILE.exists():
        try:
            if (_time.time() - os.path.getmtime(_CACHE_FILE)) / 3600 < 24:
                return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    if os.environ.get("FINSIGHT_OFFLINE") == "1":
        return []
    try:
        import requests
        s = requests.Session()
        s.trust_env = False
        stocks: list[dict] = []
        page = 1
        while True:
            resp = s.get(
                "http://push2.eastmoney.com/api/qt/clist/get",
                params={
                    "pn": str(page), "pz": "100", "po": "1", "np": "1",
                    "fltt": "2", "invt": "2", "fid": "f12",
                    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                    "fields": "f12,f13,f14",
                },
                headers={"User-Agent": "Mozilla/5.0",
                         "Referer": "https://quote.eastmoney.com/"},
                timeout=15)
            data = resp.json().get("data", {})
            diff = data.get("diff", [])
            if not diff:
                break
            for d in diff:
                digits = str(d.get("f12", ""))
                market = int(d.get("f13", 0))
                name = str(d.get("f14", ""))
                prefix = "SH" if market == 1 else ("BJ" if digits.startswith(("4", "8")) and market == 0 else "SZ")
                stocks.append({"code": digits, "name": name, "em": f"{prefix}{digits}"})
            if len(stocks) >= data.get("total", 0):
                break
            page += 1
        if stocks:
            _CACHE_FILE.write_text(json.dumps(stocks, ensure_ascii=False), encoding="utf-8")
            return stocks
    except Exception as exc:
        logger.warning("获取股票列表失败：%s", exc)
    return []


def search_by_name(query: str) -> list[tuple[str, str, str]]:
    """按名称模糊搜索，返回 [(名称, EM代码, 纯数字代码), ...]（最多 10 条）。"""
    query = query.strip()
    if not query or query.isdigit():
        return []
    stocks = _load_stock_list()
    # 精确 > 开头 > 包含
    exact = [(s["name"], s["em"], s["code"]) for s in stocks if s["name"] == query]
    starts = [(s["name"], s["em"], s["code"]) for s in stocks if s["name"].startswith(query) and s["name"] != query]
    contains = [(s["name"], s["em"], s["code"]) for s in stocks if query in s["name"] and not s["name"].startswith(query)]
    return (exact + starts + contains)[:10]
