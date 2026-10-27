"""规则解析：从网页复制来的原始文本里抽出题干、选项、我的答案、正确答案。

它是 LLM 不可用时的兜底，也用于给 LLM 结果做交叉校验。
"""
import hashlib
import re
import unicodedata
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

# 行首的选项：A. / A、/ A) / A：/ a.
_OPT_LINE = re.compile(r"^\s*([A-Ha-h])\s*[.、．)）:：]\s*(\S.*?)\s*$")
# 题干开头的题号：1. / (1) / （3） / 第2题
_NUM_PREFIX = re.compile(
    r"^\s*(?:第\s*\d+\s*题|\(\s*\d+\s*\)|（\s*\d+\s*）|\d+\s*[.、．)）])\s*"
)
# 题干开头的题型标记：（单选题）/ (多选题) / 【判断题】
_TYPE_PREFIX = re.compile(
    r"^\s*[（(【\[]?\s*(?:单项选择题|多项选择题|不定项选择题|单选题|多选题|判断题|填空题|简答题|问答题|选择题)\s*[）)】\]]?\s*"
)
_ANSWER_MINE = re.compile(
    r"(?:我的答案|你的答案|我的选择|已选答案|我的作答)\s*[:：．.]?\s*([^\n]{0,40})"
)
_ANSWER_RIGHT = re.compile(
    r"(?:正确答案|参考答案|标准答案|答案(?!解析|分析))\s*[:：．.]?\s*([^\n]{0,40})"
)
# 解析块：必须独占行首且有冒号，避免题干里出现"分析"二字被误判
_ANALYSIS = re.compile(
    r"(?m)^[ \t]*(?:答案解析|题目解析|试题解析|解析|分析)\s*[:：][ \t]*([\s\S]+)"
)
_JUDGE_TOKENS = {"正确", "错误", "对", "错", "√", "×", "true", "false", "t", "f"}


def normalize_choice(value: str) -> str:
    raw = (value or "").strip().replace("\u3000", " ")
    raw = re.sub(r"\s+", "", raw)
    raw = re.sub(r"[，,、;；|/]+", "", raw)
    if re.fullmatch(r"[A-Ha-h]+", raw):
        return "".join(sorted(set(raw.upper())))
    return raw


def answers_equal(mine: str, correct: str):
    """返回 True / False，无法判断时返回 None。"""
    a = normalize_choice(mine)
    b = normalize_choice(correct)
    if not a or not b:
        return None
    return a.upper() == b.upper()


def _find_answer(text: str, pattern) -> Tuple[str, str]:
    m = pattern.search(text)
    if not m:
        return "", text
    value = normalize_choice(m.group(1))
    return value, text[: m.start()] + "\n" + text[m.end():]


def normalize_stem(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    s = re.sub(r"\s+", "", s)
    s = re.sub(
        r"[，。；：、（）【】《》“”‘’！？,.;:()\[\]{}<>\"'!?~`…—\-_/\\|·]+", "", s
    )
    return s.lower()


def stem_hash(text: str) -> str:
    return hashlib.sha256(normalize_stem(text).encode("utf-8")).hexdigest()


def _detect_type(options: List[Dict[str, str]], correct: str) -> str:
    if len(options) == 2:
        tokens = {normalize_choice(o["text"]).lower() for o in options}
        if tokens <= _JUDGE_TOKENS:
            return "judge"
    if options:
        return "multiple" if len(normalize_choice(correct)) > 1 else "single"
    if correct:
        return "fill"
    return "unknown"


def parse_question(raw: str) -> Dict[str, Any]:
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    analysis = ""
    m = _ANALYSIS.search(text)
    if m:
        analysis = m.group(1).strip()[:4000]
        text = text[: m.start()] + "\n" + text[m.end():]

    my_answer, text = _find_answer(text, _ANSWER_MINE)
    correct_answer, text = _find_answer(text, _ANSWER_RIGHT)

    options: List[Dict[str, str]] = []
    stem_lines: List[str] = []
    seen = set()
    for line in text.split("\n"):
        line = line.rstrip()
        m = _OPT_LINE.match(line)
        sequential = bool(m) and (
            not options
            or ord(m.group(1).upper()) == ord(options[0]["key"]) + len(options)
        )
        if sequential:
            key = m.group(1).upper()
            if key in seen:
                stem_lines.append(line)
                continue
            seen.add(key)
            options.append({"key": key, "text": m.group(2).strip()})
        elif options:
            continue  # 选项之后的杂行（解析、标签）丢掉
        else:
            stem_lines.append(line)

    if len(options) < 2:
        options = []

    stem = "\n".join(stem_lines).strip()
    stem = _NUM_PREFIX.sub("", stem).strip()
    stem = _TYPE_PREFIX.sub("", stem).strip()

    return {
        "stem": stem[:2000],
        "options": options,
        "my_answer": my_answer,
        "correct_answer": correct_answer,
        "analysis": analysis,
        "qtype": _detect_type(options, correct_answer),
    }


def looks_like_question(raw: str) -> bool:
    text = raw or ""
    return bool(_OPT_LINE.search(text)) or "答案" in text or "？" in text or "?" in text


# 行首的题号：1. / 2、/ 3) / 4）
_Q_HEAD = re.compile(r"(?m)^[ \t]*(\d{1,3})[ \t]*[.．、)）][ \t]*(?=\S)")
# 作业结果页的表格残留：分值单元格、提交/评阅时间
_SCORE_CELL = re.compile(r"\t\s*\d+(?:\.\d+)?\s*(?=\t|$)")
_META_TAIL = re.compile(
    r"(?:\t|\s{2,})(?:初次提交时间|最后一次提交时间|最后一次修改时间|提交时间|"
    r"得分|评阅状态|批阅状态)\s*[:：]?[^\n]*"
)


def _clean_head_line(chunk: str) -> str:
    """去掉题目首行的「分值 / 提交时间」等表格残留。"""
    head, sep, rest = chunk.partition("\n")
    head = _SCORE_CELL.sub("", head)
    head = _META_TAIL.sub("", head)
    return (head.strip() + sep + rest) if sep else head.strip()


def split_questions(text: str, min_len: int = 25, max_questions: int = 30) -> List[str]:
    """把「一页多题」的文本按题号切成多段。

    只有编号从 1 开始连续递增（1、2、3…）时才认为是分题，
    这样题干/解析里偶尔出现的「1. xxx」不会被误切；
    每段还要有一定长度（min_len），避免把解析里列的两条短句当成两道题。
    切不出两段以上时原样返回一段。
    """
    clean = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not clean:
        return []

    heads = list(_Q_HEAD.finditer(clean))
    if len(heads) < 2:
        return [clean]

    seq = []
    expect = 1
    for m in heads:
        number = int(m.group(1))
        if number == expect:
            seq.append(m)
            expect += 1
    if len(seq) < 2:
        return [clean]

    chunks = []
    for i, m in enumerate(seq):
        end = seq[i + 1].start() if i + 1 < len(seq) else len(clean)
        chunk = clean[m.start():end].strip()
        if len(chunk) >= min_len:
            chunks.append(_clean_head_line(chunk))
    if len(chunks) < 2:
        return [clean]
    return chunks[:max_questions]


def guess_site(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    if not host:
        return ""
    if "chaoxing" in host or "xuexitong" in host:
        return "超星学习通"
    if "zhihuishu" in host:
        return "智慧树"
    if "icve" in host:
        return "智慧职教"
    return host
