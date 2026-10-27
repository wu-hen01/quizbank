"""FastAPI 应用：本地 API + 静态前端。"""
import json
import os
import re
import threading
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, exporter, llm, ocr, parse, quiz, review
from .db import (
    db,
    get_or_create_kp,
    get_or_create_subject,
    init_db,
    utcnow,
)

@asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    """启动时补跑上次没解析完的题（进程被关掉时留下的 pending）。"""
    try:
        n = _resume_pending()
        if n:
            print(f"[QuizBank] 继续解析上次未完成的 {n} 道题")
    except Exception as exc:  # 启动阶段绝不因为补跑失败而起不来
        print(f"[QuizBank] 补跑未完成的解析失败：{exc}")
    yield


app = FastAPI(
    title="QuizBank", version="1.0.0", docs_url="/api/docs", lifespan=_lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- 认证

def auth(request: Request, token: str = "") -> None:
    """配置了 QUIZBANK_TOKEN 才校验；支持 X-Token 头或 ?token= 查询参数。"""
    if not config.TOKEN:
        return
    supplied = request.headers.get("x-token") or token
    if supplied != config.TOKEN:
        raise HTTPException(status_code=401, detail="token 无效")


# ---------------------------------------------------------------- 请求体

class CaptureIn(BaseModel):
    text: str = ""
    url: str = ""
    title: str = ""
    site: str = ""
    whole_page: bool = False   # 扩展没拿到选区、退化成整页时为 True
    selection_len: int = 0     # 扩展拿到的选区长（调试用）


class CaptureImageIn(BaseModel):
    image: str = ""        # dataURL 或裸 base64
    image_url: str = ""    # 或者远程图片地址，由服务端下载
    text: str = ""         # 可选：手动补充/更正的文本，优先于 OCR
    url: str = ""
    title: str = ""
    site: str = ""
    lang: str = ""         # 覆盖 OCR 语言，如 en-US
    keep_image: bool = True


class QuestionPatch(BaseModel):
    stem: Optional[str] = None
    qtype: Optional[str] = None
    options: Optional[List[Dict[str, str]]] = None
    my_answer: Optional[str] = None
    correct_answer: Optional[str] = None
    analysis: Optional[str] = None
    subject: Optional[str] = None
    chapter: Optional[str] = None
    difficulty: Optional[int] = None
    knowledge_points: Optional[List[str]] = None


class GradeIn(BaseModel):
    grade: int = Field(4, ge=0, le=5)


class MergeIn(BaseModel):
    from_id: int
    to_id: int


# ---------------------------------------------------------------- 序列化

def _q_to_dict(conn, row) -> Dict[str, Any]:
    subject_row = None
    if row["subject_id"]:
        subject_row = conn.execute(
            "SELECT name FROM subjects WHERE id = ?", (row["subject_id"],)
        ).fetchone()

    kps = conn.execute(
        "SELECT k.id, k.name FROM knowledge_points k"
        " JOIN question_kp qk ON qk.kp_id = k.id"
        " WHERE qk.question_id = ? ORDER BY k.name",
        (row["id"],),
    ).fetchall()

    rev = conn.execute(
        "SELECT * FROM reviews WHERE question_id = ?", (row["id"],)
    ).fetchone()

    try:
        options = json.loads(row["options"] or "[]")
    except (TypeError, ValueError):
        options = []

    return {
        "id": row["id"],
        "stem": row["stem"],
        "qtype": row["qtype"],
        "options": options,
        "my_answer": row["my_answer"],
        "correct_answer": row["correct_answer"],
        "analysis": row["analysis"],
        "subject": subject_row["name"] if subject_row else "",
        "chapter": row["chapter"],
        "difficulty": row["difficulty"],
        "knowledge_points": [{"id": k["id"], "name": k["name"]} for k in kps],
        "source_url": row["source_url"],
        "source_title": row["source_title"],
        "source_site": row["source_site"],
        "image_path": row["image_path"] or "",
        "image_url": ("/images/" + row["image_path"]) if row["image_path"] else "",
        "llm_status": row["llm_status"],
        "seen_count": row["seen_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "review": (
            {
                "due_at": rev["due_at"],
                "interval": rev["interval"],
                "ease": rev["ease"],
                "reps": rev["reps"],
                "lapses": rev["lapses"],
            }
            if rev
            else None
        ),
    }


def _fetch_question(conn, qid: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="题目不存在")
    return _q_to_dict(conn, row)


# ---------------------------------------------------------------- LLM 增强

def _apply_struct(conn, qid: int, row, obj: Dict[str, Any]) -> None:
    updates: Dict[str, Any] = {}

    stem = (obj.get("stem") or "").strip()
    if len(stem) >= 2:
        new_hash = parse.stem_hash(stem)
        if new_hash == row["stem_hash"]:
            updates["stem"] = stem
        else:
            clash = conn.execute(
                "SELECT id FROM questions WHERE stem_hash = ?", (new_hash,)
            ).fetchone()
            if not clash:
                updates["stem"] = stem
                updates["stem_hash"] = new_hash

    # 规则解析已经拿到的答案不覆盖，AI 只做补全
    if obj.get("qtype") and obj["qtype"] != "unknown":
        updates["qtype"] = obj["qtype"]
    if obj.get("my_answer") and not row["my_answer"]:
        updates["my_answer"] = obj["my_answer"]
    if obj.get("correct_answer") and not row["correct_answer"]:
        updates["correct_answer"] = obj["correct_answer"]
    if obj.get("analysis"):
        updates["analysis"] = obj["analysis"]
    if obj.get("chapter"):
        updates["chapter"] = obj["chapter"]
    if isinstance(obj.get("difficulty"), int):
        updates["difficulty"] = obj["difficulty"]

    options = obj.get("options") or []
    if len(options) >= 2:
        updates["options"] = json.dumps(options, ensure_ascii=False)

    subject_id = row["subject_id"]
    if obj.get("subject"):
        subject_id = get_or_create_subject(conn, obj["subject"])
        updates["subject_id"] = subject_id

    if updates:
        updates["updated_at"] = utcnow()
        sets = ", ".join(f"{key} = ?" for key in updates)
        conn.execute(
            f"UPDATE questions SET {sets} WHERE id = ?",
            list(updates.values()) + [qid],
        )

    names = [str(n).strip() for n in (obj.get("knowledge_points") or []) if str(n).strip()]
    if names:
        ids = [get_or_create_kp(conn, n, subject_id) for n in names[:6]]
        ids = [i for i in ids if i]
        conn.executemany(
            "INSERT OR IGNORE INTO question_kp(question_id, kp_id) VALUES(?, ?)",
            [(qid, kp_id) for kp_id in ids],
        )


def _enrich(qid: int, raw_text: str, url: str, title: str) -> None:
    """后台线程：调用 LLM 补全结构，失败就保留规则解析的结果。"""
    obj = llm.structure(raw_text, url, title)
    with db() as conn:
        row = conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone()
        if not row:
            return
        if not obj:
            conn.execute(
                "UPDATE questions SET llm_status = 'failed', updated_at = ? WHERE id = ?",
                (utcnow(), qid),
            )
            return
        if not obj.get("is_question", True):
            conn.execute(
                "UPDATE questions SET llm_status = 'skipped', updated_at = ? WHERE id = ?",
                (utcnow(), qid),
            )
            return
        try:
            _apply_struct(conn, qid, row, obj)
        except Exception:
            conn.execute(
                "UPDATE questions SET llm_status = 'failed', updated_at = ? WHERE id = ?",
                (utcnow(), qid),
            )
            return
        conn.execute(
            "UPDATE questions SET llm_status = 'ok', updated_at = ? WHERE id = ?",
            (utcnow(), qid),
        )


def _record_attempt(conn, qid: int, mine: str, correct: str) -> None:
    if not mine and not correct:
        return
    equal = parse.answers_equal(mine, correct) if (mine and correct) else None
    conn.execute(
        "INSERT INTO attempts(question_id, answer, is_correct, created_at) VALUES(?, ?, ?, ?)",
        (qid, mine or "", None if equal is None else (1 if equal else 0), utcnow()),
    )


# ---------------------------------------------------------------- 收录

def _store_capture(
    text: str, url: str, title: str, site: str, image_path: str = ""
) -> Dict[str, Any]:
    """收录一段题目文本。调用方负责截断长度。"""
    parsed = parse.parse_question(text)
    stem = parsed["stem"] or text[:200]
    stem_hash = parse.stem_hash(stem)
    site = site or parse.guess_site(url)
    now = utcnow()
    created = False

    with db() as conn:
        row = conn.execute(
            "SELECT * FROM questions WHERE stem_hash = ?", (stem_hash,)
        ).fetchone()

        if row:
            qid = row["id"]
            conn.execute(
                "UPDATE questions SET seen_count = seen_count + 1, updated_at = ? WHERE id = ?",
                (now, qid),
            )
            if not row["my_answer"] and parsed["my_answer"]:
                conn.execute(
                    "UPDATE questions SET my_answer = ? WHERE id = ?",
                    (parsed["my_answer"], qid),
                )
            if not row["correct_answer"] and parsed["correct_answer"]:
                conn.execute(
                    "UPDATE questions SET correct_answer = ? WHERE id = ?",
                    (parsed["correct_answer"], qid),
                )
            if row["llm_status"] in ("failed", "none"):
                conn.execute(
                    "UPDATE questions SET llm_status = ? WHERE id = ?",
                    ("pending" if config.LLM_ENABLED else row["llm_status"], qid),
                )
            if not row["analysis"] and parsed.get("analysis"):
                conn.execute(
                    "UPDATE questions SET analysis = ? WHERE id = ?",
                    (parsed["analysis"], qid),
                )
            if image_path and not (row["image_path"] or ""):
                conn.execute(
                    "UPDATE questions SET image_path = ? WHERE id = ?",
                    (image_path, qid),
                )
        else:
            cur = conn.execute(
                """INSERT INTO questions
                   (stem, stem_hash, qtype, options, my_answer, correct_answer, analysis,
                    subject_id, chapter, difficulty, source_url, source_title, source_site,
                    raw_text, image_path, llm_status, seen_count, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    stem,
                    stem_hash,
                    parsed["qtype"],
                    json.dumps(parsed["options"], ensure_ascii=False),
                    parsed["my_answer"],
                    parsed["correct_answer"],
                    parsed.get("analysis") or "",
                    None,
                    "",
                    2,
                    url,
                    title,
                    site,
                    text,
                    image_path,
                    "pending" if config.LLM_ENABLED else "none",
                    1,
                    now,
                    now,
                ),
            )
            qid = cur.lastrowid
            created = True
            conn.execute(
                "INSERT OR IGNORE INTO reviews"
                "(question_id, due_at, interval, ease, reps, lapses, updated_at)"
                " VALUES (?, ?, 0, 2.5, 0, 0, ?)",
                (qid, now, now),
            )

        _record_attempt(conn, qid, parsed["my_answer"], parsed["correct_answer"])
        question = _fetch_question(conn, qid)

    if config.LLM_ENABLED and question["llm_status"] == "pending":
        threading.Thread(
            target=_enrich, args=(qid, text, url, title), daemon=True
        ).start()

    return {"created": created, "id": qid, "question": question}


@app.post("/api/capture")
def capture(body: CaptureIn, _=Depends(auth)):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="没有收到文本，请先选中题目再右键")
    text = text[:20000]

    chunks = parse.split_questions(text)
    if len(chunks) <= 1:
        return _store_capture(text, body.url, body.title, body.site)

    results = [_store_capture(chunk, body.url, body.title, body.site) for chunk in chunks]
    first = results[0]
    return {
        "created": any(r["created"] for r in results),
        "count": len(results),
        "id": first["id"],
        "question": first["question"],
        "items": results,
    }


# ---------------------------------------------------------------- 图片收录

def _download_image(url: str) -> Tuple[bytes, str]:
    """服务端下载远程图片（扩展的 canvas 方案失败时的兜底）。"""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        raise HTTPException(status_code=400, detail="image_url 必须是 http(s) 地址")
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            resp = client.get(url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail="下载图片失败: %s" % exc)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=400, detail="下载图片失败: HTTP %d" % resp.status_code
        )
    if len(resp.content) > ocr._MAX_BYTES:
        raise HTTPException(status_code=400, detail="图片超过 12 MB")
    if not resp.content:
        raise HTTPException(status_code=400, detail="图片内容为空")
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/bmp": ".bmp",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/tiff": ".tif",
    }.get(ctype, "")
    if not ext:
        ext = os.path.splitext(urlparse(url).path)[1].lower() or ".png"
    return resp.content, ext


@app.post("/api/capture/image")
def capture_image(body: CaptureImageIn, _=Depends(auth)):
    """截图/图片收录：OCR 出文字后走和文本收录一样的流程。"""
    if body.image:
        try:
            data, ext = ocr.decode_image(body.image)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    elif body.image_url:
        data, ext = _download_image(body.image_url)
    else:
        raise HTTPException(
            status_code=400, detail="请提供 image（dataURL/base64）或 image_url"
        )

    ok, reason = ocr.available()
    ocr_text, ocr_error = "", ""
    if ok:
        ocr_text, ocr_error = ocr.recognize_bytes(data, ext, body.lang)
    else:
        ocr_error = reason

    text = (body.text or "").strip() or ocr_text
    if not text:
        raise HTTPException(
            status_code=400,
            detail="OCR 没识别出文字（%s），可以选中文本再收录" % (ocr_error or "图片里没有文字"),
        )

    image_path = ""
    if body.keep_image:
        try:
            image_path, _full = ocr.save_image(data, ext)
        except Exception:  # 存图失败不影响收录
            image_path = ""

    result = _store_capture(text[:20000], body.url, body.title, body.site, image_path)
    result["ocr_text"] = ocr_text
    result["ocr_error"] = ocr_error
    return result


# ---------------------------------------------------------------- 题目

@app.get("/api/questions")
def list_questions(
    q: str = "",
    subject: str = "",
    kp: str = "",
    status: str = "",
    limit: int = 200,
    offset: int = 0,
    _=Depends(auth),
):
    sql = """SELECT q.* FROM questions q
             LEFT JOIN subjects s ON s.id = q.subject_id
             WHERE 1 = 1"""
    params: List[Any] = []
    if q:
        sql += " AND (q.stem LIKE ? OR q.analysis LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if subject:
        sql += " AND s.name = ?"
        params.append(subject)
    if kp:
        sql += (
            " AND q.id IN (SELECT qk.question_id FROM question_kp qk"
            " JOIN knowledge_points k ON k.id = qk.kp_id WHERE k.name = ?)"
        )
        params.append(kp)
    if status:
        sql += " AND q.llm_status = ?"
        params.append(status)
    sql += " ORDER BY q.id DESC LIMIT ? OFFSET ?"
    params += [max(1, min(limit, 1000)), max(0, offset)]

    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
        return {"total": len(rows), "items": [_q_to_dict(conn, r) for r in rows]}


@app.get("/api/questions/{qid}")
def get_question(qid: int, _=Depends(auth)):
    with db() as conn:
        return _fetch_question(conn, qid)


@app.patch("/api/questions/{qid}")
def update_question(qid: int, body: QuestionPatch, _=Depends(auth)):
    updates: Dict[str, Any] = {}

    if body.stem is not None and body.stem.strip():
        updates["stem"] = body.stem.strip()[:3000]
        updates["stem_hash"] = parse.stem_hash(updates["stem"])
    if body.qtype is not None:
        updates["qtype"] = body.qtype
    if body.options is not None:
        updates["options"] = json.dumps(body.options, ensure_ascii=False)
    for field in ("my_answer", "correct_answer", "analysis", "chapter"):
        value = getattr(body, field)
        if value is not None:
            updates[field] = value
    if body.difficulty is not None:
        updates["difficulty"] = max(1, min(5, int(body.difficulty)))

    try:
        with db() as conn:
            row = conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="题目不存在")

            if body.subject is not None:
                updates["subject_id"] = get_or_create_subject(conn, body.subject)

            if updates:
                updates["updated_at"] = utcnow()
                sets = ", ".join(f"{key} = ?" for key in updates)
                conn.execute(
                    f"UPDATE questions SET {sets} WHERE id = ?",
                    list(updates.values()) + [qid],
                )

            if body.knowledge_points is not None:
                conn.execute("DELETE FROM question_kp WHERE question_id = ?", (qid,))
                final_row = conn.execute(
                    "SELECT subject_id FROM questions WHERE id = ?", (qid,)
                ).fetchone()
                for name in body.knowledge_points:
                    kp_id = get_or_create_kp(conn, name, final_row["subject_id"])
                    if kp_id:
                        conn.execute(
                            "INSERT OR IGNORE INTO question_kp(question_id, kp_id) VALUES(?, ?)",
                            (qid, kp_id),
                        )

            return _fetch_question(conn, qid)
    except HTTPException:
        raise
    except Exception as exc:  # 大多是改题干后撞了去重哈希
        raise HTTPException(status_code=409, detail=f"保存失败：{exc}")


@app.delete("/api/questions/{qid}")
def delete_question(qid: int, _=Depends(auth)):
    with db() as conn:
        conn.execute("DELETE FROM questions WHERE id = ?", (qid,))
    return {"ok": True}


@app.post("/api/questions/{qid}/reparse")
def reparse_question(qid: int, _=Depends(auth)):
    if not config.LLM_ENABLED:
        raise HTTPException(status_code=400, detail="未配置 LLM_API_KEY")
    with db() as conn:
        row = conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="题目不存在")
        conn.execute(
            "UPDATE questions SET llm_status = 'pending' WHERE id = ?", (qid,)
        )
        raw_text = row["raw_text"] or row["stem"]
        url, title = row["source_url"], row["source_title"]

    threading.Thread(
        target=_enrich, args=(qid, raw_text, url, title), daemon=True
    ).start()
    return {"ok": True, "status": "pending"}


# ---------------------------------------------------------------- 学科 / 知识点

@app.get("/api/subjects")
def list_subjects(_=Depends(auth)):
    with db() as conn:
        rows = conn.execute(
            """SELECT s.id, s.name, COUNT(q.id) AS n
               FROM subjects s LEFT JOIN questions q ON q.subject_id = s.id
               GROUP BY s.id ORDER BY n DESC, s.name"""
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.get("/api/knowledge-points")
def list_kps(subject: str = "", _=Depends(auth)):
    sql = """SELECT k.id, k.name, COALESCE(s.name, '') AS subject,
                    COUNT(qk.question_id) AS n
             FROM knowledge_points k
             LEFT JOIN subjects s ON s.id = k.subject_id
             LEFT JOIN question_kp qk ON qk.kp_id = k.id
             WHERE 1 = 1"""
    params: List[Any] = []
    if subject:
        sql += " AND s.name = ?"
        params.append(subject)
    sql += " GROUP BY k.id HAVING n > 0 ORDER BY n DESC, k.name"
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.post("/api/knowledge-points/merge")
def merge_kps(body: MergeIn, _=Depends(auth)):
    if body.from_id == body.to_id:
        raise HTTPException(status_code=400, detail="不能合并到自己")
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO question_kp(question_id, kp_id)"
            " SELECT question_id, ? FROM question_kp WHERE kp_id = ?",
            (body.to_id, body.from_id),
        )
        conn.execute("DELETE FROM question_kp WHERE kp_id = ?", (body.from_id,))
        conn.execute("DELETE FROM knowledge_points WHERE id = ?", (body.from_id,))
    return {"ok": True}


# ---------------------------------------------------------------- 复习

@app.get("/api/review/queue")
def review_queue(limit: int = 30, _=Depends(auth)):
    with db() as conn:
        rows = conn.execute(
            """SELECT q.* FROM questions q JOIN reviews r ON r.question_id = q.id
               WHERE r.due_at <= ? ORDER BY r.due_at ASC, q.id ASC LIMIT ?""",
            (utcnow(), max(1, min(limit, 200))),
        ).fetchall()
        items = [_q_to_dict(conn, r) for r in rows]
        due = conn.execute(
            "SELECT COUNT(*) AS n FROM reviews WHERE due_at <= ?", (utcnow(),)
        ).fetchone()["n"]
    return {"due": due, "items": items}


@app.post("/api/review/{qid}")
def grade_question(qid: int, body: GradeIn, _=Depends(auth)):
    with db() as conn:
        rev = conn.execute(
            "SELECT * FROM reviews WHERE question_id = ?", (qid,)
        ).fetchone()
        if not rev:
            raise HTTPException(status_code=404, detail="这道题还没有复习记录")

        due_at, interval, ease, reps = review.schedule(
            rev["ease"], rev["interval"], rev["reps"], body.grade
        )
        lapses = rev["lapses"] + (1 if body.grade < 3 else 0)
        conn.execute(
            """UPDATE reviews SET due_at = ?, interval = ?, ease = ?, reps = ?,
               lapses = ?, last_grade = ?, updated_at = ? WHERE question_id = ?""",
            (due_at, interval, ease, reps, lapses, body.grade, utcnow(), qid),
        )
    return {
        "due_at": due_at,
        "interval": interval,
        "ease": ease,
        "reps": reps,
        "label": review.describe(interval),
    }


# ---------------------------------------------------------------- 模拟测验

class QuizStartIn(BaseModel):
    subject: str = ""
    kp: str = ""
    size: int = Field(10, ge=1, le=50)
    kind: str = "all"       # all | objective
    prefer: str = "random"  # random | wrong | unseen


class QuizAnswerIn(BaseModel):
    id: int
    answer: str = ""


class QuizSubmitIn(BaseModel):
    answers: List[QuizAnswerIn] = []
    seconds: int = 0


class QuizMarkItem(BaseModel):
    id: int
    correct: bool


class QuizMarkIn(BaseModel):
    items: List[QuizMarkItem] = []


def _quiz_item(conn, row) -> Dict[str, Any]:
    """发给前端的题面：不含正确答案与解析，避免边做边看。"""
    q = _q_to_dict(conn, row)
    return {
        "id": q["id"],
        "stem": q["stem"],
        "qtype": q["qtype"],
        "options": q["options"],
        "image_url": q["image_url"],
        "subject": q["subject"],
        "chapter": q["chapter"],
        "knowledge_points": [k["name"] for k in q["knowledge_points"]],
        "auto_graded": quiz.is_objective(q["qtype"]) and bool(q["correct_answer"]),
    }


def _apply_quiz_grade(conn, qid: int, correct: Optional[bool]) -> None:
    """测验判完，把结果折进复习调度：错题立刻进队列，对题往后推。"""
    if correct is None:
        return
    rev = conn.execute(
        "SELECT * FROM reviews WHERE question_id = ?", (qid,)
    ).fetchone()
    if correct:
        base = rev or {"ease": 2.5, "interval": 0, "reps": 0, "lapses": 0}
        due_at, interval, ease, reps = review.schedule(
            base["ease"], base["interval"], base["reps"], 4
        )
        lapses = base["lapses"]
    else:
        base = rev or {"ease": 2.5, "interval": 0, "reps": 0, "lapses": 0}
        due_at, interval, ease, reps = review.schedule(
            base["ease"], base["interval"], base["reps"], 1
        )
        due_at = utcnow()  # 错题今天就该复习
        lapses = base["lapses"] + 1

    if rev:
        conn.execute(
            """UPDATE reviews SET due_at = ?, interval = ?, ease = ?, reps = ?,
               lapses = ?, last_grade = ?, updated_at = ? WHERE question_id = ?""",
            (
                due_at, interval, ease, reps, lapses,
                4 if correct else 1, utcnow(), qid,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO reviews(question_id, due_at, interval, ease, reps,
               lapses, last_grade, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                qid, due_at, interval, ease, reps, lapses,
                4 if correct else 1, utcnow(),
            ),
        )


@app.get("/api/quiz/pool")
def quiz_pool(subject: str = "", kp: str = "", kind: str = "all", _=Depends(auth)):
    sql, params = quiz.pool_sql(subject, kp, kind)
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM (" + sql + ")", params).fetchone()["n"]
    return {"available": n}


@app.post("/api/quiz/start")
def quiz_start(body: QuizStartIn, _=Depends(auth)):
    sql, params = quiz.pick_sql(body.subject, body.kp, body.kind, body.prefer)
    with db() as conn:
        rows = conn.execute(sql + " LIMIT ?", params + [body.size]).fetchall()
        items = [_quiz_item(conn, r) for r in rows]
    return {"count": len(items), "items": items}


@app.post("/api/quiz/submit")
def quiz_submit(body: QuizSubmitIn, _=Depends(auth)):
    seen = set()
    results: List[Dict[str, Any]] = []
    with db() as conn:
        for ans in body.answers:
            if ans.id in seen:
                continue
            seen.add(ans.id)
            row = conn.execute(
                "SELECT * FROM questions WHERE id = ?", (ans.id,)
            ).fetchone()
            if not row:
                continue

            qtype = row["qtype"]
            mine = quiz.normalize_answer(qtype, ans.answer)
            correct = row["correct_answer"]
            try:
                options = json.loads(row["options"] or "[]")
            except (TypeError, ValueError):
                options = []
            ok = quiz.judge(qtype, mine, correct, options)

            conn.execute(
                "INSERT INTO attempts(question_id, answer, is_correct, created_at)"
                " VALUES(?, ?, ?, ?)",
                (ans.id, mine, None if ok is None else (1 if ok else 0), utcnow()),
            )
            _apply_quiz_grade(conn, ans.id, ok)

            q = _q_to_dict(conn, row)
            results.append(
                {
                    "id": q["id"],
                    "stem": q["stem"],
                    "qtype": qtype,
                    "options": q["options"],
                    "image_url": q["image_url"],
                    "my_answer": mine,
                    "correct_answer": correct,
                    "analysis": q["analysis"],
                    "graded": ok is not None,
                    "correct": ok,
                }
            )

    return {
        "score": quiz.score(results),
        "items": results,
        "seconds": max(0, int(body.seconds or 0)),
    }


@app.post("/api/quiz/mark")
def quiz_mark(body: QuizMarkIn, _=Depends(auth)):
    """简答/填空题的自评：把“对/错”补进去。"""
    with db() as conn:
        for item in body.items:
            row = conn.execute(
                "SELECT qtype FROM questions WHERE id = ?", (item.id,)
            ).fetchone()
            if not row:
                continue
            if quiz.is_objective(row["qtype"]):
                continue
            conn.execute(
                "UPDATE attempts SET is_correct = ?"
                " WHERE id = (SELECT MAX(id) FROM attempts WHERE question_id = ?)",
                (1 if item.correct else 0, item.id),
            )
            _apply_quiz_grade(conn, item.id, item.correct)
    return {"ok": True}


# ---------------------------------------------------------------- 统计

@app.get("/api/stats")
def stats(_=Depends(auth)):
    now = utcnow()
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM questions").fetchone()["n"]
        subjects = conn.execute("SELECT COUNT(*) AS n FROM subjects").fetchone()["n"]
        kps = conn.execute("SELECT COUNT(*) AS n FROM knowledge_points").fetchone()["n"]
        due = conn.execute(
            "SELECT COUNT(*) AS n FROM reviews WHERE due_at <= ?", (now,)
        ).fetchone()["n"]
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM questions WHERE llm_status = 'pending'"
        ).fetchone()["n"]
        attempt_row = conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(is_correct), 0) AS c"
            " FROM attempts WHERE is_correct IS NOT NULL"
        ).fetchone()
        by_subject = conn.execute(
            """SELECT s.name, COUNT(q.id) AS n FROM subjects s
               LEFT JOIN questions q ON q.subject_id = s.id
               GROUP BY s.id ORDER BY n DESC, s.name"""
        ).fetchall()

    answered = attempt_row["n"] or 0
    correct = attempt_row["c"] or 0
    return {
        "total": total,
        "subjects": subjects,
        "knowledge_points": kps,
        "due": due,
        "pending": pending,
        "answered": answered,
        "correct": correct,
        "accuracy": round(correct / answered * 100, 1) if answered else None,
        "by_subject": [dict(r) for r in by_subject],
    }


# ---------------------------------------------------------------- 导出

@app.get("/api/export/anki")
def export_anki(subject: str = "", _=Depends(auth)):
    sql = """SELECT q.* FROM questions q LEFT JOIN subjects s ON s.id = q.subject_id WHERE 1 = 1"""
    params: List[Any] = []
    if subject:
        sql += " AND s.name = ?"
        params.append(subject)
    sql += " ORDER BY q.id ASC"
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
        questions = [_q_to_dict(conn, r) for r in rows]

    csv_text = exporter.anki_csv(questions)
    return PlainTextResponse(
        csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="quizbank.csv"'},
    )


@app.get("/api/export/outline")
def export_outline(subject: str = "", _=Depends(auth)):
    sql = """SELECT q.* FROM questions q LEFT JOIN subjects s ON s.id = q.subject_id WHERE 1 = 1"""
    params: List[Any] = []
    if subject:
        sql += " AND s.name = ?"
        params.append(subject)
    sql += " ORDER BY q.id ASC"
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
        questions = [_q_to_dict(conn, r) for r in rows]

    md = exporter.outline_md(subject or "全部", questions)
    return PlainTextResponse(
        md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="outline.md"'},
    )


# ---------------------------------------------------------------- 其它

@app.get("/api/health")
def health():
    ocr_ok, ocr_reason = ocr.available()
    return {
        "ok": True,
        "llm_enabled": config.LLM_ENABLED,
        "model": config.LLM_MODEL if config.LLM_ENABLED else None,
        "token_required": bool(config.TOKEN),
        "ocr_enabled": bool(config.OCR_ENABLED),
        "ocr_available": ocr_ok,
        "ocr_engine": ocr_reason,
        "ocr_lang": config.OCR_LANG,
        "db": str(config.DB_PATH),
    }


@app.get("/favicon.ico")
def favicon():
    return PlainTextResponse("", status_code=204)


def _resume_pending() -> int:
    """把上次没解析完的题（进程被关掉时留下的 pending）重新排上。

    不这么做的话，解析中遇到服务重启，那些题会永远停在「解析中…」。
    """
    if not config.LLM_ENABLED:
        return 0
    with db() as conn:
        rows = conn.execute(
            "SELECT id, raw_text, source_url, source_title FROM questions"
            " WHERE llm_status = 'pending' ORDER BY id LIMIT 20"
        ).fetchall()
    for row in rows:
        threading.Thread(
            target=_enrich,
            args=(
                row["id"],
                row["raw_text"] or "",
                row["source_url"] or "",
                row["source_title"] or "",
            ),
            daemon=True,
        ).start()
    return len(rows)


init_db()
config.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
app.mount("/images", StaticFiles(directory=str(config.IMAGE_DIR)), name="images")


@app.get("/")
def index():
    return FileResponse(str(config.STATIC_DIR / "index.html"))
