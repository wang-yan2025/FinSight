"""东方财富三大报表数据源（经 akshare），带本地 CSV 缓存。

设计说明：
- akshare 导入较慢（数秒），延迟到实际取数时才导入。
- 缓存文件名：{EM代码}_{报表类型}.csv，UTF-8-SIG 编码（Excel 可直接打开）。
- 未过 TTL 的缓存直接使用；拉取失败时若存在过期缓存则回退使用并在日志告警。
- 返回的 DataFrame 保留 akshare 原始列，字段映射由上层（阶段2）负责。
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

import pandas as pd

from finsight.audit import audit_event, audit_fail
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


_PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
               "ALL_PROXY", "all_proxy")


@contextlib.contextmanager
def _no_proxy():
    """对东财接口临时禁用系统代理（本地代理可能拦截 akshare 的 HTTPS 请求）。

    仅影响当前上下文内的环境变量，结束后恢复；LLM 调用不受影响。
    """
    saved = {}
    for key in _PROXY_KEYS:
        if key in os.environ:
            saved[key] = os.environ.pop(key)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    try:
        yield
    finally:
        # 恢复原代理设置（供 LLM 等其他外联使用）
        os.environ.pop("NO_PROXY", None)
        os.environ.pop("no_proxy", None)
        for key, value in saved.items():
            os.environ[key] = value


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
        audit_event("data_cache_read", kind=kind, cache_file=path.name,
                    cache_age_hours=round(_cache_age_hours(path), 2))
        logger.info("读取缓存 %s", path.name)
        return pd.read_csv(path, encoding="utf-8-sig")

    if os.environ.get("FINSIGHT_OFFLINE") == "1":
        # 离线模式硬守卫：禁止任何联网取数尝试
        raise RuntimeError(f"离线模式（FINSIGHT_OFFLINE=1）：{em_symbol} {KIND_CN[kind]} "
                           "无新鲜缓存，禁止联网取数；请提供本地数据样本")

    audit_event("data_fetch_attempt", kind=kind, refresh=refresh,
                cache_exists=path.exists())
    fetcher = _akshare_fetchers()[kind]
    try:
        with _no_proxy():
            df = fetcher(symbol=em_symbol)
    except Exception as exc:
        audit_fail("data_fetch_failed", exc, kind=kind)
        if path.exists():
            logger.warning(
                "拉取 %s %s 失败，回退到 %.1f 小时前的过期缓存",
                em_symbol, KIND_CN[kind], _cache_age_hours(path),
            )
            audit_event("data_cache_fallback", kind=kind,
                        cache_age_hours=round(_cache_age_hours(path), 2))
            return pd.read_csv(path, encoding="utf-8-sig")
        raise

    if df is None or df.empty:
        raise RuntimeError(f"{em_symbol} {KIND_CN[kind]} 返回空数据（接口异常或代码不存在）")

    df.to_csv(path, index=False, encoding="utf-8-sig")
    audit_event("data_fetch_ok", kind=kind, rows=len(df), cache_file=path.name)
    logger.info("已拉取并缓存 %s %s：%d 期", em_symbol, KIND_CN[kind], len(df))
    return df


def fetch_all_statements(code: str, refresh: bool = False) -> dict[str, pd.DataFrame]:
    """一次性拉取三大报表，返回 {kind: DataFrame}。"""
    return {kind: fetch_statement(code, kind, refresh=refresh) for kind in STATEMENT_KINDS}


def fetch_company_profile(code: str, refresh: bool = False) -> dict:
    """获取公司基本信息（名称/行业，经东财 push2 接口直连），带缓存。

    返回 {公司名称, 行业, 板块}；失败返回空 dict（调用方如实标注待补充）。
    """
    em_symbol = to_em_symbol(code)
    path = CACHE_DIR / f"{em_symbol}_profile.json"
    ensure_dirs()
    if not refresh and path.exists() and _cache_age_hours(path) < CACHE_TTL_HOURS * 4:
        return json.loads(path.read_text(encoding="utf-8"))
    if os.environ.get("FINSIGHT_OFFLINE") == "1":
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}
    secid = ("1." if em_symbol.startswith("SH") else "0.") + code
    try:
        import requests as _rq
        _session = _rq.Session()
        _session.trust_env = False  # 绕过系统代理（本地代理可能拦截东财接口）
        resp = _session.get(
            "http://push2.eastmoney.com/api/qt/stock/get",
            params={"secid": secid, "fields": "f57,f58,f127,f128"},
            headers={"User-Agent": "Mozilla/5.0",
                     "Referer": "https://quote.eastmoney.com/"}, timeout=15)
        data = (resp.json() or {}).get("data") or {}
        profile = {"公司名称": data.get("f58") or "", "行业": data.get("f127") or "",
                   "板块": data.get("f128") or ""}
        profile = {k: v for k, v in profile.items() if v}
        if not profile.get("公司名称"):
            raise RuntimeError("接口返回无公司名称")
    except Exception as exc:
        logger.warning("获取 %s 公司信息失败：%s", em_symbol, exc)
        audit_fail("profile_fetch_failed", exc, kind="profile")
        return {}
    path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    audit_event("profile_fetch_ok", kind="profile", name=profile.get("公司名称", ""))
    return profile
