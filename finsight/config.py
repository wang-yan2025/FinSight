"""全局配置：路径与缓存策略。"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"

# 缓存有效期（小时）。财报数据更新频率低，当日缓存即可。
CACHE_TTL_HOURS = 12


def ensure_dirs() -> None:
    """确保数据目录存在（幂等）。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
