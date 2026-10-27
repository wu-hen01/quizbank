"""启动入口：python run.py"""
from app import config

import uvicorn


def _say(zh, en=""):
    """打印一行；控制台编码不支持中文时退回英文，避免启动直接崩。"""
    try:
        print(zh)
    except UnicodeEncodeError:
        if en:
            print(en)
        else:
            print(zh.encode("ascii", "replace").decode("ascii"))


if __name__ == "__main__":
    _say("=" * 56)
    _say("  QuizBank · 本地错题题库", "  QuizBank - local question bank")
    _say("=" * 56)
    _say(f"  页面   http://{config.HOST}:{config.PORT}", f"  page   http://{config.HOST}:{config.PORT}")
    _say(f"  数据库 {config.DB_PATH}", f"  db     {config.DB_PATH}")
    if config.LLM_ENABLED:
        _say(f"  AI     {config.LLM_MODEL} @ {config.LLM_BASE_URL}")
    else:
        _say(
            "  AI     未配置（只用规则解析，可在 .env 里填 LLM_API_KEY）",
            "  AI     not configured (rule-based parsing only; set LLM_API_KEY in .env)",
        )
    _say("  停止   Ctrl + C", "  stop   Ctrl + C")
    _say("=" * 56)
    uvicorn.run("app.main:app", host=config.HOST, port=config.PORT, log_level="info")
