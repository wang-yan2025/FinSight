"""东方财富三大报表数据源（经 akshare），带本地 CSV 缓存。

设计说明：
- akshare 导入较慢（数秒），延迟到实际取数时才导入。
- 缓存文件名：{EM代码}_{报表类型}.csv，UTF-8-SIG 编码（Excel 可直接打开）。
- 未过 TTL 的缓存直接使用；拉取失败时若存在过期缓存则回退使用并在日志告警。
- 返回的 DataFrame 保留 akshare 原始列，字段映射由上层（阶段2）负责。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

import pandas as pd

from finsight.config import CACHE_DIR, CACHE_TTL_HOURS, ensure_dirs
from finsight.datasources.symbols import to_em_symbol

logger = logging.getLogger(__name__)

STATEMENT_KINDS = ("balance", "income", "cashflow")
KIND_CN = {"balance": "资产负债表", "income": "利润表", "cashflow": "现金流量表"}


def _akshare_fetchers() -> dict[str, Callable[[str], pd.DataFrame]]:
    import akshare as ak  # 延迟导入

    return {
        "balance": ak.stock_balance_sheet_by_report_em,
        "income": ak.stock_profit_sheet_by_report_em,
        "cashflow": ak.stock_cash_flow_sheet_by_report_em,
    }


def _cache_path(em_symbol: str, kind: str) -> Path:
    return CACHE_DIR / f"{em_symbol}_{kind}.csv"


def _cache_age_hours(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 3600


def fetch_statement(code: str, kind: str, refresh: bool = False) -> pd.DataFrame:
    """拉取单张报表（按报告期，优先读缓存）。

    refresh=True 时强制重新拉取并覆盖缓存。
    """
    if kind not in STATEMENT_KINDS:
        raise ValueError(f"kind 必须是 {STATEMENT_KINDS} 之一，收到: {kind!r}")
    ensure_dirs()
    em_symbol = to_em_symbol(code)
    path = _cache_path(em_symbol, kind)

    if not refresh and path.exists() and _cache_age_hours(path) < CACHE_TTL_HOURS:
        logger.info("读取缓存 %s", path.name)
        return pd.read_csv(path, encoding="utf-8-sig")

    fetcher = _akshare_fetchers()[kind]
    try:
        df = fetcher(symbol=em_symbol)
    except Exception:
        if path.exists():
            logger.warning(
                "拉取 %s %s 失败，回退到 %.1f 小时前的过期缓存",
                em_symbol, KIND_CN[kind], _cache_age_hours(path),
            )
            return pd.read_csv(path, encoding="utf-8-sig")
        raise

    if df is None or df.empty:
        raise RuntimeError(f"{em_symbol} {KIND_CN[kind]} 返回空数据（接口异常或代码不存在）")

    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("已拉取并缓存 %s %s：%d 期", em_symbol, KIND_CN[kind], len(df))
    return df


def fetch_all_statements(code: str, refresh: bool = False) -> dict[str, pd.DataFrame]:
    """一次性拉取三大报表，返回 {kind: DataFrame}。"""
    return {kind: fetch_statement(code, kind, refresh=refresh) for kind in STATEMENT_KINDS}
