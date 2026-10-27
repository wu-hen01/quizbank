"""调用任意 OpenAI 兼容接口，把原始文本结构化。

返回 None 表示没配置或调用失败，调用方应退回规则解析的结果。
"""
import json
import re
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from . import config

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "[::1]"}


def _is_loopback(url: str) -> bool:
    """本地推理服务（Ollama、LM Studio）必须绕开系统代理，否则会被代理打回 502。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in _LOOPBACK_HOSTS or host.startswith("127.")

SYSTEM_PROMPT = """你是「错题结构化助手」。用户会给你一段从网页复制的原始文本，\
里面可能夹着题目、选项、答案、解析和一堆页面噪声。

只输出一个 JSON 对象，不要输出解释、不要用代码块包裹。字段如下：

- is_question: 布尔。这段文本里是否确实包含一道题目。
- stem: 题干。去掉题号、选项、答案标注和解析后的纯题目。
- qtype: "single" | "multiple" | "judge" | "fill" | "short" | "unknown"
- options: 选项数组，元素形如 {"key": "A", "text": "选项内容"}；没有选项就输出 []
- my_answer: 用户自己选的答案（例如「我的答案：B」），没有就输出 ""
- correct_answer: 正确答案（例如「正确答案：B」），没有就输出 ""
- analysis: 题目解析，没有就输出 ""
- subject: 学科名，用常见标准叫法，例如「高等数学」「线性代数」「计算机网络」「马克思主义基本原理」
- chapter: 所属章节或模块，没有就输出 ""
- knowledge_points: 1 到 4 个知识点短语，粒度适中，例如「二叉树遍历」「基尔霍夫电流定律」
- difficulty: 1 到 5 的整数，1 最简单

硬性要求：
1. 严格保留原文的语言和公式，不要改写题干，不要润色。
2. 不要编造原文里没有的选项或答案；没有把握就留空字符串。
3. 如果文本里有多道题，只处理第一道完整的题。
4. 选项可能写在同一行（如「A.甲 B.乙」），要正确拆开。
5. 判断题的选项常常是「正确 / 错误」，此时 qtype 用 "judge"。
6. stem 必须是题目的**完整原始表述**。如果原文的题目只有一句话（例如「状态空间——农夫、狼、羊、青菜问题」），
   就把这句话原样作为 stem；不要把题干缩写成标题，也不要只取冒号前后的一半。
7. 页面上的导航、课程名、学生姓名、分值、「提交时间」「评阅状态」这类表格列，都属于噪声，不要写进任何字段。
8. 简答题 / 论述题里跟随在题目后面的那一大段文字，通常是**用户自己的作答**，
   放 my_answer（超过一句话时可以截取开头），不要当成 analysis。
9. analysis 优先放原文里明确标注的「解析 / 答案解析 / 参考答案解析」内容；
   **如果原文没有解析，就自己写一段 2 到 6 行的简明解题要点**（关键步骤、为什么选它、
   易错点），并以「（AI 讲解）」开头，让人一眼看出这段不是你从原文抄的。
   如果连题目都看不懂，就留空字符串。"""


def _strip_fence(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _as_str(value: Any, limit: int = 4000) -> str:
    if isinstance(value, str):
        return value.strip()[:limit]
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _normalize(obj: Dict[str, Any]) -> Dict[str, Any]:
    options = []
    raw_options = obj.get("options")
    if isinstance(raw_options, list):
        for item in raw_options[:10]:
            if isinstance(item, dict):
                key = _as_str(item.get("key"), 2).upper()
                text = _as_str(item.get("text"), 1000)
                if key and text:
                    options.append({"key": key, "text": text})
            elif isinstance(item, str) and item.strip():
                options.append({"key": "", "text": item.strip()[:1000]})

    kps = []
    raw_kps = obj.get("knowledge_points")
    if isinstance(raw_kps, list):
        for item in raw_kps[:6]:
            text = _as_str(item, 60)
            if text and text not in kps:
                kps.append(text)

    try:
        difficulty = int(obj.get("difficulty", 2))
    except (TypeError, ValueError):
        difficulty = 2
    difficulty = max(1, min(5, difficulty))

    qtype = _as_str(obj.get("qtype"), 20).lower()
    if qtype not in {"single", "multiple", "judge", "fill", "short", "unknown"}:
        qtype = "unknown"

    return {
        "is_question": bool(obj.get("is_question", True)),
        "stem": _as_str(obj.get("stem"), 3000),
        "qtype": qtype,
        "options": options,
        "my_answer": _as_str(obj.get("my_answer"), 60),
        "correct_answer": _as_str(obj.get("correct_answer"), 60),
        "analysis": _as_str(obj.get("analysis"), 3000),
        "subject": _as_str(obj.get("subject"), 40),
        "chapter": _as_str(obj.get("chapter"), 80),
        "knowledge_points": kps,
        "difficulty": difficulty,
    }


def _post(endpoint: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST 到 chat/completions，对限流/超时/5xx 做几次退避重试。"""
    last = None
    for attempt in range(3):
        try:
            with httpx.Client(
                timeout=config.LLM_TIMEOUT, trust_env=not _is_loopback(endpoint)
            ) as client:
                resp = client.post(endpoint, json=payload, headers=headers)
                if resp.status_code == 400 and "response_format" in payload:
                    # 部分兼容接口不认 response_format，去掉再试
                    payload = {k: v for k, v in payload.items() if k != "response_format"}
                    resp = client.post(endpoint, json=payload, headers=headers)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    last = "HTTP %d" % resp.status_code
                    time.sleep(1.5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp.json()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = type(exc).__name__
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise RuntimeError("接口多次重试仍失败：%s" % last)


def structure(raw_text: str, url: str = "", title: str = "") -> Optional[Dict[str, Any]]:
    if not config.LLM_ENABLED:
        return None

    user_prompt = (
        f"页面标题：{title or '（无）'}\n"
        f"页面地址：{url or '（无）'}\n\n"
        f"原始内容：\n{raw_text[:6000]}"
    )
    payload = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    endpoint = f"{config.LLM_BASE_URL}/chat/completions"

    try:
        data = _post(endpoint, headers, payload)
        content = data["choices"][0]["message"]["content"]
        obj = json.loads(_strip_fence(content))
        if not isinstance(obj, dict):
            return None
        return _normalize(obj)
    except Exception:
        return None
