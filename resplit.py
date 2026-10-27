"""把一条「整页被当成一道题」的脏记录，按题号拆回多道题。

用法：
    python resplit.py 9          # 拆第 9 条，原记录删除
    python resplit.py 9 --dry    # 只预览会切出几段，不动数据库

早期版本的扩展在拿不到选区时会退回整页文字，于是整页变成一条记录。
新版已经改成优先用选区，这个脚本用来修历史数据。
"""
import sys
import time

from app import config, parse
from app.db import db, init_db, utcnow
from app.main import _store_capture


def load(conn, qid):
    return conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone()


def main(argv):
    if len(argv) < 2 or not argv[1].isdigit():
        print("用法: python resplit.py <题目id> [--dry]")
        return 2

    qid = int(argv[1])
    dry = "--dry" in argv[2:]

    init_db()
    with db() as conn:
        row = load(conn, qid)
        if not row:
            print("找不到题目 id=%d" % qid)
            return 1
        raw = row["raw_text"] or ""
        url = row["source_url"] or ""
        title = row["source_title"] or ""
        site = row["source_site"] or ""

    chunks = parse.split_questions(raw)
    print("raw_text %d 字 -> %d 段" % (len(raw), len(chunks)))
    for i, chunk in enumerate(chunks):
        first = chunk.split("\n", 1)[0]
        print("  [%d] %d 字  %s" % (i + 1, len(chunk), first[:60]))

    if len(chunks) <= 1:
        print("切不出多段，未做修改。")
        return 1
    if dry:
        print("--dry：未改动数据库。")
        return 0

    # 先删旧记录，否则某一段可能与它自己撞 stem_hash，收回来还是同一条。
    with db() as conn:
        conn.execute("DELETE FROM questions WHERE id = ?", (qid,))

    results = []
    try:
        for chunk in chunks:
            results.append(_store_capture(chunk, url, title, site))
    except Exception:
        # 出错就把原文当一条记录放回去，不丢用户数据
        with db() as conn:
            conn.execute(
                "INSERT INTO questions(stem, stem_hash, qtype, options, my_answer,"
                " correct_answer, analysis, subject_id, chapter, difficulty,"
                " source_url, source_title, source_site, raw_text, image_path,"
                " llm_status, seen_count, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,NULL,'',2,?,?,?,?,'','none',1,?,?)",
                (
                    raw.split("\n", 1)[0][:200],
                    parse.stem_hash(raw.split("\n", 1)[0][:200]),
                    "unknown",
                    "[]",
                    "",
                    "",
                    "",
                    url,
                    title,
                    site,
                    raw,
                    utcnow(),
                    utcnow(),
                ),
            )
        raise

    print("已收录 %d 道题，新 id：%s（原记录 %d 已删除）"
          % (len(results), ", ".join(str(r["id"]) for r in results), qid))

    # 等后台的 AI 结构化跑完，方便立刻看到结果
    if config.LLM_ENABLED:
        print("等待 AI 结构化", end="", flush=True)
        pending = {r["id"] for r in results}
        for _ in range(60):
            with db() as conn:
                rows = conn.execute(
                    "SELECT id, llm_status FROM questions WHERE id IN (%s)"
                    % ",".join("?" * len(pending)),
                    tuple(pending),
                ).fetchall()
            done = {r["id"] for r in rows if r["llm_status"] not in ("pending",)}
            if done >= pending:
                break
            print(".", end="", flush=True)
            time.sleep(1)
        print(" 完成")

    with db() as conn:
        for r in results:
            cur = conn.execute(
                "SELECT stem, llm_status FROM questions WHERE id = ?", (r["id"],)
            ).fetchone()
            if cur:
                print("  #%d [%s] %s" % (r["id"], cur["llm_status"], cur["stem"][:70]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
