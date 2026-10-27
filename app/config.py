"""全局配置。

优先级：真实的系统环境变量 > 项目根目录的 .env > 默认值。
改完 .env 需要重启服务。
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(
    os.environ.get("QUIZBANK_DATA_DIR") or (BASE_DIR / "data")
).expanduser().resolve()
DB_PATH = DATA_DIR / "quizbank.db"

HOST = os.environ.get("QUIZBANK_HOST", "127.0.0.1")
PORT = int(os.environ.get("QUIZBANK_PORT", "8765"))

# 留空表示不校验；设置后扩展需要在选项页填同样的 token
TOKEN = (os.environ.get("QUIZBANK_TOKEN") or "").strip()

# 任意 OpenAI 兼容接口：DeepSeek / OpenAI / 智谱 / 通义 / 本地 Ollama
LLM_BASE_URL = (os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")
LLM_API_KEY = (os.environ.get("LLM_API_KEY") or "").strip()
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "45"))
LLM_ENABLED = bool(LLM_API_KEY)

# ---- 图片 OCR（Windows 自带引擎，无需安装）----
IMAGE_DIR = DATA_DIR / "images"
OCR_ENABLED = (os.environ.get("OCR_ENABLED") or "1").strip().lower() not in (
    "0", "false", "no", "off",
)
OCR_LANG = (os.environ.get("OCR_LANG") or "zh-Hans-CN").strip()
# 单次最多识别多大图片，超了直接拒绝，避免大图把内存撑爆
OCR_MAX_BYTES = int(os.environ.get("OCR_MAX_BYTES", str(12 * 1024 * 1024)))

STATIC_DIR = BASE_DIR / "static"
