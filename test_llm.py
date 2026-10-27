"""验证 AI 结构化链路：用本地假 OpenAI 兼容服务，走完整的 _enrich → _apply_struct。"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ["QUIZBANK_DATA_DIR"] = os.path.join(
    tempfile.mkdtemp(prefix="quizbank_llmtest_"), "data"
)

from starlette.testclient import TestClient

from app import config, llm
from app.main import app

CANNED = {
    "is_question": True,
    "stem": "下列关于 TCP 三次握手的说法，正确的是？",
    "qtype": "single",
    "options": [
        {"key": "A", "text": "第三次握手可以携带数据"},
        {"key": "B", "text": "SYN 报文不消耗序号"},
        {"key": "C", "text": "握手过程需要四次交互"},
        {"key": "D", "text": "服务端先发送 SYN"},
    ],
    "my_answer": "A",
    "correct_answer": "A",
    "analysis": "第三次握手的 ACK 可以携带数据，前两次不行。",
    "subject": "计算机网络",
    "chapter": "传输层",
    "knowledge_points": ["TCP 三次握手", "可靠传输"],
    "difficulty": 3,
}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        content = json.dumps(CANNED, ensure_ascii=False)
        payload = {
            "choices": [{"message": {"role": "assistant", "content": content}}],
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 8799), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

config.LLM_ENABLED = True
config.LLM_BASE_URL = "http://127.0.0.1:8799/v1"
config.LLM_MODEL = "fake-model"
config.LLM_API_KEY = "test"

client = TestClient(app)
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


print("[1] llm.structure 直连假服务")
result = llm.structure("TCP 三次握手 题目原文", "https://example.com", "传输层")
check("返回 dict", isinstance(result, dict), str(type(result)))
check("学科正确", result and result["subject"] == "计算机网络", str(result and result["subject"]))
check("2 个知识点", result and len(result["knowledge_points"]) == 2, str(result and result["knowledge_points"]))
check("difficulty=3", result and result["difficulty"] == 3, str(result and result["difficulty"]))

print("[2] capture → 后台 AI 补全")
raw = """下列关于 TCP 三次握手的说法，正确的是？
A. 第三次握手的数据可以先发
B. SYN 不消耗序号
C. 需要四次交互
D. 服务端先发 SYN
我的答案：A
"""
r = client.post("/api/capture", json={"text": raw, "title": "传输层练习", "url": "https://example.com/q"})
check("capture 200", r.status_code == 200, r.text[:200])
qid = r.json()["question"]["id"]
check("初始 pending", r.json()["question"]["llm_status"] == "pending", r.json()["question"]["llm_status"])

final = None
for _ in range(40):
    time.sleep(0.25)
    g = client.get(f"/api/questions/{qid}")
    if g.json()["llm_status"] in ("ok", "failed", "skipped", "none"):
        final = g.json()
        break

check("AI 完成", final is not None and final["llm_status"] == "ok", str(final and final["llm_status"]))
check("学科写入", final and final["subject"] == "计算机网络", str(final and final["subject"]))
check("章节写入", final and final["chapter"] == "传输层", str(final and final["chapter"]))
kps = [k["name"] for k in (final or {}).get("knowledge_points", [])]
check("知识点写入", sorted(kps) == sorted(["TCP 三次握手", "可靠传输"]), str(kps))
check("解析写入", final and "ACK" in final["analysis"], str(final and final["analysis"])[:80])
check("难度写入", final and final["difficulty"] == 3, str(final and final["difficulty"]))
check("规则答案不被覆盖", final and final["my_answer"] == "A", str(final and final["my_answer"]))
print("       最终:", json.dumps({
    "stem": final["stem"][:24],
    "subject": final["subject"],
    "kps": kps,
    "qtype": final["qtype"],
}, ensure_ascii=False))

print("[3] 非题目文本")
r = client.post("/api/capture", json={"text": "这是一段普通的课程公告，没有任何题目。"})
check("不崩", r.status_code in (200, 400), f"{r.status_code} {r.text[:150]}")
print("       返回:", r.status_code, r.text[:100])

server.shutdown()
print()
print(f"结果：{ok} 通过 / {fail} 失败")
sys.exit(1 if fail else 0)
