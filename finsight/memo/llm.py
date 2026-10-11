"""备忘录 LLM 调用层：OpenAI 兼容接口，可配置、可 mock、密钥安全。

设计约束：
- 仅负责定性章节生成；财务事实、指标、风险信号由 Python 侧计算后
  以只读摘要形式输入，模型不得新增财务数字或证据。
- 输出为 JSON 文本，经程序化校验（章节键、证据 ID 引用、数字白名单）；
  无效引用替换为 [待核实引用] 并记录 issue，未核验数字追加待核实标记。
  【校验能力边界（如实声明）】程序化检查不等于事实核验：
  a) 数字命中全局白名单 ≠ 其指标归属、单位、报告期正确；
  b) 引用合法证据 ID ≠ 该证据支持所在结论；
  c) 语义级对应关系核验未实现，所有 LLM 内容必须携带
     LLM_PROVENANCE_NOTE 可信度标注。
- 密钥仅经环境变量/配置对象传入，只出现在请求头；不写入日志、异常
  文本、测试或 Git。自动化测试一律 mock，不依赖真实接口。
- 未配置或调用失败时抛出/返回明确状态，由上层保留确定性基础备忘录。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import requests

from finsight.memo.skeleton import InvestmentMemo

ENV_KEY = "FINSIGHT_LLM_API_KEY"
ENV_BASE = "FINSIGHT_LLM_BASE_URL"
ENV_MODEL = "FINSIGHT_LLM_MODEL"

ALLOWED_SECTION_KEYS = ("summary", "company", "thesis", "scenarios")
LLM_SECTION_CN = {"summary": "研究摘要（定性）", "company": "公司概况",
                  "thesis": "投资逻辑", "scenarios": "情景分析"}

# 统一可信度标注：如实描述程序化校验的实际能力边界。
# 程序化检查仅覆盖：输出结构、证据 ID 是否存在、数字是否在证据值白名单内；
# 不覆盖：数字与指标/单位/报告期的对应关系、证据能否支持引用它的结论
# （语义级核验未实现）。所有 LLM 内容必须携带本标注。
LLM_PROVENANCE_NOTE = (
    "LLM 辅助生成；已进行结构、引用 ID 与数字匹配检查；"
    "定性结论及数字与指标、单位、报告期的对应关系尚未经独立核验"
)


class LLMError(Exception):
    """模型调用失败（网络/HTTP/结构/内容异常）。异常文本不包含密钥。"""


@dataclass
class LLMConfig:
    api_key: str | None = None
    base_url: str | None = None      # 如 https://api.example.com/v1
    model: str | None = None
    timeout: int = 120

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            api_key=os.environ.get(ENV_KEY) or None,
            base_url=os.environ.get(ENV_BASE) or None,
            model=os.environ.get(ENV_MODEL) or None,
        )

    def __repr__(self) -> str:
        # 掩码密钥，防止日志/调试输出泄露
        key_state = "已设置" if self.api_key else "未设置"
        return (f"LLMConfig(api_key={key_state}, base_url={self.base_url!r}, "
                f"model={self.model!r}, timeout={self.timeout})")

    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


def call_llm(cfg: LLMConfig, system_prompt: str, user_prompt: str) -> str:
    """调用 OpenAI 兼容 chat/completions，返回模型文本。失败抛 LLMError。"""
    if not cfg.configured():
        raise LLMError("LLM 未配置（需要 API Key、Base URL 与模型名）")
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.api_key}",
               "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}]
    payload = {"model": cfg.model, "messages": messages, "temperature": 0.3,
               "response_format": {"type": "json_object"}}

    def _post(body):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=cfg.timeout)
        except requests.RequestException as exc:
            raise LLMError(f"网络或接口请求异常：{type(exc).__name__}") from None
        if resp.status_code == 400 and "response_format" in body:
            return _post({k: v for k, v in body.items() if k != "response_format"})
        if resp.status_code != 200:
            raise LLMError(f"接口返回 HTTP {resp.status_code}")
        try:
            return resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("接口响应结构异常（缺少 choices[0].message.content）") from None

    content = _post(payload)
    if not content or not content.strip():
        raise LLMError("模型返回内容为空")
    return content


def _parse_json_text(text: str) -> dict:
    """解析模型 JSON 输出；容忍 markdown 代码围栏。"""
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        raise LLMError("模型输出不是有效 JSON")
    if not isinstance(data, dict):
        raise LLMError("模型输出 JSON 不是对象")
    return data


_EID_PATTERN = re.compile(r"\[((?:E-)[A-Z0-9]+(?:-[A-Za-z0-9]+)+)\]")
_NUMBER_PATTERN = re.compile(r"\d[\d,]*(?:\.\d+)?%?")


def build_number_allowlist(memo: InvestmentMemo) -> set[str]:
    """从证据集生成数字白名单（含格式化变体），用于校验模型输出。"""
    allow: set[str] = set()
    if memo.evidence is None:
        return allow
    for e in memo.evidence.evidences.values():
        if e.value is None:
            continue
        v = float(e.value)
        allow.update({f"{v:g}", f"{v:,.0f}", f"{v:.1%}", f"{v:+.1%}", f"{v:.2%}", f"{v:+.2%}",
                      f"{v:.2f}", f"{abs(v):.1%}", f"{abs(v):.2%}"})
        year = e.report_date[:4]
        if year:
            allow.add(year)
    # 报告期标签（如 2024Q4）拆分出的年/季数字
    for eid in memo.evidence.ids():
        for part in re.findall(r"\d{4}Q\d", eid):
            allow.update({part, part[:4], "Q" + part[-1], part[-1]})
    return allow


def _check_numbers(text: str, allowlist: set[str]) -> list[str]:
    """文本中的数字不在白名单 → 记录违规（拦截/标记，不删除）。"""
    problems = []
    for token in _NUMBER_PATTERN.findall(text):
        clean = token.rstrip("%")
        variants = {token, clean, clean.replace(",", "")}
        if not variants & allowlist:
            problems.append(token)
    return problems


def validate_llm_sections(data: dict, valid_eids: set[str],
                          allowlist: set[str]) -> tuple[dict[str, str], list[str]]:
    """校验模型输出：键白名单、证据 ID 引用、数字白名单。

    返回 (合规章节文本, issues)。无效证据引用替换为 [待核实引用]；
    含未核验数字的章节末尾追加待核实标记；不删除模型结论本身。
    """
    sections: dict[str, str] = {}
    issues: list[str] = []
    for key in ALLOWED_SECTION_KEYS:
        text = data.get(key)
        if not isinstance(text, str) or not text.strip():
            issues.append(f"章节 {LLM_SECTION_CN[key]} 缺失或非文本，未采用")
            continue

        def _replace(match):
            eid = match.group(1)
            if eid in valid_eids:
                return match.group(0)
            issues.append(f"章节 {LLM_SECTION_CN[key]} 引用了无效证据 ID {eid}，已标记待核实")
            return "[待核实引用]"

        text = _EID_PATTERN.sub(_replace, text)
        bad_numbers = _check_numbers(text, allowlist)
        if bad_numbers:
            uniq = "、".join(sorted(set(bad_numbers))[:5])
            issues.append(f"章节 {LLM_SECTION_CN[key]} 含未核验数字（{uniq}），已追加待核实标记")
            text = text.rstrip() + "【含未经核验数字，待核实】"
        sections[key] = text.strip()
    unknown = [k for k in data if k not in ALLOWED_SECTION_KEYS]
    if unknown:
        issues.append(f"忽略未知输出键：{unknown}")
    return sections, issues


def _facts_digest(memo: InvestmentMemo, focus_question: str = "") -> str:
    """构造模型输入的事实摘要（只读、已计算、含证据 ID）。"""
    ev = memo.evidence
    lines = [f"标的代码：{memo.code}", f"报告期：{memo.period}",
             f"数据来源：{ev.snapshot.get('data_source', '')}（公司经营材料未接入）"]
    for e in ev.evidences.values():
        val = "（规则触发，非数值）" if e.value is None else f"{e.value}{e.unit}"
        lines.append(f"[{e.eid}] {e.label} = {val}" +
                     (f"（{e.note}）" if e.note else ""))
    if ev.missing:
        lines.append("数据缺口/限制：" + "；".join(ev.missing[:30]))
    if focus_question:
        lines.append(f"研究关注问题（优先回应）：{focus_question}")
    return "\n".join(lines)


SYSTEM_PROMPT = (
    "你是买方研究分析师。基于给定的、已由程序计算核验的财务事实与证据（带 [E-...] 编号）"
    "撰写投资备忘录的定性章节。必须遵守：\n"
    "1. 只能使用给定事实，禁止新增任何财务数字、目标价、收益率、行业排名或竞争结论；\n"
    "2. 引用事实时使用给定证据编号，格式如 [E-REV-2024Q4]；\n"
    "3. 区分事实与推断：推断需标明'推断'；情景为定性假设，不给估值数字；\n"
    "4. 只输出 JSON 对象，键为 summary/company/thesis/scenarios，值为中文文本"
    "（各 100-200 字）；不要输出其他内容。"
)


def generate_llm_sections(cfg: LLMConfig, memo: InvestmentMemo,
                          focus_question: str = "") -> tuple[dict[str, str], list[str]]:
    """生成并校验定性章节。返回 (sections, issues)；失败抛 LLMError。"""
    raw = call_llm(cfg, SYSTEM_PROMPT, _facts_digest(memo, focus_question))
    data = _parse_json_text(raw)
    valid_eids = set(memo.evidence.ids()) if memo.evidence else set()
    return validate_llm_sections(data, valid_eids, build_number_allowlist(memo))


def apply_llm_sections(memo: InvestmentMemo, sections: dict[str, str], issues: list[str]) -> None:
    """把校验后的定性内容合入骨架（不覆盖确定性 facts）。"""
    for sec in memo.sections:
        if sec.key in sections:
            sec.llm_text = sections[sec.key]
            if sec.status in ("llm_pending", "degraded"):
                sec.status = "ready"
    memo.llm_issues = list(issues)
    memo.llm_status = "已生成" if sections else "输出无效"
