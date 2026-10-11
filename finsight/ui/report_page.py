"""财报分析页（方向2 · 阶段4B 完整 MVP）。

查询 → 三大报表 → 指标概览 → 趋势 → 风险信号，一个页面完成。
- 取数/缓存复用 em_statements；标准化复用 field_mapping；
  指标复用 indicators；风险信号复用 signals（evaluate_all 优先）。
- 查询时以全部可用历史数据计算，展示期数仅裁剪显示范围。
- 查询结果存 session_state：切换 Tab / 展示期数不重复请求。
"""
import pandas as pd
import streamlit as st

from finsight.datasources.em_statements import STATEMENT_KINDS
from finsight.datasources.symbols import to_em_symbol
from finsight.finance.field_mapping import standardize_statement
from finsight.ui.signals_view import (
    LEVEL_CN,
    evaluate_available,
    format_metric_lines,
    signal_summary_frame,
)
from finsight.ui.view import (
    build_display_frame,
    build_indicator_display,
    build_trend_frames,
    fetch_with_source,
    statement_caption,
)

PERIOD_CHOICES = (4, 8, 12, 24)
_STATE_KEY = "fs_analysis_v1"

CHART_TITLES = {
    "单季流量": "单季营业收入 / 净利润 / 经营现金流净额（亿元，单季口径，缺失期不插值）",
    "盈利比率": "毛利率 / 净利率（%，累计口径）",
    "偿债比率": "资产负债率（%，期末时点）与流动比率（倍，期末时点）",
}


def _fetch_and_prepare(code: str) -> dict:
    """拉取三表、标准化并保存到 session_state；单表失败不阻断其他表。"""
    prepared = {"code": code, "std": {}, "sources": {}, "errors": {}}
    for kind in STATEMENT_KINDS:
        try:
            raw, source = fetch_with_source(code, kind)
            prepared["std"][kind] = standardize_statement(kind, raw)
            prepared["sources"][kind] = source
        except Exception as exc:
            prepared["errors"][kind] = str(exc)
    st.session_state[_STATE_KEY] = prepared
    return prepared


def render_report_page() -> None:
    st.subheader("财报分析：报表 · 指标 · 趋势 · 风险信号")
    st.caption(
        "数据来源：东方财富三大报表（经 akshare）｜金额单位：元｜"
        "资产负债表为报告期末时点值，利润表与现金流量表为年初至报告期末累计值；缺失值显示为 '—'，不补造"
    )

    col_code, col_periods, col_btn = st.columns([2, 2, 1])
    with col_code:
        code = st.text_input("A股股票代码", placeholder="如 600519", key="fs_code")
    with col_periods:
        n_periods = st.selectbox("展示期数（仅影响显示，计算始终用全部历史）",
                                 PERIOD_CHOICES, index=0, key="fs_periods")
    with col_btn:
        st.write("")
        queried = st.button("查询", type="primary", key="fs_query")

    if queried:
        code_clean = (code or "").strip()
        if not code_clean:
            st.warning("请先输入股票代码，例如 600519。")
            return
        try:
            to_em_symbol(code_clean)
        except ValueError as exc:
            st.error(f"股票代码无效：{exc}")
            return
        with st.spinner("正在获取三大报表并计算指标与风险信号（首次拉取约需 30 秒）…"):
            state = _fetch_and_prepare(code_clean)
    else:
        state = st.session_state.get(_STATE_KEY)

    if not state:
        st.info("输入股票代码后点击「查询」：报表 → 指标 → 趋势 → 风险信号将一次性完成。")
        return

    std_map: dict = state["std"]
    errors: dict = state["errors"]
    if not std_map:
        st.error("三张报表均未获取到数据：请检查股票代码是否正确、网络是否可用后重试。")
        return

    st.markdown(f"查询标的：**{to_em_symbol(state['code'])}**")
    for kind, source in state["sources"].items():
        st.caption(f"{statement_caption(kind).split('｜')[0]}数据来源：{source}")
    for kind, err in errors.items():
        st.warning(f"{statement_caption(kind).split('｜')[0]} 获取失败：{err}"
                   f"——依赖该表的指标与规则将无法评估，其余内容正常展示。")

    std_income = std_map.get("income")
    std_balance = std_map.get("balance")
    std_cashflow = std_map.get("cashflow")

    tab_overview, tab_trend, tab_signals, tab_statements = st.tabs(
        ["指标概览", "趋势分析", "风险信号", "三大报表"]
    )

    with tab_overview:
        indicator_df, indicator_notes = build_indicator_display(
            std_income, std_balance, std_cashflow, max_rows=n_periods)
        if indicator_df.empty:
            st.warning(indicator_notes[0] if indicator_notes else "指标概览不可用。")
        else:
            st.dataframe(indicator_df, width="stretch", hide_index=True)
            st.caption("口径说明：同比列为累计口径（与同列金额一致）；周转率为年化值（次/年）；"
                       "特殊同比状态（扭亏为盈/由盈转亏/亏损收窄/亏损扩大）按状态分类显示，不作普通增减解读。")
        if indicator_notes:
            with st.expander(f"未计算说明（{len(indicator_notes)} 条，缺失或口径不成立，不编造）"):
                for note in indicator_notes:
                    st.write(f"- {note}")

    with tab_trend:
        trend_frames = build_trend_frames(std_income, std_cashflow, std_balance)
        if not trend_frames:
            st.warning("报表数据不可用，无法生成趋势图。")
        for name, frame in trend_frames.items():
            st.markdown(f"**{CHART_TITLES[name]}**")
            if frame.empty or frame.isna().all().all():
                st.caption("该组趋势无可用数据。")
                continue
            st.line_chart(frame)
            st.caption("横轴为报告期（时间正序）；缺失期显示断点，不插值。")

    with tab_signals:
        analysis = evaluate_available(std_income, std_cashflow, std_balance,
                                      company=to_em_symbol(state["code"]))
        high = [s for s in analysis.signals if s.level == "high"]
        medium = [s for s in analysis.signals if s.level == "medium"]
        c1, c2, c3 = st.columns(3)
        c1.metric("触发信号总数", len(analysis.signals))
        c2.metric("较高关注（high）", len(high))
        c3.metric("关注（medium）", len(medium))
        st.info(
            "以下均为**风险提示（risk_hint）**，不构成财务造假或违规的认定；"
            "**未触发信号不代表公司不存在风险**；阈值为第一版启发式参数（详见引擎文档）。"
        )
        if analysis.signals:
            st.dataframe(signal_summary_frame(analysis.signals), width="stretch", hide_index=True)
            for s in analysis.signals:
                title = f"[{LEVEL_CN.get(s.level, s.level)}] {s.rule_id} {s.name} · {s.period}"
                with st.expander(title):
                    st.write(f"**触发条件**：{s.condition}")
                    st.write("**关键数值**")
                    for line in format_metric_lines(s.metrics):
                        st.write(f"- {line}")
                    st.write("**口径与数据来源**")
                    for key, value in s.fields_used.items():
                        st.write(f"- {key}：{value}")
                    if s.caveats:
                        st.write("**口径限制**")
                        for caveat in s.caveats:
                            st.write(f"- {caveat}")
                    st.write(f"**风险解释**：{s.interpretation}")
                    st.write("**建议人工核查**")
                    for tip in s.suggestions:
                        st.write(f"- {tip}")
        else:
            st.write("当前数据下四类规则均未触发信号（已评估部分见下方说明）。")
        if analysis.unevaluated:
            with st.expander(f"无法评估的期间/规则（{len(analysis.unevaluated)} 条，数据不足，非未触发）"):
                for note in analysis.unevaluated:
                    st.write(f"- {note}")

    with tab_statements:
        st.caption("字段为标准中文名（映射自东财原始列）；表格可横向滚动查看全部字段。")
        sub_tabs = st.tabs([statement_caption(kind).split("｜")[0] for kind in STATEMENT_KINDS])
        for sub_tab, kind in zip(sub_tabs, STATEMENT_KINDS):
            with sub_tab:
                if kind in std_map:
                    display = build_display_frame(std_map[kind], kind, n_periods)
                    if isinstance(display, pd.DataFrame) and display.empty:
                        st.warning("该报表无有效报告期数据（接口返回为空），已如实展示。")
                    else:
                        st.caption(statement_caption(kind))
                        st.dataframe(display, width="stretch", hide_index=True)
                else:
                    st.warning(f"该报表获取失败，无法展示（见页首错误提示）。")
