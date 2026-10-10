"""FinSight 智研投 —— Streamlit 入口。

阶段1：导航骨架。三个功能页在后续阶段逐一实现。
"""
import streamlit as st

st.set_page_config(page_title="FinSight 智研投", page_icon="📊", layout="wide")

PAGES = {
    "财报分析（方向2）": "阶段2 待实现：财务指标计算引擎（数据层已就绪）。",
    "买方备忘录（方向6）": "阶段3 待实现：基于指标与证据链的备忘录生成。",
    "研报纠错（方向5）": "阶段4 待实现：PDF 解析与规则核查引擎。",
}

st.title("FinSight 智研投")
st.caption("上市公司财报分析 · 买方备忘录 · 研报纠错 —— 纯规则版（LLM 通道预留）")

with st.sidebar:
    choice = st.radio("功能导航", list(PAGES))

st.divider()
st.info(PAGES[choice])
st.markdown(
    "- 技术栈：Python + Streamlit + akshare（东方财富）\n"
    "- 数据缓存：本地 CSV，12 小时 TTL，断网回退过期缓存\n"
    "- 原则：财务计算全部由程序完成；结论关联证据或标注待核验"
)
