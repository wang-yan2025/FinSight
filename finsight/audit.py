"""结构化执行日志（JSONL）：统一 run_id 串联关键步骤。

设计：
- 轻量、无新依赖：标准库 json + 时间戳；默认目录 data/logs/（已 gitignore），
  可经环境变量 FINSIGHT_LOG_DIR 覆盖。
- 无活动 run 时 audit_event 为 no-op（业务代码零开销、测试零噪音）。
- 脱敏：写入前递归清洗疑似密钥字段（sk-…/Bearer/API_KEY 等），
  绝不写入密钥或完整文档正文（只记长度/哈希类信息）。
- 每条事件：ts / run_id / step / status / company / period + 业务字段。
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from finsight.config import PROJECT_ROOT

DEFAULT_LOG_DIR = PROJECT_ROOT / "data" / "logs"

_SECRET_PATTERNS = (re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
                    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{8,}"))
_KEY_NAME = re.compile(r"(?i)(api[_-]?key|password|secret|token)")
_INLINE_KV = re.compile(r"(?i)\b(api[_-]?key|password|secret|token)\s*[=:]\s*([^\s,;」】]+)")
_REDACTED = "«REDACTED»"


def _scrub(value):
    """递归脱敏：敏感键名整体替换；值中密钥样式与内联 key=value 均脱敏。"""
    if isinstance(value, dict):
        return {k: (_REDACTED if _KEY_NAME.search(str(k)) else _scrub(v))
                for k, v in value.items()}
    if isinstance(value, str):
        out = value
        for pat in _SECRET_PATTERNS:
            out = pat.sub(_REDACTED, out)
        out = _INLINE_KV.sub(lambda m: f"{m.group(1)}={_REDACTED}", out)
        return out
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


class RunLogger:
    """一次完整任务的日志文件（JSONL，每行一条事件）。"""

    def __init__(self, company: str = "", period: str = "", log_dir: Path | None = None,
                 run_id: str | None = None, params: dict | None = None):
        self.run_id = run_id or f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        self.company = company
        self.period = period
        self.log_dir = Path(log_dir) if log_dir else _log_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.log_dir / f"run_{self.run_id}.jsonl"
        self._closed = False
        self._write("run_start", "ok", params=params or {})

    def event(self, step: str, status: str = "ok", **fields):
        self._write(step, status, **fields)

    def fail(self, step: str, error, **fields):
        self._write(step, "error", error=f"{type(error).__name__}: {error}"[:500], **fields)

    def finish(self, status: str = "ok", **fields):
        if not self._closed:
            self._write("run_finish", status, **fields)
            self._closed = True

    def _write(self, step: str, status: str, **fields):
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "run_id": self.run_id, "step": step, "status": status,
                  "company": self.company, "period": self.period}
        record.update(_scrub(fields))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def events(self) -> list[dict]:
        return [json.loads(line) for line in
                self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _log_dir() -> Path:
    env = os.environ.get("FINSIGHT_LOG_DIR")
    return Path(env) if env else DEFAULT_LOG_DIR


# ---------------- 当前活动 run（线程内单活动；无活动时全部 no-op） ----------------

_current: RunLogger | None = None


@contextmanager
def start_run(company: str = "", period: str = "", log_dir: Path | None = None,
              params: dict | None = None):
    """开启一次执行 run；块内 audit_event 均写入同一 run_id。"""
    global _current
    logger = RunLogger(company=company, period=period, log_dir=log_dir, params=params)
    previous, _current = _current, logger
    try:
        yield logger
        logger.finish("ok")
    except Exception as exc:
        logger.fail("unhandled_exception", exc)
        logger.finish("error")
        raise
    finally:
        _current = previous


@contextmanager
def use_run(logger: RunLogger):
    """绑定一个已存在的 run（如 Streamlit 会话跨多次交互共用同一 run_id）。

    与 start_run 不同：不创建、不自动 finish；离开上下文即解绑，
    避免页面重渲染路径误写入。
    """
    global _current
    previous, _current = _current, logger
    try:
        yield logger
    finally:
        _current = previous


def audit_event(step: str, status: str = "ok", **fields) -> None:
    """记录事件；无活动 run 时为 no-op（业务与测试零开销）。"""
    if _current is not None:
        _current.event(step, status, **fields)


def audit_fail(step: str, error, **fields) -> None:
    if _current is not None:
        _current.fail(step, error, **fields)


def current_run_id() -> str | None:
    return _current.run_id if _current is not None else None
