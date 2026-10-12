"""全局配置：路径与缓存策略。"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"

# 缓存有效期（小时）。财报数据更新频率低，当日缓存即可。
CACHE_TTL_HOURS = 12

ENV_FILE = PROJECT_ROOT / ".env"


def load_env_file() -> dict[str, str]:
    """加载项目根目录 .env（本仓库已 gitignore，密钥不入库）。

    - 纯标准库实现，不引入 python-dotenv；
    - 已存在于 os.environ 的变量不覆盖（系统环境优先）；
    - 测试可设 FINSIGHT_IGNORE_ENV_FILE=1 跳过（保证离线/mock 测试不受真实密钥影响）；
    - 返回本次实际注入的变量（不含值，仅键名计数用途不返回值以防泄漏）。
    """
    if os.environ.get("FINSIGHT_IGNORE_ENV_FILE") == "1" or not ENV_FILE.exists():
        return {}
    loaded: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = "<loaded>"
    return loaded


def ensure_dirs() -> None:
    """确保数据目录存在（幂等）。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
