"""工具调用型 Agent：LLM 自主决定查什么数据，输出经现有校验护栏。

架构：DeepSeek function calling + 只读工具注册 → 模型自主查数 →
     定性分析文本 → 现有 LLM 校验层（结构/引用 ID/数字白名单）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field as dc_field
from typing import Callable

import pandas as pd

from finsight.audit import audit_event
from finsight.finance.scoring import InvestmentScore
from finsight.finance.signals_v2 import RiskAssessment
from finsight.finance.technical import TechnicalView
from finsight.memo.llm import LLMConfig, LLMError, validate_llm_sections
from finsight.memo.skeleton import InvestmentMemo

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 10


@dataclass
class ToolCall:
    name: str
    arguments: dict
    result: str = ""


@dataclass
class AgentResult:
    """Agent 执行结果：文本 + 调用链（供审计与可解释性）。"""
    sections: dict[str, str] = dc_field(default_factory=dict)
    issues: list[str] = dc_field(default_factory=list)
    tool_calls: list[ToolCall] = dc_field(default_factory=list)
    status: str = "ok"  # ok / tool_limit / api_error / invalid_output


# ---------------- 工具注册表 ----------------

def build_tools(risk: RiskAssessment, tech: TechnicalView | None,
                score: InvestmentScore, memo: InvestmentMemo) -> list[dict]:
    """注册只读工具（OpenAI function calling 格式）。"""
    tools = [
        {"type": "function", "function": {
            "name": "get_risk_signals",
            "description": "获取 S1–S7 财务风险预警信号详情",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }},
        {"type": "function", "function": {
            "name": "get_technical_analysis",
            "description": "获取技术面分析（均线/MACD/RSI/KDJ/支撑压力位）",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }},
        {"type": "function", "function": {
            "name": "get_investment_score",
            "description": "获取四维加权评分模型结果（基本面/财务/技术/风险）",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }},
        {"type": "function", "function": {
            "name": "get_financial_evidence",
            "description": "获取财务证据摘要（带证据编号 E-XXX-期间）",
            "parameters": {"type": "object", "properties": {}, "required": []},
        }},
    ]
    return tools


def _execute_tool(name: str, risk, tech, score, memo) -> str:
    """执行工具调用，返回文本结果。"""
    if name == "get_risk_signals":
        lines = [f"预警等级: {risk.level_cn}（{risk.triggered_count}/7 项触发）"]
        for s in risk.signals:
            lines.append(f"  {s.sid} {s.name}: {'触发' if s.triggered else '未触发'} — {s.detail}")
        return "\n".join(lines)
    if name == "get_technical_analysis":
        if tech is None:
            return "K线数据不可用"
        return (f"趋势: {tech.trend}\nMACD: {tech.macd_signal}\n"
                f"RSI: {tech.rsi14} {tech.rsi_signal}\nKDJ: {tech.kdj_signal}\n"
                f"支撑: {tech.support} 压力: {tech.resistance}")
    if name == "get_investment_score":
        lines = [f"总分: {score.total} → {score.rating_cn}（置信度{score.confidence}）"]
        for d in score.dimensions:
            lines.append(f"  {d.name}: {d.score:.0f} (权重{d.weight:.0%})")
        return "\n".join(lines)
    if name == "get_financial_evidence":
        if memo.evidence is None:
            return "无证据数据"
        lines = []
        for eid in memo.evidence.ids()[:20]:
            e = memo.evidence.get(eid)
            val = "（规则触发）" if e.value is None else f"{e.value}{e.unit}"
            lines.append(f"[{eid}] {e.label} = {val}")
        return "\n".join(lines)
    return f"未知工具: {name}"


AGENT_SYSTEM_PROMPT = (
    "你是 FinSight 智研投的投研 Agent。你可以调用工具获取实时分析数据，"
    "然后基于获取到的数据撰写投资备忘录的定性分析章节。\n"
    "必须遵守：\n"
    "1. 先调用工具获取数据，再基于获取到的真实数据撰写分析；\n"
    "2. 只能使用工具返回的事实，禁止新增任何财务数字；\n"
    "3. 引用事实时使用工具返回的证据编号，格式如 [E-REV-2024Q4]；\n"
    "4. 区分事实与推断：推断需标明'推断'；\n"
    "5. 只输出 JSON 对象，键为 summary/company/thesis/scenarios，值为中文文本。"
)


def run_agent(cfg: LLMConfig, memo: InvestmentMemo,
              risk: RiskAssessment, tech: TechnicalView | None,
              score: InvestmentScore,
              focus_question: str = "") -> AgentResult:
    """执行 Agent 循环：模型自主调用工具 → 汇总 → 生成定性章节。"""
    result = AgentResult()
    tools = build_tools(risk, tech, score, memo)
    import requests

    messages = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": focus_question or "请分析该公司的投资价值，并生成备忘录定性章节。"},
    ]
    session = requests.Session()
    session.trust_env = False

    for _ in range(MAX_TOOL_CALLS):
        try:
            resp = session.post(
                cfg.base_url.rstrip("/") + "/chat/completions",
                json={"model": cfg.model, "messages": messages,
                      "tools": tools, "temperature": 0.3},
                headers={"Authorization": f"Bearer {cfg.api_key}",
                         "Content-Type": "application/json"},
                timeout=cfg.timeout)
            if resp.status_code != 200:
                result.status = "api_error"
                result.issues.append(f"API 返回 HTTP {resp.status_code}")
                return result
            choice = resp.json()["choices"][0]["message"]
        except Exception as exc:
            result.status = "api_error"
            result.issues.append(f"Agent 请求失败: {type(exc).__name__}")
            return result

        # 模型选择调用工具
        if choice.get("tool_calls"):
            messages.append(choice)
            for tc in choice["tool_calls"]:
                fn = tc["function"]
                call = ToolCall(name=fn["name"], arguments=json.loads(fn.get("arguments", "{}")))
                call.result = _execute_tool(call.name, risk, tech, score, memo)
                result.tool_calls.append(call)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": call.result,
                })
                audit_event("agent_tool_call", tool=call.name, result_len=len(call.result))
            continue

        # 模型返回最终文本（无工具调用）
        content = choice.get("content", "")
        if content:
            # 解析 JSON 输出并校验
            try:
                cleaned = content.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
                data = json.loads(cleaned)
                valid_eids = set(memo.evidence.ids()) if memo.evidence else set()
                from finsight.memo.llm import build_number_allowlist
                allowlist = build_number_allowlist(memo)
                sections, issues = validate_llm_sections(data, valid_eids, allowlist)
                result.sections = sections
                result.issues = issues
                result.status = "ok"
                audit_event("agent_completed", tool_calls=len(result.tool_calls),
                            sections=len(sections), issues=len(issues))
                return result
            except (json.JSONDecodeError, KeyError):
                result.status = "invalid_output"
                result.issues.append("Agent 输出非有效 JSON")
                return result

    result.status = "tool_limit"
    result.issues.append(f"达到最大工具调用次数 ({MAX_TOOL_CALLS})")
    return result
