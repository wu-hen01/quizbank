"""模拟测验：判分规则。

放在这里而不是 main.py，是为了能脱离 HTTP 单独测试。
"""
from typing import Optional

from . import parse

# 能自动判分的题型
OBJECTIVE_TYPES = ("single", "multiple", "judge")

# 判断题的等价写法。注意 "×" 与小写 x 指“错”，不要和选项字母 X 混用。
_TRUE_WORDS = {"对", "正确", "√", "✓", "是", "t", "true", "y", "yes", "1"}
_FALSE_WORDS = {"错", "错误", "×", "✗", "x", "否", "f", "false", "n", "no", "0"}


def is_objective(qtype: str) -> bool:
    return (qtype or "") in OBJECTIVE_TYPES


def _judge_bool(value: str) -> Optional[bool]:
    raw = (value or "").strip().lower().replace(" ", "")
    if raw in _TRUE_WORDS:
        return True
    if raw in _FALSE_WORDS:
        return False
    return None


def normalize_answer(qtype: str, value: str) -> str:
    """把前端传来的答案规整成题库里的写法。

    判断题统一成“正确/错误”，选择题去空白去标点、字母排序。
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    if qtype == "judge":
        flag = _judge_bool(raw)
        if flag is True:
            return "正确"
        if flag is False:
            return "错误"
    return parse.normalize_choice(raw)


def _option_flag_map(options) -> dict:
    """从判断题的选项里推 A/B 各代表什么：{"A": True, "B": False}。"""
    table = {}
    for opt in options or []:
        if not isinstance(opt, dict):
            continue
        key = str(opt.get("key") or "").strip().upper()
        text = str(opt.get("text") or "").strip()
        if not key:
            continue
        if text.startswith(("正确", "对", "√", "✓", "是")):
            table[key] = True
        elif text.startswith(("错误", "错", "×", "否")):
            table[key] = False
    return table


def _resolve_judge(value: str, table: dict) -> Optional[bool]:
    flag = _judge_bool(value)
    if flag is not None:
        return flag
    key = (value or "").strip().upper()
    if len(key) == 1 and key in table:
        return table[key]
    return None


def judge(qtype: str, mine: str, correct: str, options=None) -> Optional[bool]:
    """True / False，无法自动判定时返回 None（简答、填空、缺答案）。"""
    if not is_objective(qtype):
        return None
    if not (mine or "").strip() or not (correct or "").strip():
        return None

    if qtype == "judge":
        table = _option_flag_map(options)
        a = _resolve_judge(mine, table)
        b = _resolve_judge(correct, table)
        if a is not None and b is not None:
            return a == b

    return parse.answers_equal(mine, correct)


def pick_sql(
    subject: str = "",
    kp: str = "",
    kind: str = "all",
    prefer: str = "random",
) -> tuple:
    """返回 (SQL 片段, 参数列表)。题目本身的过滤条件，不含 LIMIT。"""
    where = ["1 = 1"]
    params: list = []

    if subject:
        where.append("s.name = ?")
        params.append(subject)
    if kp:
        where.append(
            "EXISTS (SELECT 1 FROM question_kp qk JOIN knowledge_points k"
            " ON k.id = qk.kp_id WHERE qk.question_id = q.id AND k.name = ?)"
        )
        params.append(kp)
    if kind == "objective":
        marks = ", ".join("?" * len(OBJECTIVE_TYPES))
        where.append("q.qtype IN (%s)" % marks)
        params.extend(OBJECTIVE_TYPES)

    if prefer == "wrong":
        order = (
            "COALESCE((SELECT a.is_correct FROM attempts a"
            " WHERE a.question_id = q.id ORDER BY a.id DESC LIMIT 1), 2) ASC, RANDOM()"
        )
    elif prefer == "unseen":
        order = (
            "(SELECT COUNT(*) FROM attempts a WHERE a.question_id = q.id) ASC, RANDOM()"
        )
    else:
        order = "RANDOM()"

    sql = (
        "SELECT q.* FROM questions q LEFT JOIN subjects s ON s.id = q.subject_id"
        " WHERE " + " AND ".join(where) + " ORDER BY " + order
    )
    return sql, params


def pool_sql(subject: str = "", kp: str = "", kind: str = "all") -> tuple:
    """可用题量统计用的 SQL（同 pick_sql 的过滤条件，但不要 ORDER BY）。"""
    sql, params = pick_sql(subject, kp, kind)
    cut = sql.find(" ORDER BY ")
    return sql[:cut] if cut > 0 else sql, params


def score(results: list) -> dict:
    """按已判分的结果算总分。results 是 submit 返回的逐题结果。"""
    total = len(results)
    graded = [r for r in results if r.get("graded")]
    right = [r for r in graded if r.get("correct")]
    accuracy = (len(right) / len(graded)) if graded else 0.0
    return {
        "total": total,
        "graded": len(graded),
        "right": len(right),
        "wrong": len(graded) - len(right),
        "ungraded": total - len(graded),
        "accuracy": round(accuracy, 4),
        "score": round(len(right) / len(graded) * 100) if graded else 0,
    }
