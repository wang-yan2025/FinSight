"""投研工作台（阶段7）：生成→核验→修订→再核验→对比→导出 的完整闭环页面。

- 所有关键动作仅在按钮分支执行；重渲染不重复取数/核验/修订。
- 上下文指纹（代码+期间）变化即清空旧会话，杜绝跨股票/期间混用。
- LLM 未配置/失败：确定性闭环完整可用；原始版本 V1 只读，修订必产 V2。
"""
import streamlit as st

from finsight.datasources.em_statements import STATEMENT_KINDS
from finsight.datasources.symbols import to_em_symbol
from finsight.finance.field_mapping import standardize_statement
from finsight.memo.llm import LLMConfig
from finsight.memo.render import memo_to_docx_bytes, memo_to_markdown
from finsight.ui.view import fetch_with_source, period_label
from finsight.verify.report import report_to_docx_bytes, report_to_markdown
from finsight.verify.rules import STATUS_CN, STATUS_ORDER
from finsight.workflow.export import comparison_to_markdown, revisions_to_markdown
from finsight.workflow.pipeline import compare_rounds, revise, start_session

_CTX_KEY = "fs_bench_ctx"
_DATA_KEY = "fs_bench_data"
_SESSION_KEY = "fs_bench_session"
_PICKS_KEY = "fs_bench_picks"


def _memo_period_options(data):
    import pandas as pd
    inc = data["std"]["income"]
    rows = inc.sort_values("report_date", ascending=False)
    labels = {}
    if "report_type" in rows.columns:
        labels = {d: period_label(d, rt) for d, rt in zip(rows["report_date"], rows["report_type"])}
    else:
        labels = {d: period_label(d) for d in rows["report_date"]}
    return list(rows["report_date"]), labels


def render_bench_page() -> None:
    st.subheader("投研工作台：生成 → 核验 → 修订 → 再核验 → 对比（完整闭环）")
    st.caption("原始版本只读；修订必产新版本且逐条可追溯；两轮核验共用同一参考快照；"
               "「通过」仅覆盖已提取声明。无 LLM 配置时确定性闭环完整可用。")

    cfg = LLMConfig.from_env()
    st.caption(f"LLM：{'已配置（' + cfg.model + '）' if cfg.configured() else '未配置（基础备忘录）'}")

    col_code, col_load = st.columns([3, 1])
    with col_code:
        code = st.text_input("A股股票代码", placeholder="如 600519", key="fs_bench_code")
    with col_load:
        st.write("")
        if st.button("1 加载数据", key="fs_bench_load"):
            code_clean = (code or "").strip()
            try:
                to_em_symbol(code_clean)
            except ValueError as exc:
                st.error(f"股票代码无效：{exc}")
                return
            with st.spinner("获取三大报表（复用缓存）…"):
                data = {"code": code_clean, "std": {}, "sources": {}, "errors": {}}
                for kind in STATEMENT_KINDS:
                    try:
                        raw, source = fetch_with_source(code_clean, kind)
                        data["std"][kind] = standardize_statement(kind, raw)
                        data["sources"][kind] = source
                    except Exception as exc:
                        data["errors"][kind] = str(exc)
                income = data["std"].get("income")
                if income is None or income.empty:
                    st.error("利润表不可用，无法启动闭环（至少需要利润表）。")
                    return
                st.session_state[_DATA_KEY] = data
                st.session_state.pop(_SESSION_KEY, None)
                st.session_state.pop(_PICKS_KEY, None)

    data = st.session_state.get(_DATA_KEY)
    if not data:
        st.info("输入股票代码并点击「1 加载数据」启动闭环。")
        return
    st.markdown(f"已加载：**{to_em_symbol(data['code'])}**（"
                + "；".join(f"{k}={v}" for k, v in data["sources"].items()) + "）")

    # 上下文守卫：代码变化清空会话
    if st.session_state.get(_CTX_KEY) != data["code"]:
        st.session_state.pop(_SESSION_KEY, None)
        st.session_state.pop(_PICKS_KEY, None)
        st.session_state[_CTX_KEY] = data["code"]

    dates, labels = _memo_period_options(data)
    target_date = st.selectbox("目标报告期", dates,
                               format_func=lambda d: labels.get(d, str(d)[:10]),
                               key="fs_bench_period")

    session = st.session_state.get(_SESSION_KEY)

    # ---- 步骤 2：生成 + 首轮核验 ----
    if st.button("2 生成备忘录并独立核验", type="primary", key="fs_bench_run1",
                 disabled=session is not None):
        with st.spinner("构建证据、生成备忘录并独立核验…"):
            em = to_em_symbol(data["code"])
            try:
                new_session = start_session(em, data["std"].get("income"),
                                            data["std"].get("balance"),
                                            data["std"].get("cashflow"),
                                            sources=data["sources"],
                                            target_date=target_date)
            except Exception as exc:
                st.error(f"闭环初始化失败：{exc}")
                return
            st.session_state[_SESSION_KEY] = new_session
            st.session_state[_PICKS_KEY] = []
            session = new_session

    if session is None:
        return

    r1 = session.rounds[1]
    st.success(f"V1 已生成并完成首轮核验：{r1.report.claim_count} 条声明｜"
               + "｜".join(f"{STATUS_CN[s]} {r1.report.counts()[s]}"
                          for s in STATUS_ORDER if r1.report.counts()[s]))
    st.caption(f"参考快照指纹：{session.fingerprint}（两轮核验将共用此快照）")

    with st.expander("V1 备忘录（原始版本，只读）"):
        st.markdown(session.versions["V1"].markdown[:4000])
        st.download_button("下载 V1 备忘录（MD）",
                           data=session.versions["V1"].markdown.encode("utf-8"),
                           file_name=f"memo_V1_{session.code}_{session.period}.md",
                           key="fs_bench_dl_v1")

    # ---- 步骤 3：修订建议与人工采纳 ----
    st.markdown("### 修订建议（人工逐条确认，不自动应用）")
    if not session.proposals:
        st.info("首轮核验未产生修订建议（无确定性错误或口径歧义声明）。")
    picks = st.session_state.get(_PICKS_KEY, [])
    for p in session.proposals:
        det_tag = "确定性" if p.deterministic else "人工建议"
        with st.expander(f"{p.pid}｜{p.kind}｜{det_tag}｜{p.old_display}"
                         + (f" → {p.new_display}" if p.new_display else "")):
            st.write(f"- 对应声明：{p.cid}｜位置：{p.location}")
            st.write(f"- 原文：{p.original_sentence}")
            st.write(f"- 修改：{p.old_display} → {p.new_display or '（人工修订）'}")
            st.write(f"- 参考依据：{p.ref_display}"
                     + (f"（证据 {p.evidence_id}）" if p.evidence_id else ""))
            st.write(f"- 原因：{p.reason}")
            for lim in p.limitations:
                st.caption(f"限制：{lim}")
            if p.deterministic and p.new_sentence:
                checked = st.checkbox("采纳该确定性修订", value=p.pid in picks,
                                      key=f"fs_bench_pick_{p.pid}")
                if checked and p.pid not in picks:
                    picks.append(p.pid)
                elif not checked and p.pid in picks:
                    picks.remove(p.pid)
                st.session_state[_PICKS_KEY] = picks
            else:
                st.caption("非确定性建议：不提供自动替换，请人工修订后重新核验。")

    # ---- 步骤 4：生成修订稿 + 再核验 ----
    have_v2 = "V2" in session.versions
    if st.button("3 生成修订稿并再核验", type="primary", key="fs_bench_revise",
                 disabled=have_v2 or not picks):
        with st.spinner("应用已采纳修订（生成 V2）并独立再核验…"):
            try:
                session, _child = revise(session, picks)
            except Exception as exc:
                st.error(f"修订/再核验失败：{exc}")
                return
        st.session_state[_SESSION_KEY] = session
        st.rerun()

    if have_v2:
        r2 = session.rounds[2]
        cmp_report = compare_rounds(session)
        st.success(f"V2 已生成并完成再核验：{r2.report.claim_count} 条声明｜"
                   + "｜".join(f"{STATUS_CN[s]} {r2.report.counts()[s]}"
                              for s in STATUS_ORDER if r2.report.counts()[s]))
        if not cmp_report.comparable:
            st.warning(cmp_report.snapshot_note)
        else:
            col_a, col_b, col_c = st.columns(3)
            col_a.metric("已确认修复", len(cmp_report.outcomes("fixed")))
            col_b.metric("口径澄清后通过", len(cmp_report.outcomes("clarified")))
            col_c.metric("新引入错误", len(cmp_report.outcomes("regression")))
        with st.expander("逐条对比（原文 → 首轮 → 修订 → 再核验）"):
            for item in cmp_report.items:
                if item.outcome == "unchanged":
                    continue
                st.write(f"- `{item.cid}`：{item.round1_status} → {item.round2_status}"
                         f"（{item.outcome}）{item.note}")
        st.download_button("下载 V2 修订稿（MD）",
                           data=session.versions["V2"].markdown.encode("utf-8"),
                           file_name=f"memo_V2_{session.code}_{session.period}.md",
                           key="fs_bench_dl_v2md")
        st.download_button("下载 V2 修订稿（DOCX）",
                           data=memo_to_docx_bytes(session.versions["V2"].memo),
                           file_name=f"memo_V2_{session.code}_{session.period}.docx",
                           key="fs_bench_dl_v2docx")
        st.download_button("下载再核验报告（MD）",
                           data=report_to_markdown(r2.report).encode("utf-8"),
                           file_name=f"verify_V2_{session.code}.md", key="fs_bench_dl_r2md")
        st.download_button("下载再核验报告（DOCX）",
                           data=report_to_docx_bytes(r2.report),
                           file_name=f"verify_V2_{session.code}.docx", key="fs_bench_dl_r2docx")
        st.download_button("下载修订记录（MD）",
                           data=revisions_to_markdown(session).encode("utf-8"),
                           file_name=f"revisions_{session.code}_{session.period}.md",
                           key="fs_bench_dl_rev")
        st.download_button("下载前后对比摘要（MD）",
                           data=comparison_to_markdown(session).encode("utf-8"),
                           file_name=f"comparison_{session.code}_{session.period}.md",
                           key="fs_bench_dl_cmp")

    st.divider()
    st.download_button("下载首轮核验报告（MD）",
                       data=report_to_markdown(r1.report).encode("utf-8"),
                       file_name=f"verify_V1_{session.code}.md", key="fs_bench_dl_r1md")
