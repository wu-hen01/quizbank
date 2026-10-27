"""端到端冒烟测试：不依赖真实服务，直接用 TestClient 打全部核心接口。

用独立的临时数据目录，避免把真实题库改脏。
"""
import json
import os
import sys
import tempfile

os.environ["QUIZBANK_DATA_DIR"] = os.path.join(
    tempfile.mkdtemp(prefix="quizbank_test_"), "data"
)

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SAMPLE = """1.（单选题）下列哪个不是 Python 的内置数据类型？
A. list
B. tuple
C. array
D. dict
我的答案：C
正确答案：C
解析：array 需要 import array 模块，不是内置类型。
"""

SAMPLE2 = """2.（判断题）Python 中字符串是不可变对象。
A. 正确
B. 错误
正确答案：A
"""

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {extra}")


print("[1] health")
r = client.get("/api/health")
check("health 200", r.status_code == 200, r.text[:200])
print("      ", r.json())

print("[2] 首页与静态资源")
r = client.get("/")
check("index 200", r.status_code == 200 and "QuizBank" in r.text, r.status_code)
r = client.get("/static/app.js")
check("app.js 200", r.status_code == 200 and len(r.text) > 1000, r.status_code)

print("[3] capture 单选")
r = client.post("/api/capture", json={
    "text": SAMPLE,
    "url": "https://mooc1.chaoxing.com/mooc-ans/mycourse/stu?x=1",
    "title": "第2章 小测",
})
check("capture 200", r.status_code == 200, r.text[:300])
data = r.json()
q = data["question"]
qid = q["id"]
check("created=True", data["created"] is True, str(data.get("created")))
check("qtype=single", q["qtype"] == "single", str(q["qtype"]))
check("4 个选项", len(q["options"]) == 4, str(q["options"]))
check("我的答案 C", q["my_answer"] == "C", str(q["my_answer"]))
check("正确答案 C", q["correct_answer"] == "C", str(q["correct_answer"]))
check("识别站点", q["source_site"] == "超星学习通", str(q["source_site"]))
check("有解析", bool(q["analysis"]), repr(q["analysis"]))
print("       stem:", q["stem"][:40])

print("[4] capture 判断题")
r = client.post("/api/capture", json={"text": SAMPLE2, "title": "判断练习"})
data2 = r.json()
q2 = data2["question"]
check("判断题 qtype=judge", q2["qtype"] == "judge", str(q2["qtype"]))
check("判断题答案 A", q2["correct_answer"] == "A", str(q2["correct_answer"]))

print("[5] 重复收录去重")
r = client.post("/api/capture", json={"text": SAMPLE, "title": "第2章 小测"})
d3 = r.json()
check("created=False", d3["created"] is False, str(d3.get("created")))
check("seen_count=2", d3["question"]["seen_count"] == 2, str(d3["question"]["seen_count"]))
check("同一 id", d3["question"]["id"] == qid, f"{d3['question']['id']} vs {qid}")

print("[6] 列表 / 搜索 / 过滤")
r = client.get("/api/questions")
check("list 200", r.status_code == 200)
check("共 2 题", r.json()["total"] == 2, str(r.json().get("total")))
r = client.get("/api/questions", params={"q": "内置"})
check("搜索命中 1", r.json()["total"] == 1, str(r.json().get("total")))
r = client.get(f"/api/questions/{qid}")
check("单题 200", r.status_code == 200 and r.json()["id"] == qid)

print("[7] PATCH 编辑")
r = client.patch(f"/api/questions/{qid}", json={
    "subject": "Python 基础",
    "chapter": "数据类型",
    "knowledge_points": ["内置类型", "序列类型"],
})
check("patch 200", r.status_code == 200, r.text[:300])
pq = r.json()
check("学科已存", pq["subject"] == "Python 基础", str(pq["subject"]))
check("2 个知识点", len(pq["knowledge_points"]) == 2, str(pq["knowledge_points"]))

print("[8] 学科 / 知识点")
r = client.get("/api/subjects")
subs = r.json()["items"]
check("subjects 非空", len(subs) >= 1, str(subs))
check("题数正确", sum(s["n"] for s in subs) >= 1, str(subs))
r = client.get("/api/knowledge-points")
kps = r.json()["items"]
check("kps 非空", len(kps) >= 2, str(kps))
print("       kps:", [(k["name"], k["n"]) for k in kps])

print("[9] 复习队列与评分")
r = client.get("/api/review/queue")
queue = r.json()
check("queue 非空", len(queue["items"]) >= 2, str(len(queue.get("items", []))))
rid = queue["items"][0]["id"]
r = client.post(f"/api/review/{rid}", json={"grade": 5})
check("grade 200", r.status_code == 200, r.text[:300])
g = r.json()
check("间隔被拉长", g["interval"] >= 1, str(g))
print("       after grade5:", g)
r = client.post(f"/api/review/{rid}", json={"grade": 0})
check("grade0 重置", r.status_code == 200 and r.json()["interval"] == 1, r.text[:200])

print("[10] 统计")
r = client.get("/api/stats")
st = r.json()
check("stats 200", r.status_code == 200)
check("total 正确", st["total"] == 2, str(st.get("total")))
check("有学科分布", len(st["by_subject"]) >= 1, str(st.get("by_subject")))
check("due 字段存在", "due" in st, str(st))
print("       stats:", json.dumps(st, ensure_ascii=False)[:220])

print("[11] 导出")
r = client.get("/api/export/anki")
check("anki 200", r.status_code == 200)
check("anki 含题干", "内置数据类型" in r.text, r.text[:200].replace("\n", " "))
print("       anki 前两行:", r.text.split("\n")[:2])
r = client.get("/api/export/outline")
check("outline 200", r.status_code == 200 and "#" in r.text, r.status_code)
check("outline 含知识点", "内置类型" in r.text, r.text[:200])

print("[12] reparse（无 AI key 时应安全返回）")
r = client.post(f"/api/questions/{qid}/reparse")
check("reparse 200/4xx 不崩", r.status_code in (200, 400, 409, 503), f"{r.status_code} {r.text[:200]}")

print("[13] 图片收录（OCR）")
r = client.post("/api/capture/image", json={})
check("空请求 400", r.status_code == 400, f"{r.status_code} {r.text[:200]}")
r = client.post("/api/capture/image", json={"image": "data:image/png;base64,bm90LWFuLWltYWdl"})
check("坏图 400", r.status_code == 400, f"{r.status_code} {r.text[:160]}")
r = client.post("/api/capture/image", json={"image_url": "file:///C:/x.png"})
check("非 http 的 image_url 400", r.status_code == 400, f"{r.status_code} {r.text[:160]}")

health = client.get("/api/health").json()
check("health 带 OCR 字段", "ocr_available" in health, str(health))
print("       ocr:", health.get("ocr_available"), health.get("ocr_engine"))

if health.get("ocr_available"):
    # 用一张纯白小图：能跑通 OCR 流程，但没有文字，应该报"没识别出文字"
    png_1x1 = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
        b"\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    import base64 as _b64

    r = client.post(
        "/api/capture/image",
        json={"image": "data:image/png;base64," + _b64.b64encode(png_1x1).decode()},
    )
    check("空白图不崩", r.status_code in (200, 400), f"{r.status_code} {r.text[:200]}")

print("[15] 一页多题拆分")
body = "题干内容" * 12
page = (
    "1.\t" + body + "\n\n作答一" + "x" * 50 + "\n\n"
    "2.\t" + body + "乙\n\n作答二\n\n"
    "3.\t" + body + "丙\n\n作答三"
)
r = client.post("/api/capture", json={"text": page, "url": "http://x.test/a", "whole_page": True})
data = r.json()
check("多题收录 200", r.status_code == 200, r.text[:200])
check("切出 3 道", data.get("count") == 3, str(data.get("count")))
stems = [it["question"]["stem"] for it in data.get("items", [])]
check("三段题干互不相同", len(set(stems)) == 3, str(stems)[:200])
check("题干没留下题号", all(not s.strip().startswith("2.") for s in stems), str(stems)[:200])

r = client.post(
    "/api/capture",
    json={
        "text": "下列关于栈的说法正确的是\nA. 先进先出\nB. 后进先出\n正确答案：B\n解析：\n1. 栈是 LIFO\n2. 队列是 FIFO",
        "url": "http://x.test/b",
    },
)
check("单选文本不拆分", r.status_code == 200 and not r.json().get("count"), r.text[:200])
check("单选文本题干干净", "正确答案" not in r.json()["question"]["stem"], r.json()["question"]["stem"][:80])

print("[16] 模拟测验")
r = client.post("/api/capture", json={
    "text": "（单选题）测验专用：HTTP 协议默认端口是？\nA. 21\nB. 80\nC. 443\nD. 8080\n正确答案：B\n解析：HTTP 默认 80，HTTPS 默认 443。",
    "url": "http://t.test/quiz",
})
single_id = r.json()["question"]["id"]
r = client.post("/api/capture", json={
    "text": "简答：什么是幂等性？\n我的答案：同一个操作执行多次，结果和只执行一次相同。",
    "url": "http://t.test/short",
})
short_q = r.json()["question"]
short_id = short_q["id"]

r = client.get("/api/quiz/pool")
check("pool 200", r.status_code == 200, r.text[:200])
check("pool 有可用题", r.json().get("available", 0) >= 1, r.text[:200])

r = client.post("/api/quiz/start", json={"size": 2})
start = r.json()
check("start 200", r.status_code == 200, r.text[:200])
check("start 抽到 2 题", start.get("count") == 2, str(start.get("count")))
first = (start.get("items") or [{}])[0]
check("题面不含答案", "correct_answer" not in first and "analysis" not in first, str(list(first.keys())))
check("题面带 auto_graded", "auto_graded" in first, str(list(first.keys())))

r = client.post("/api/quiz/start", json={"size": 20, "kind": "objective"})
kinds = {it["qtype"] for it in r.json().get("items", [])}
check("只考客观题时不含简答", kinds <= {"single", "multiple", "judge"}, str(kinds))

r = client.post("/api/quiz/submit", json={"answers": [{"id": single_id, "answer": "B"}], "seconds": 42})
sub = r.json()
item = sub["items"][0]
check("submit 200", r.status_code == 200, r.text[:200])
check("单选答对", item["correct"] is True and item["graded"] is True, str(item))
check("判分返回正确答案", item["correct_answer"] == "B", str(item.get("correct_answer")))
check("判分返回解析", bool(item.get("analysis")), str(item.get("analysis"))[:60])
check("score 记录", sub["score"]["right"] == 1 and sub["score"]["graded"] == 1, str(sub["score"]))
check("seconds 回显", sub["seconds"] == 42, str(sub.get("seconds")))

r = client.post("/api/quiz/submit", json={"answers": [{"id": single_id, "answer": "A"}]})
bad = r.json()
check("单选答错", bad["items"][0]["correct"] is False, str(bad["items"][0]))
check("答错进复习队列", bad["score"]["wrong"] == 1, str(bad["score"]))

r = client.post("/api/quiz/submit", json={"answers": [{"id": q2["id"], "answer": "对"}]})
jt = r.json()["items"][0]
check("判断题 对≈正确", jt["graded"] is True and jt["correct"] is True, str(jt))

r = client.post("/api/quiz/submit", json={"answers": [{"id": q2["id"], "answer": "错误"}]})
jt = r.json()["items"][0]
check("判断题 错误判错", jt["correct"] is False, str(jt))

r = client.post("/api/quiz/submit", json={"answers": [{"id": short_id, "answer": "多次执行结果一致"}]})
st_item = r.json()["items"][0]
check("简答不自动判分", st_item["graded"] is False and st_item["correct"] is None, str(st_item))
check("简答计入待自评", r.json()["score"]["ungraded"] == 1, str(r.json()["score"]))

r = client.post("/api/quiz/mark", json={"items": [{"id": short_id, "correct": True}]})
check("自评 200", r.status_code == 200, r.text[:200])
r = client.post("/api/quiz/mark", json={"items": [{"id": single_id, "correct": False}]})
check("自评跳过客观题也不报错", r.status_code == 200, r.text[:200])

r = client.post("/api/quiz/start", json={"size": 0})
check("size 越界 422", r.status_code == 422, str(r.status_code))

r = client.get("/api/review/queue?limit=50")
check("复习队列有到期题", r.json()["due"] >= 1, str(r.json().get("due")))

print("[17] 删除")
before = client.get("/api/questions").json()["total"]
r = client.delete(f"/api/questions/{q2['id']}")
check("delete 200", r.status_code == 200, r.text[:200])
after = client.get("/api/questions").json()["total"]
check("删后少 1 题", after == before - 1, f"{before} -> {after}")

print()
print(f"结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
