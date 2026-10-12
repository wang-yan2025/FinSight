"""通用 UI 组件：页头、KPI 卡、徽标、步骤条、分布条、对比卡。

全部为展示层封装（st.markdown + HTML/CSS），不含业务逻辑；
动态文本一律 html.escape 后进入 HTML。
"""
from __future__ import annotations

import html

import streamlit as st

from finsight.ui.styles import C


def esc(value) -> str:
    return html.escape(str(value), quote=True)


# ---------------- 页头横幅 ----------------

def page_header(title: str, subtitle: str, chips: list[tuple[str, str]] | None = None) -> None:
    """统一页面标题栏：渐变横幅 + 状态 chips。"""
    chips_html = "".join(
        f'<span class="fs-chip">{esc(k)}：<b>{esc(v)}</b></span>' for k, v in (chips or [])
    )
    st.markdown(
        f'<div class="fs-hero"><div><h1>{esc(title)}</h1>'
        f'<div class="fs-hero-sub">{esc(subtitle)}</div></div>'
        f'<div class="fs-chiprow">{chips_html}</div></div>',
        unsafe_allow_html=True)


def section_title(text: str) -> None:
    st.markdown(f'<div class="fs-sec-title">{esc(text)}</div>', unsafe_allow_html=True)


def status_chip_row(label: str, text: str, tone: str = "neutral") -> None:
    """次级状态标签行（替代裸 caption，如 LLM 配置状态）。"""
    st.markdown(
        f'<div class="fs-chip-line"><span class="cap">{esc(label)}</span>'
        f'{badge(text, tone)}</div>', unsafe_allow_html=True)


# ---------------- KPI 卡 ----------------

def kpi_cards(cards: list[dict]) -> None:
    """KPI 卡组：使用 Streamlit 原生 st.metric + st.columns（保证渲染兼容性）。"""
    if not cards:
        return
    n = len(cards)
    cols = st.columns(n)
    for i, c in enumerate(cards):
        with cols[i]:
            badge_text = c.get("badge", {}).get("text", "") if c.get("badge") else ""
            st.metric(
                label=c.get("label", ""),
                value=f"{c.get('value', '—')} {c.get('unit', '')}".strip(),
                help=c.get("sub", ""))
            if badge_text:
                tone = c.get("badge", {}).get("tone", "neutral")
                color = {"pass": "#0E8C7E", "fail": "#C0392B", "warn": "#B7791F",
                         "info": "#376CFB", "neutral": "#667085"}.get(tone, "#667085")
                st.markdown(
                    f'<div style="font-size:.75rem;color:{color};font-weight:600;'
                    f'margin-top:-8px;">{esc(badge_text)}</div>',
                    unsafe_allow_html=True)


# ---------------- 状态徽标 ----------------

_TONE = {"pass": "pass", "fail": "fail", "warn": "warn", "info": "info", "neutral": "neutral"}


def badge(text: str, tone: str = "neutral") -> str:
    return f'<span class="fs-b fs-b-{_TONE.get(tone, "neutral")}">{esc(text)}</span>'


def badges_row(pairs: list[tuple[str, str]]) -> str:
    return " ".join(badge(t, tone) for t, tone in pairs)


# ---------------- 核验状态分布条 ----------------

_DIST_COLORS = {"pass": C["teal"], "fail": "#E05B5B", "insufficient_evidence": "#B9C4D6",
                "unable_to_verify": "#98A6BF", "needs_review": "#E8B44F"}
_DIST_CN = {"pass": "通过", "fail": "不通过", "insufficient_evidence": "证据不足",
            "unable_to_verify": "无法核验", "needs_review": "需人工复核"}


def dist_bar(counts: dict[str, int]) -> None:
    """核验五态分布条（宽度=真实占比，零值不渲染）。"""
    total = sum(counts.values())
    if total == 0:
        return
    segs = "".join(
        f'<i style="width:{counts[k] / total * 100:.2f}%;background:{_DIST_COLORS[k]}"></i>'
        for k in ("pass", "fail", "insufficient_evidence", "unable_to_verify", "needs_review")
        if counts.get(k))
    legend = "".join(
        f'<span><s style="background:{_DIST_COLORS[k]}"></s>{_DIST_CN[k]} {counts.get(k, 0)}</span>'
        for k in ("pass", "fail", "insufficient_evidence", "unable_to_verify", "needs_review")
        if counts.get(k))
    st.markdown(f'<div class="fs-dist">{segs}</div>'
                f'<div class="fs-legend">{legend}</div>', unsafe_allow_html=True)


# ---------------- 工作流步骤条 ----------------

def stepper(steps: list[str], states: list[str]) -> None:
    """states ∈ todo/active/done/error（由业务状态推导，见 ui.flow）。"""
    marks = {"done": "✓", "active": "●", "error": "!", "todo": ""}
    nums = {s: str(i + 1).zfill(2) for i, s in enumerate(steps)}
    html_steps = []
    for i, (step, state) in enumerate(zip(steps, states)):
        mark = marks.get(state, "") or nums[step]
        html_steps.append(
            f'<div class="fs-step {state}"><div class="dot">{mark}</div>'
            f'<div class="lbl">{esc(step)}</div><div class="bar"></div></div>')
    st.markdown(f'<div class="fs-steps">{"".join(html_steps)}</div>', unsafe_allow_html=True)


# ---------------- 修订前后对比卡 ----------------

def diff_card(left_title: str, left_html: str, mid_title: str, mid_html: str,
              right_title: str, right_html: str) -> None:
    st.markdown(
        f'<div class="fs-lift"><div class="fs-diff">'
        f'<div class="col"><h5>{esc(left_title)}</h5>{left_html}</div>'
        f'<div class="col mid"><h5>{esc(mid_title)}</h5>{mid_html}</div>'
        f'<div class="col"><h5>{esc(right_title)}</h5>{right_html}</div>'
        f'</div></div>', unsafe_allow_html=True)


def quote(text: str, ok: bool = False) -> str:
    return f'<div class="fs-quote{" ok" if ok else ""}">{esc(text)}</div>'
