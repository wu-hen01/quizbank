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
# 测试不打真实的 LLM 接口：显式置空，_load_dotenv 不会再用 .env 里的真 key 覆盖
os.environ["LLM_API_KEY"] = ""
os.environ.pop("QUIZBANK_CORS", None)

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

print("[14] 题干配图：文字 + 多张图一起收录")
import base64 as _b64

_png = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _img(suffix=b""):
    return "data:image/png;base64," + _b64.b64encode(_png + suffix).decode()


text_img = (
    "如图所示，一棵二叉搜索树的中序遍历结果是递增序列，下列说法正确的是？\n"
    "A. 左子树上所有结点值均小于根\n"
    "B. 右子树上所有结点值均大于根\n"
    "C. 中序遍历可以得到有序序列\n"
    "D. 以上都正确\n"
    "正确答案：D"
)
r = client.post(
    "/api/capture",
    json={"text": text_img, "images": [_img(), _img(b"1")], "url": "http://x.test/img"},
)
data = r.json()
check("带图收录 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
check("服务端存了 2 张图", data.get("images") == 2, str(data.get("images")))
urls = (data.get("question") or {}).get("image_urls") or []
check("题面带 2 个 image_urls", len(urls) == 2, str(urls))
check("image_url 回落成第一张", (data.get("question") or {}).get("image_url") == (urls[0] if urls else None), str(urls))
check("图片能访问", all(client.get(u).status_code == 200 for u in urls), str(urls))
check("选项与答案没被图破坏", (data.get("question") or {}).get("correct_answer") == "D", str((data.get("question") or {}).get("correct_answer")))

r = client.post(
    "/api/capture",
    json={"text": text_img, "images": [_img(b"2")], "url": "http://x.test/img"},
)
data = r.json()
urls2 = (data.get("question") or {}).get("image_urls") or []
check("重复收录时追加图片", len(urls2) == 3, str(urls2))
check("重复收录不新建题", data.get("created") is False, str(data.get("created")))

r = client.post(
    "/api/capture",
    json={"text": "坏图不影响文字：另一道题的题干内容在这里\nA. 对\nB. 错\n正确答案：A", "images": ["data:image/png;base64,@@not-base64@@"]},
)
check("坏图不阻塞收录", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
check("坏图被跳过", (r.json().get("images") or 0) == 0, str(r.json().get("images")))
check("坏图有原因", bool(r.json().get("image_errors")), str(r.json().get("image_errors")))

r = client.post("/api/capture", json={"images": [_img()]})
check("只有图没有文字时给 400 且说明 OCR", r.status_code == 400 and "OCR" in r.text, f"{r.status_code} {r.text[:160]}")

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

print("[17] 安全加固")
import app.config as cfg_mod
import app.main as main_mod

r = client.get("/api/health", headers={"Origin": "http://evil.example"})
check(
    "陌生来源没有 CORS 放行",
    "access-control-allow-origin" not in {k.lower() for k in r.headers},
    str(r.headers.get("access-control-allow-origin")),
)
r = client.get("/api/health", headers={"Origin": "http://127.0.0.1:8765"})
check(
    "本机页面有 CORS 放行",
    r.headers.get("access-control-allow-origin") == "http://127.0.0.1:8765",
    str(r.headers.get("access-control-allow-origin")),
)
r = client.get(
    "/api/health",
    headers={"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"},
)
check(
    "浏览器扩展来源有 CORS 放行",
    bool(r.headers.get("access-control-allow-origin")),
    str(r.headers.get("access-control-allow-origin")),
)
r = client.options(
    "/api/capture",
    headers={
        "Origin": "http://evil.example",
        "Access-Control-Request-Method": "POST",
    },
)
check(
    "陌生来源预检不通",
    "access-control-allow-origin" not in {k.lower() for k in r.headers},
    str(r.status_code),
)

check("127.0.0.1 不算公网", not main_mod._is_public_url("http://127.0.0.1:8765/x.png"))
check("localhost 不算公网", not main_mod._is_public_url("http://localhost/x.png"))
check(
    "云元数据地址不算公网",
    not main_mod._is_public_url("http://169.254.169.254/latest/meta-data/"),
)
check("内网段不算公网", not main_mod._is_public_url("http://192.168.1.10/x.png"))
check("file 协议被拒", not main_mod._is_public_url("file:///C:/x.png"))
check("公网地址放行", main_mod._is_public_url("http://8.8.8.8/x.png"))

check(
    "PNG 文件头能认出来",
    main_mod._sniff_image_ext(b"\x89PNG\r\n\x1a\n" + b"0" * 16) == ".png",
)
check(
    "HTML 伪装成图片会被识破",
    main_mod._sniff_image_ext(b"<html><body>not an image</body></html>") == "",
)

r = client.post(
    "/api/capture",
    json={
        "text": "1. 安全测试：本机图片地址\nA. 甲\nB. 乙\n正确答案：A",
        "images": ["http://127.0.0.1:8765/api/health"],
    },
)
body = r.json()
check("本机图片地址存不下来", r.status_code == 200 and body.get("images") == 0, r.text[:200])
check(
    "拒绝原因是公网限制",
    any("公网" in e for e in (body.get("image_errors") or [])),
    str(body.get("image_errors")),
)
r = client.post(
    "/api/capture",
    json={
        "text": "2. 安全测试：云元数据地址\nA. 甲\nB. 乙\n正确答案：B",
        "images": ["http://169.254.169.254/latest/meta-data/"],
    },
)
check("云元数据地址存不下来", r.json().get("images") == 0, r.text[:120])

h = client.get("/api/health").json()
check(
    "health 不暴露本机绝对路径",
    "\\" not in h.get("db", "") and "/" not in h.get("db", ""),
    str(h.get("db")),
)

old_token = cfg_mod.TOKEN
try:
    cfg_mod.TOKEN = "s3cret-token"
    r = client.get("/api/questions")
    check("开了 token 后没凭据 401", r.status_code == 401, str(r.status_code))
    r = client.get("/api/questions", headers={"X-Token": "wrong"})
    check("错误 token 401", r.status_code == 401, str(r.status_code))
    r = client.get("/api/questions", headers={"X-Token": "s3cret-token"})
    check("正确 token 放行", r.status_code == 200, str(r.status_code))
finally:
    cfg_mod.TOKEN = old_token

print("[18] 删除")
before = client.get("/api/questions").json()["total"]
r = client.delete(f"/api/questions/{q2['id']}")
check("delete 200", r.status_code == 200, r.text[:200])
after = client.get("/api/questions").json()["total"]
check("删后少 1 题", after == before - 1, f"{before} -> {after}")

print()
print(f"结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
