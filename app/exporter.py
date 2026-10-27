"""导出：Anki 可导入的 CSV，以及按学科的知识要点 Markdown 提纲。"""
from collections import OrderedDict
from typing import Any, Dict, Iterable, List


def _html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


def _csv_cell(text: str) -> str:
    return '"' + (text or "").replace('"', '""') + '"'


def anki_csv(rows: Iterable[Dict[str, Any]]) -> str:
    lines = []
    for q in rows:
        front = _html(q.get("stem", ""))
        options = q.get("options") or []
        if options:
            front += "<br>" + "<br>".join(
                f"{_html(o.get('key', ''))}. {_html(o.get('text', ''))}" for o in options
            )

        back_parts: List[str] = []
        correct = q.get("correct_answer") or ""
        mine = q.get("my_answer") or ""
        if correct:
            back_parts.append("答案：" + _html(correct))
        if mine and mine != correct:
            back_parts.append("我的答案：" + _html(mine))
        if q.get("analysis"):
            back_parts.append("解析：" + _html(q["analysis"]))
        if q.get("chapter"):
            back_parts.append("章节：" + _html(q["chapter"]))

        tags = []
        if q.get("subject"):
            tags.append(str(q["subject"]).replace(" ", "_"))
        for kp in q.get("knowledge_points") or []:
            name = kp.get("name") if isinstance(kp, dict) else kp
            if name:
                tags.append(str(name).replace(" ", "_"))

        lines.append(
            ",".join([_csv_cell(front), _csv_cell("<br>".join(back_parts)), _csv_cell(" ".join(tags))])
        )
    return "\n".join(lines)


def outline_md(subject: str, rows: Iterable[Dict[str, Any]]) -> str:
    grouped: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for q in rows:
        kps = [kp.get("name") if isinstance(kp, dict) else kp for kp in (q.get("knowledge_points") or [])]
        kps = [k for k in kps if k] or ["未归类"]
        for kp in kps:
            grouped.setdefault(kp, []).append(q)

    out = [f"# {subject or '全部'} · 知识要点", ""]
    out.append(f"共 {sum(len(v) for v in grouped.values())} 条记录，{len(grouped)} 个知识点。")
    out.append("")

    for kp, items in grouped.items():
        out.append(f"## {kp}")
        out.append("")
        for q in items:
            stem = (q.get("stem") or "").replace("\n", " ").strip()
            if len(stem) > 120:
                stem = stem[:120] + "…"
            correct = q.get("correct_answer") or ""
            mine = q.get("my_answer") or ""
            marker = "❌" if (mine and correct and mine != correct) else "•"
            suffix = f"（答案 {correct}）" if correct else ""
            out.append(f"- {marker} {stem}{suffix}")
        out.append("")

    return "\n".join(out)
