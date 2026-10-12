"""研究报告核验页面（方向5）：验证控制台（UI 升级版）。

状态概览（数字卡 + 分布条）→ 按状态筛选 → 逐条「原文 vs 参考」对照。
业务逻辑、widget key 与断言锚点文本不变。
"""
import streamlit as st

from finsight.datasources.em_statements import STATEMENT_KINDS
from finsight.datasources.symbols import to_em_symbol
from finsight.finance.field_mapping import standardize_statement
from finsight.ui.components import badge, dist_bar, kpi_cards, page_header
from finsight.ui.view import fetch_with_source
from finsight.verify.claims import extract_claims
from finsight.verify.extract import DocumentError, extract_fragments
from finsight.verify.reference import build_reference_snapshot
from finsight.verify.report import report_to_docx_bytes, report_to_markdown
from finsight.verify.rules import STATUS_CN, STATUS_ORDER, verify_claim_set

_DATA_KEY = "fs_verify_data"
_RESULT_KEY = "fs_verify_result"
_CTX_KEY = "fs_verify_ctx"
MEMO_KEY = "fs_memo_result"      # 阶段5 备忘录（只读复用）
MEMO_DATA_KEY = "fs_memo_data"

_STATUS_TONE = {"pass": "pass", "fail": "fail", "insufficient_evidence": "neutral",
                "unable_to_verify": "neutral", "needs_review": "warn"}


def _memo_evidence_values(memo) -> dict | None:
    """把备忘录证据登记转为引用核验表：eid -> (公司, 期间, 指标, 值)。"""
    if memo is None or memo.evidence is None:
        return None
    values = {}
    metric_map = {"REV": "revenue", "NP": "net_profit", "PNP": "parent_net_profit",
                  "COST": "operate_cost", "OCF": "operating_cash_flow",
                  "ASSETS": "total_assets", "LIAB": "total_liabilities",
                  "AR": "accounts_receivable"}
    for eid, e in memo.evidence.evidences.items():
        parts = eid.split("-")
        metric = metric_map.get(parts[1]) if len(parts) > 2 else None
        period = parts[-1] if len(parts) > 2 else ""
        values[eid] = (memo.code, period, metric, e.value)
    return values


def _memo_fragments(memo):
    """备忘录正文 → 片段（章节标题作为 section，含 LLM 定性文本）。"""
    from finsight.verify.extract import Fragment
    frags = []
    for sec in memo.sections:
        if sec.llm_text:
            frags.append(Fragment(sec.llm_text, f"章节「{sec.title}」（LLM 定性）", section=sec.title))
        for fact in sec.facts:
            frags.append(Fragment(fact, f"章节「{sec.title}」", section=sec.title))
    return frags


def render_verify_page() -> None:
    page_header("独立报告核验", "验证控制台｜参考值独立重算（V1–V5）· 不采信报告自身内容 · 通过仅覆盖已提取声明")

    mode = st.radio("核验入口", ["核验本会话备忘录", "上传报告文件（MD/DOCX）"],
                    key="fs_verify_mode", horizontal=True)

    uploaded_file = None
    uploaded_bytes = None
    if mode == "上传报告文件（MD/DOCX）":
        uploaded_file = st.file_uploader("上传研究报告", type=["md", "docx"],
                                         key="fs_verify_file",
                                         help="仅支持 .md / .docx，≤10MB；正文仅做文本提取，不会执行其中内容")
        if uploaded_file is not None:
            uploaded_bytes = uploaded_file.getvalue()
            try:
                from finsight.verify.extract import validate_upload
                validate_upload(uploaded_file.name, uploaded_bytes)
            except DocumentError as exc:
                st.error(f"文件校验未通过：{exc}")
                return
            st.caption(f"已选择文件：{uploaded_file.name}（{len(uploaded_bytes) / 1024:.0f} KB）")

    memo = st.session_state.get(MEMO_KEY)
    if mode == "核验本会话备忘录":
        if memo is None:
            st.info("当前会话尚未生成投资备忘录：请先在「投资备忘录」页生成，"
                    "或改用文件上传入口。")
            return
        st.caption(f"将核验备忘录：{memo.code}｜目标报告期 {memo.period}｜生成于 {memo.generated_at}")
        default_code = memo.code
    else:
        if uploaded_file is None:
            st.info("请上传 .md 或 .docx 报告文件。")
            return
        default_code = ""

    code = st.text_input("目标公司 A 股代码（参考数据归属）", value=default_code,
                         placeholder="如 600519", key="fs_verify_code")

    col_btn, col_ctx = st.columns([1, 3])
    with col_btn:
        st.write("")
        run = st.button("开始核验", type="primary", key="fs_verify_run")

    # 上下文指纹：任一输入变化 → 旧结果失效
    ctx = (mode, (uploaded_file.name if uploaded_file else ""),
           (memo.code if mode.startswith("核验本会话") else ""), code.strip())
    if st.session_state.get(_CTX_KEY) != ctx:
        st.session_state.pop(_RESULT_KEY, None)
        st.session_state[_CTX_KEY] = ctx

    if run:
        code_clean = code.strip()
        try:
            to_em_symbol(code_clean)
        except ValueError as exc:
            st.error(f"股票代码无效：{exc}")
            return
        with st.status("正在独立核验…", expanded=True) as status:
            st.write("获取参考财务数据（本地缓存优先）…")
            std, sources, errors = {}, {}, {}
            for kind in STATEMENT_KINDS:
                try:
                    raw, source = fetch_with_source(code_clean, kind)
                    std[kind] = standardize_statement(kind, raw)
                    sources[kind] = source
                except Exception as exc:
                    errors[kind] = str(exc)
            income_std = std.get("income")
            if income_std is None or income_std.empty:
                st.error("利润表不可用：无法构建核验参考数据（至少需要利润表）。"
                         + (f" 错误：{errors.get('income', '')}" if errors.get("income") else ""))
                return
            st.write("构建参考快照并独立重算（V1–V5）…")
            snap = build_reference_snapshot(to_em_symbol(code_clean), income_std,
                                            std.get("balance"), std.get("cashflow"),
                                            sources=sources)
            if mode == "核验本会话备忘录":
                fragments = _memo_fragments(memo)
                target_desc = f"FinSight 备忘录（{memo.code}，{memo.period}，生成于 {memo.generated_at}）"
                snapshot_values = _memo_evidence_values(memo)
            else:
                fragments = extract_fragments(uploaded_file.name, uploaded_bytes)
                target_desc = f"上传文件 {uploaded_file.name}"
                snapshot_values = None
            claim_set = extract_claims(fragments)
            report_data = verify_claim_set(
                claim_set, snap, snapshot_values=snapshot_values,
                fragments_text="\n".join(f.text for f in fragments),
                target_desc=target_desc)
            kind_cn = {"balance": "资产负债表", "income": "利润表", "cashflow": "现金流量表"}
            for kind, err in errors.items():
                report_data.source_findings.append(
                    f"{kind_cn[kind]} 获取失败（{err}）：依赖该表的声明将无法核验")
            st.session_state[_RESULT_KEY] = (report_data, claim_set, snap.fingerprint)
            st.write(f"完成：提取 {report_data.claim_count} 条声明。")
            status.update(label="核验完成", state="complete", expanded=False)

    result = st.session_state.get(_RESULT_KEY)
    if not result:
        st.info("确认股票代码与核验对象后点击「开始核验」。")
        return
    report_data, claim_set, fingerprint = result

    counts = report_data.counts()
    st.success(f"核验完成：共提取 {report_data.claim_count} 条声明｜"
               + "｜".join(f"{STATUS_CN[s]} {counts[s]}" for s in STATUS_ORDER if counts[s]))
    if report_data.source_findings:
        for finding in report_data.source_findings:
            st.warning(finding)
    if claim_set.extraction_notes:
        for note in claim_set.extraction_notes:
            st.caption(f"提取说明：{note}")
    st.caption(f"参考数据快照指纹：{fingerprint}（用于识别报告生成后数据是否变化）｜"
               "「通过」仅覆盖成功提取并核验的声明；参考数据与备忘录同源，为独立计算路径比对")

    # ---- 状态概览：数字卡 + 分布条（真实计数） ----
    kpi_cards([{ "label": STATUS_CN[s], "value": counts.get(s, 0), "unit": "条",
                 "badge": ({"text": "重点" if s in ("fail", "needs_review") else "OK",
                            "tone": _STATUS_TONE[s]} if counts.get(s) else None),
                 "sub": None}
                for s in ("pass", "fail", "insufficient_evidence", "unable_to_verify",
                          "needs_review") if counts.get(s)])
    dist_bar(counts)

    filter_status = st.multiselect("按状态筛选", STATUS_ORDER, default=STATUS_ORDER,
                                   format_func=lambda s: STATUS_CN[s], key="fs_verify_filter")
    shown = [r for r in report_data.results if r.status in filter_status]
    for r in shown:
        title = f"{STATUS_CN[r.status]}｜{r.cid}｜{r.location}"
        with st.expander(title):
            st.markdown(badge(STATUS_CN[r.status], _STATUS_TONE[r.status])
                        + (f'　<span class="fs-eid">{r.cid}</span>' if r.cid else ""),
                        unsafe_allow_html=True)
            col_src, col_ref = st.columns(2)
            with col_src:
                st.markdown("**报告原文**")
                st.write(r.claim_text)
                if r.claimed_display:
                    st.caption(f"声称值：{r.claimed_display}")
            with col_ref:
                st.markdown("**独立参考数据**")
                if r.reference_display:
                    st.write(f"{r.reference_display}"
                             + (f"（{r.ref_period}，{r.ref_basis}）" if r.ref_period else ""))
                else:
                    st.write("—")
                st.caption(f"来源：{r.ref_source}")
            if r.difference:
                st.write(f"**差异**：{r.difference}")
            if r.diff_reason:
                st.write(f"**原因**：{r.diff_reason}")
            if r.basis_detail:
                st.write(f"**核验依据**：{r.basis_detail}")
            if r.suggestion:
                st.write(f"**建议修订方向**：{r.suggestion}")
            for lim in r.limitations:
                st.caption(f"限制：{lim}")

    st.divider()
    md = report_to_markdown(report_data)
    st.download_button("下载核验报告（Markdown）", data=md.encode("utf-8"),
                       file_name=f"verify_{report_data.code}.md", mime="text/markdown",
                       key="fs_verify_dl_md")
    st.download_button("下载核验报告（DOCX）", data=report_to_docx_bytes(report_data),
                       file_name=f"verify_{report_data.code}.docx",
                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                       key="fs_verify_dl_docx")
    st.caption("核验结果不会自动修改原始报告；自动修订闭环留待后续阶段。")
