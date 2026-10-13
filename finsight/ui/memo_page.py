"""买方投资备忘录页面（方向6，V2.0）：三层基本面 + 技术面 + 结论卡 + Agent。"""
import pandas as pd
import streamlit as st

from finsight.datasources.em_statements import STATEMENT_KINDS, fetch_company_profile, fetch_kline, fetch_macro
from finsight.datasources.symbols import to_em_symbol
from finsight.finance.conclusion import build_conclusion
from finsight.finance.field_mapping import standardize_statement
from finsight.finance.fundamental import build_fundamental
from finsight.finance.scoring import compute_score
from finsight.finance.signals_v2 import assess_risk
from finsight.finance.technical import analyze_technical
from finsight.memo.evidence import build_evidence_book
from finsight.memo.llm import LLMConfig, LLM_PROVENANCE_NOTE, apply_llm_sections, generate_llm_sections
from finsight.memo.render import memo_to_docx_bytes, memo_to_markdown, memo_to_pdf_bytes
from finsight.memo.skeleton import build_memo_skeleton
from finsight.ui.components import page_header, status_chip_row
from finsight.ui.view import fetch_with_source, period_label

_DATA_KEY = "fs_memo_data"
_RESULT_KEY = "fs_memo_result"


def _load_data(code: str) -> dict:
    """拉取三表并标准化（复用缓存层）；结果整体覆盖 session，清空旧备忘录。"""
    from finsight.datasources.em_statements import fetch_company_profile
    data = {"code": code, "std": {}, "sources": {}, "errors": {}}
    for kind in STATEMENT_KINDS:
        try:
            raw, source = fetch_with_source(code, kind)
            data["std"][kind] = standardize_statement(kind, raw)
            data["sources"][kind] = source
        except Exception as exc:
            data["errors"][kind] = str(exc)
    data["profile"] = fetch_company_profile(code)
    st.session_state[_DATA_KEY] = data
    st.session_state.pop(_RESULT_KEY, None)  # 新数据：旧备忘录立即失效
    return data


def render_memo_page() -> None:
    page_header("投资备忘录", "证据关联的买方研究文档｜确定性事实骨架 + 可选 LLM 定性（经校验）｜MD / DOCX 导出")

    cfg = LLMConfig.from_env()
    if cfg.configured():
        status_chip_row("模型状态", f"LLM：已配置（模型 {cfg.model}）", tone="info")
    else:
        status_chip_row("模型状态",
                        "LLM：未配置（将生成仅含确定性内容的基础备忘录；"
                        "可经环境变量 FINSIGHT_LLM_API_KEY / FINSIGHT_LLM_BASE_URL / FINSIGHT_LLM_MODEL 配置）",
                        tone="neutral")

    # ---- 参数卡片 ----
    with st.container(border=True):
        col_code, col_load = st.columns([3, 1])
        with col_code:
            code = st.text_input("A股股票代码", placeholder="如 600519", key="fs_memo_code")
        with col_load:
            st.write("")
            if st.button("加载数据", key="fs_memo_load"):
                code_clean = (code or "").strip()
                if not code_clean:
                    st.warning("请先输入股票代码。")
                    return
                try:
                    to_em_symbol(code_clean)
                except ValueError as exc:
                    st.error(f"股票代码无效：{exc}")
                    return
                with st.status("正在获取三大报表…", expanded=True) as status:
                    st.write("本地缓存优先，过期才联网（复用缓存层）…")
                    _load_data(code_clean)
                    st.write("标准化完成。")
                    status.update(label="数据加载完成", state="complete", expanded=False)

    data = st.session_state.get(_DATA_KEY)
    if not data:
        st.info("输入股票代码并点击「加载数据」，再选择报告期生成备忘录。")
        return

    st.markdown(f"已加载：**{to_em_symbol(data['code'])}**")
    for kind, source in data["sources"].items():
        st.caption(f"{ {'balance': '资产负债表', 'income': '利润表', 'cashflow': '现金流量表'}[kind] }"
                   f"数据来源：{source}")
    kind_cn = {"balance": "资产负债表", "income": "利润表", "cashflow": "现金流量表"}
    for kind, err in data["errors"].items():
        st.warning(f"{kind_cn[kind]} 获取失败：{err}"
                   f"——依赖该表的指标与规则将无法评估，备忘录相应降级。")

    std_income = data["std"].get("income")
    if std_income is None or std_income.empty:
        st.error("利润表不可用：无法生成备忘录（至少需要利润表财务数据）。")
        return

    income_rows = std_income.sort_values("report_date", ascending=False)
    options = list(income_rows["report_date"])
    labels = {d: period_label(d, rt) for d, rt in zip(income_rows["report_date"],
                                                      income_rows.get("report_type", pd.Series(dtype="object")))}

    with st.container(border=True):
        col_period, col_focus, col_gen = st.columns([2, 3, 1])
        with col_period:
            selected = st.selectbox("目标报告期", options,
                                    format_func=lambda d: labels.get(d, str(d)[:10]),
                                    key="fs_memo_period")
        with col_focus:
            focus = st.text_input("研究关注问题（可选，仅供定性章节参考）",
                                  placeholder="如：现金流与利润背离的原因", key="fs_memo_focus")
        with col_gen:
            st.write("")
            if st.button("生成备忘录", type="primary", key="fs_memo_generate"):
                suffix = "（含 LLM 定性章节生成）" if cfg.configured() else ""
                with st.status(f"正在生成备忘录{suffix}…", expanded=True) as status:
                    st.write("构建证据与确定性骨架…")
                    book = build_evidence_book(data["code"], std_income, data["std"].get("balance"),
                                               data["std"].get("cashflow"), sources=data["sources"],
                                               target_date=selected,
                                               company_profile=data.get("profile"))
                    st.write("计算 S1–S7 风险预警与技术面…")
                    risk_v2 = assess_risk(std_income, data["std"].get("balance"),
                                          data["std"].get("cashflow"))
                    kline = fetch_kline(data["code"], days=250)
                    tech = analyze_technical(kline) if not kline.empty else None
                    st.write("计算评分模型与结论卡…")
                    score = compute_score(std_income, data["std"].get("balance"),
                                          data["std"].get("cashflow"),
                                          technical=tech, risk=risk_v2)
                    conclusion = build_conclusion(score, risk_v2, tech)
                    st.write("构建三层基本面框架…")
                    macro = fetch_macro()
                    fundamental = build_fundamental(macro, data.get("profile") or {},
                                                    risk_v2, std_income)
                    memo = build_memo_skeleton(to_em_symbol(data["code"]), book,
                                               conclusion=conclusion,
                                               fundamental=fundamental,
                                               technical=tech,
                                               risk_v2=risk_v2,
                                               score=score)
                    st.write(f"证据 {len(book.evidences)} 条，章节 {len(memo.sections)} 个。")
                    if cfg.configured():
                        st.write("调用 LLM 生成定性章节并校验…")
                        try:
                            sections, issues = generate_llm_sections(cfg, memo, focus.strip())
                            apply_llm_sections(memo, sections, issues)
                        except Exception as exc:
                            memo.llm_status = f"调用失败（{exc}）"
                            for sec in memo.sections:
                                if sec.kind in ("llm", "mixed"):
                                    sec.status = "llm_failed"
                    status.update(label="备忘录生成完成", state="complete", expanded=False)
                st.session_state[_RESULT_KEY] = memo

    memo = st.session_state.get(_RESULT_KEY)
    if not memo:
        st.info("选择目标报告期（默认最新）与可选关注问题后，点击「生成备忘录」。")
        return

    if memo.llm_status == "已生成":
        st.success(f"备忘录已生成（LLM 定性章节已通过结构/引用/数字检查并合并；{len(memo.llm_issues)} 条校验提示）。"
                   "注意：程序化检查不等于事实核验，详见各章节可信度标注。")
        for issue in memo.llm_issues:
            st.caption(f"⚠ {issue}")
    elif memo.llm_status.startswith("未配置"):
        st.info("基础备忘录已生成（确定性内容完整）；定性章节待配置 LLM 后生成。")
    else:
        st.warning(f"基础备忘录已生成；LLM 定性章节未生成——{memo.llm_status}。"
                   "确定性内容不受影响。")

    st.markdown(f'<div class="fs-lift fs-srcnote">标的 <b>{memo.code}</b>｜目标报告期 <b>{memo.period}</b>'
                f'｜生成于 {memo.generated_at}｜LLM 状态：{memo.llm_status}</div>',
                unsafe_allow_html=True)
    if memo.evidence is not None and memo.evidence.snapshot.get("company_name"):
        st.caption(f"公司：{memo.evidence.snapshot['company_name']}"
                   f"（{memo.evidence.snapshot.get('industry') or '行业未获取'}）")

    for sec in memo.sections:
        with st.expander(sec.title, expanded=sec.key in ("summary", "financials", "risks")):
            if sec.llm_text:
                st.markdown(f"> **【{LLM_PROVENANCE_NOTE}】**\n\n{sec.llm_text}")
            if sec.key == "appendix":
                if memo.evidence is not None:
                    rows = []
                    for eid in memo.evidence.ids():
                        e = memo.evidence.get(eid)
                        rows.append({"证据 ID": e.eid, "内容": e.label,
                                     "值": "—" if e.value is None else f"{e.value:,.4g}",
                                     "单位": e.unit, "报告期": e.report_date,
                                     "来源/依据": e.derivation})
                    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            else:
                for fact in sec.facts:
                    st.markdown(fact)

    st.divider()
    md_text = memo_to_markdown(memo)
    st.download_button("下载 Markdown", data=md_text.encode("utf-8"),
                       file_name=f"memo_{memo.code}_{memo.period}.md", mime="text/markdown",
                       key="fs_memo_dl_md")
    st.download_button("下载 Word（DOCX）", data=memo_to_docx_bytes(memo),
                       file_name=f"memo_{memo.code}_{memo.period}.docx",
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       key="fs_memo_dl_docx")
    st.download_button("下载 PDF", data=memo_to_pdf_bytes(memo),
                       file_name=f"memo_{memo.code}_{memo.period}.pdf",
                       mime="application/pdf", key="fs_memo_dl_pdf")
    st.caption("导出内容含完整备忘录、证据附录、待核实事项与免责声明。")
