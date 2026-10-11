"""FinSight 智研投 —— Streamlit 入口。

导航骨架：财报分析页（阶段4 MVP）与买方备忘录页（阶段5 MVP）已实现，
研报纠错页待后续阶段。
"""
import streamlit as st

from finsight.ui.memo_page import render_memo_page
from finsight.ui.report_page import render_report_page

st.set_page_config(page_title="FinSight 智研投", page_icon="📊", layout="wide")

REPORT_PAGE = "财报分析（方向2）"
MEMO_PAGE = "买方备忘录（方向6）"
PLACEHOLDERS = {
    "研报纠错（方向5）": "待实现（后续阶段）：研报解析与规则核查引擎。",
}

st.title("FinSight 智研投")
st.caption("上市公司财报分析 · 买方备忘录 · 研报纠错 —— 纯规则版（LLM 通道预留）")

with st.sidebar:
    choice = st.radio("功能导航", [REPORT_PAGE, MEMO_PAGE, *PLACEHOLDERS])

st.divider()
if choice == REPORT_PAGE:
    render_report_page()
elif choice == MEMO_PAGE:
    render_memo_page()
else:
    st.info(PLACEHOLDERS[choice])
    st.markdown(
        "- 技术栈：Python + Streamlit + akshare（东方财富）\n"
        "- 数据缓存：本地 CSV，12 小时 TTL，断网回退过期缓存\n"
        "- 原则：财务计算全部由程序完成；结论关联证据或标注待核验"
    )
