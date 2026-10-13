"""财报分析页（方向2，PRD V2.0）：S1–S7 预警 + 技术面 + 结论卡 + 指标/趋势。"""
import pandas as pd
import streamlit as st

from finsight.datasources.em_statements import STATEMENT_KINDS, fetch_kline
from finsight.datasources.symbols import to_em_symbol
from finsight.finance.conclusion import build_conclusion
from finsight.finance.field_mapping import standardize_statement
from finsight.finance.scoring import compute_score
from finsight.finance.signals_v2 import LEVEL_COLOR, LEVEL_CN, assess_risk
from finsight.finance.technical import analyze_technical
from finsight.ui.components import badge, kpi_cards, page_header
from finsight.ui.view import (
    build_display_frame,
    build_indicator_display,
    build_trend_frames,
    fetch_with_source,
    latest_kpis,
    statement_caption,
)

PERIOD_CHOICES = (4, 8, 12, 24)
_STATE_KEY = "fs_analysis_v1"

CHART_TITLES = {
    "单季流量": "单季营业收入 / 净利润 / 经营现金流净额（亿元，单季口径）",
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
    page_header("财务分析中心", "三大报表 · 财务指标 · 趋势 · R1–R4 风险信号｜数据：东方财富（akshare），单位：元",
                chips=[("口径", "时点/累计自动标注"), ("缺失", "显式标注不补造")])

    # ---- 查询卡片 ----
    with st.container(border=True):
        col_code, col_periods, col_btn = st.columns([2, 2, 1])
        with col_code:
            code = st.text_input("A股股票代码", placeholder="如 600519", key="fs_code")
        with col_periods:
            n_periods = st.selectbox("展示期数（仅影响显示，计算始终用全部历史）",
                                     PERIOD_CHOICES, index=0, key="fs_periods")
        with col_btn:
            st.write("")
            queried = st.button("查 询", type="primary", key="fs_query")

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
        with st.status("正在获取三大报表并计算指标与风险信号…", expanded=True) as status:
            st.write("读取财务数据（本地缓存优先，过期才联网）…")
            state = _fetch_and_prepare(code_clean)
            st.write(f"完成：{len(state['std'])}/3 张报表；开始标准化与指标计算。")
            status.update(label="数据就绪，指标与风险信号已计算", state="complete",
                          expanded=False)
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

    std_income = std_map.get("income")
    std_balance = std_map.get("balance")
    std_cashflow = std_map.get("cashflow")

    # ---- S1–S7 + 技术面 + 评分/结论（V2.0 新增） ----
    risk = assess_risk(std_income, std_balance, std_cashflow)
    kline = fetch_kline(state["code"], days=250)
    tech = analyze_technical(kline) if not kline.empty else None
    score = compute_score(std_income, std_balance, std_cashflow,
                          technical=tech, risk=risk)
    conclusion = build_conclusion(score, risk, tech)

    # ---- 结果概览条 + KPI + 结论卡 ----
    src_note = "；".join(f"{statement_caption(k).split('｜')[0]}：{s}"
                         for k, s in state["sources"].items())
    st.markdown(f'<div class="fs-lift fs-srcnote">查询标的 <b>{to_em_symbol(state["code"])}</b>'
                f'｜{src_note}</div>', unsafe_allow_html=True)
    for kind, err in errors.items():
        st.warning(f"{statement_caption(kind).split('｜')[0]} 获取失败：{err}"
                   f"——依赖该表的指标与规则将无法评估，其余内容正常展示。")

    kpis = latest_kpis(std_income, std_balance, std_cashflow)
    if kpis:
        kpi_cards(kpis)

    # ---- 结论卡（评级/置信度/风险/技术面） ----
    c_rate, c_score, c_risk, c_tech = st.columns(4)
    c_rate.metric("投资评级", conclusion.rating, conclusion.confidence)
    c_score.metric("综合评分", f"{conclusion.score_total:.0f}", f"/100")
    c_risk.metric("风险预警", conclusion.risk_level)
    c_tech.metric("技术面", "偏多" if tech and "上升" in tech.trend else
                  ("偏空" if tech and "下降" in tech.trend else "中性/无数据"))
    st.caption(conclusion.rationale + "。" + conclusion.disclaimers[0])

    tab_overview, tab_trend, tab_tech, tab_signals, tab_statements = st.tabs(
        ["指标概览", "趋势分析", "技术面", "风险预警 S1–S7", "三大报表"]
    )

    with tab_overview:
        indicator_df, indicator_notes = build_indicator_display(
            std_income, std_balance, std_cashflow, max_rows=n_periods)
        if indicator_df.empty:
            st.warning(indicator_notes[0] if indicator_notes else "指标概览不可用。")
        else:
            st.dataframe(indicator_df, width="stretch", hide_index=True)
            st.caption("口径说明：同比列为累计口径；周转率为年化值（次/年）。")
        if indicator_notes:
            with st.expander(f"数据边界说明（{len(indicator_notes)} 条）"):
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

    with tab_tech:
        if tech is None:
            st.warning("K 线数据不可用（需联网获取），技术面分析无法展示。")
        else:
            st.markdown(f"**技术面小结**：{tech.summary}")
            t1, t2, t3, t4 = st.columns(4)
            t1.metric("最新收盘", f"{tech.latest_close:.2f}")
            t2.metric("MA5 / MA20", f"{tech.ma5 or '—'} / {tech.ma20 or '—'}")
            t3.metric("MACD", tech.macd_signal or "—")
            t4.metric("RSI(14)", f"{tech.rsi14 or '—'} {tech.rsi_signal}")
            t5, t6, t7, t8 = st.columns(4)
            t5.metric("KDJ", tech.kdj_signal or "—")
            t6.metric("量比", tech.volume_ratio or "—")
            t7.metric("支撑位", tech.support or "—")
            t8.metric("压力位", tech.resistance or "—")
            if not kline.empty:
                st.line_chart(kline.set_index("date")["close"], height=300)
                st.caption("日 K 线收盘价（前复权，近 250 日）")

    with tab_signals:
        st.markdown(f"### 预警等级：{risk.level_cn}")
        st.progress(min(risk.triggered_count / 3, 1.0))
        c_s, c_t = st.columns(2)
        c_s.metric("触发信号数", f"{risk.triggered_count} / 7")
        c_t.metric("预警级别", risk.level_cn)
        st.caption(risk.summary)
        st.info("以下均为风险提示（risk_hint），不构成财务造假认定；未触发不代表无风险。")
        # S1–S7 汇总表
        signal_rows = [{"信号": s.sid, "名称": s.name, "维度": s.dimension,
                        "状态": "🔴 触发" if s.triggered else "🟢 未触发",
                        "判定": s.detail} for s in risk.signals]
        st.dataframe(pd.DataFrame(signal_rows), width="stretch", hide_index=True)
        for s in risk.signals:
            mark = "🔴" if s.triggered else "🟢"
            tone = "fail" if s.triggered else "pass"
            with st.expander(f"{mark} {s.sid} {s.name}（{s.dimension}）"):
                st.markdown(badge("触发" if s.triggered else "未触发", tone),
                            unsafe_allow_html=True)
                st.write(f"**判定说明**：{s.detail}")
                st.caption(f"证据：{s.evidence[:200]}")

    with tab_statements:
        st.caption("字段为标准中文名；表格可横向滚动查看全部字段。")
        sub_tabs = st.tabs([statement_caption(kind).split("｜")[0] for kind in STATEMENT_KINDS])
        for sub_tab, kind in zip(sub_tabs, STATEMENT_KINDS):
            with sub_tab:
                if kind in std_map:
                    display = build_display_frame(std_map[kind], kind, n_periods)
                    if isinstance(display, pd.DataFrame) and display.empty:
                        st.warning("该报表无有效报告期数据，已如实展示。")
                    else:
                        st.caption(statement_caption(kind))
                        st.dataframe(display, width="stretch", hide_index=True)
                else:
                    st.warning("该报表获取失败（见页首提示）。")
