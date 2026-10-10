"""创建一个测试知识库并验证真实检索链路（HTTP → celery worker → embedding → Milvus → search）。

跟 seed_test_expert.py 一样的定位：给功能验收用的种子数据。不同的是这里全走
HTTP（/kb/create, /kb/{id}/upload, /kb/search），因为知识库这条链路的价值就在于
"文档真的被切片、embedding、能被搜到"——插一行 DB 记录测不出这个。

用法（在能连到 lea-rag 的容器网络里跑，例如 docker exec -i lea-rag-scale python3 ...，
或者本机能直连 RAG_BASE 时直接跑）：
    python scripts/seed_test_kb.py
"""
import json
import time
import urllib.request
import urllib.error

RAG_BASE = "http://localhost:8300"
INTERNAL_TOKEN = "local-dev-internal-token"
TENANT_ID = "test2"
OWNER = "luhaoyang"

# 塞进文档里的不可伪造 marker，search 之后断言原样能搜到。
MARKER = "TESTKB-MARKER-7QX2"
DOC_CONTENT = f"""测试知识库验收文档

本文档用于验证知识库检索链路是否真实可用（上传 → 解析 → 切片 → embedding → Milvus 索引 → 检索命中）。

关键事实：内部代号「青鸾计划」的负责人是林奚，该计划于 2026 年第三季度启动，
目标是把知识库检索延迟压到 200 毫秒以内。

验收标记（检索结果里必须原样出现，不得省略、修改或翻译）：【{MARKER}】
"""


def _req(method: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(RAG_BASE + path, data=data, method=method)
    req.add_header("X-Internal-Token", INTERNAL_TOKEN)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")


def _upload(kb_id: int) -> dict:
    boundary = "----leakbseedboundary"
    filename = "kb_seed_test.txt"
    content = DOC_CONTENT.encode("utf-8")
    parts = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: text/plain\r\n\r\n".encode()
    )
    parts.append(content)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(f'Content-Disposition: form-data; name="tenant_id"\r\n\r\n{TENANT_ID}'.encode())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        f"{RAG_BASE}/kb/{kb_id}/upload", data=body, method="POST"
    )
    req.add_header("X-Internal-Token", INTERNAL_TOKEN)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"upload -> HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")


def main():
    print("1) 创建知识库 ...")
    kb = _req("POST", "/kb/create", {
        "name": "测试知识库",
        "description": "用于知识库上传/检索功能验收的测试 KB",
        "owner": OWNER,
        "tenant_id": TENANT_ID,
        "visibility": "shared",
        "retrieval_mode": "hybrid",
        "retrieval_top_k": 5,
        "score_threshold": 0.3,
    })
    kb_id = kb["id"]
    print(f"   OK: kb_id={kb_id}, name={kb['name']}")

    print("2) 上传测试文档 ...")
    doc = _upload(kb_id)
    doc_id = doc["id"]
    print(f"   OK: doc_id={doc_id}, status={doc['status']}")

    print("3) 等待 celery worker 处理（parse/chunk/embed）...")
    status = doc["status"]
    for i in range(30):
        time.sleep(2)
        got = _req("GET", f"/kb/{kb_id}/documents/{doc_id}")
        status = got["status"]
        print(f"   [{i*2:>3}s] status={status} chunk_count={got.get('chunk_count', 0)}")
        if status in ("completed", "done", "success", "ready", "error", "failed"):
            break
    if status not in ("completed", "done", "success", "ready"):
        raise SystemExit(f"FAIL: 文档处理未成功，最终状态={status}, detail={got}")

    print("4) 检索验证（必须搜到 marker）...")
    hits = _req("POST", "/kb/search", {
        "query": "青鸾计划的负责人是谁",
        "top_k": 5,
        "kb_ids": [kb_id],
        "tenant_id": TENANT_ID,
        "user_id": OWNER,
        "strategy": "hybrid",
    })
    print(f"   命中 {len(hits)} 条")
    for h in hits:
        print(f"   score={h['score']:.4f} content={h['content'][:80]!r}")

    found = any(MARKER in h["content"] for h in hits)
    if not found:
        raise SystemExit(f"FAIL: 检索结果里没有找到 marker 【{MARKER}】—— 链路没真正跑通")

    print(f"\nPASS: kb_id={kb_id} doc_id={doc_id} —— 上传/处理/检索全链路验证通过")


if __name__ == "__main__":
    main()
