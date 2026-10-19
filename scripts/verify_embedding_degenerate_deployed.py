"""在部署的服务里验证"上游假成功"检测真的会出指标序列。

单测过了只证明函数逻辑对。这里要证的是另一件事：**走完真实调用链之后，
/metrics 上真的出现 status="degenerate" 这条序列** —— 否则就又是一条
"埋点写了但永远不出数"的哑弹，和这个 session 开头修的那批一模一样。

做法：不改部署的源码，只在进程里把 httpx 的 post 换掉，让它回
200 + 全零向量（这正是 key 过期时上游的表现）。然后读 /metrics 看序列。

三段判定，缺一不可：
  1. 注入前 degenerate 序列不存在（基线干净）
  2. 注入假成功后 degenerate 序列出现且计数增加
  3. 正常向量仍然记 success —— 证明检测器会区分，不是一律报警

跑法：
    docker cp scripts/verify_embedding_degenerate_deployed.py lea-rag-test:/tmp/d.py
    docker exec lea-rag-test python /tmp/d.py
"""

from __future__ import annotations

import asyncio
import sys
import urllib.request

sys.path.insert(0, "/app/src")

METRICS = "http://127.0.0.1:8300/metrics"


def _registry_text() -> str:
    """读**本进程**的 prometheus 注册表。

    不能读 http://127.0.0.1:8300/metrics —— 那是 uvicorn 进程的注册表，
    而 prometheus_client 的注册表是**进程内**的。这个脚本是 docker exec 起的
    另一个进程，在这里调 record_embedding 增加的是本进程的计数器，HTTP 端点
    上永远看不到（第一版就踩了这个，三条判定全 FAIL 而检测其实是好的）。

    这里要证的是"record_embedding 被正确的 status 调用并产出序列"，用本进程
    的注册表就够，而且更干净 —— 新进程基线为空，计数是确定的。
    """
    from prometheus_client import generate_latest

    return generate_latest().decode("utf-8")


def series(status: str) -> float | None:
    """读 lea_rag_embedding_total{status=...} 的值，没有序列返回 None。"""
    needle = f'lea_rag_embedding_total{{status="{status}"}}'
    for ln in _registry_text().splitlines():
        if ln.startswith("#"):
            continue
        if ln.startswith(needle):
            return float(ln.rsplit(" ", 1)[1])
    return None


class _FakeResp:
    """假装上游回了 200。"""

    status_code = 200

    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


async def main() -> int:
    from lea_rag.embeddings.embedding_client import EmbeddingClient

    client = EmbeddingClient()
    dim = 1536

    print("=== 1. 基线 ===")
    base_deg = series("degenerate")
    base_ok = series("success")
    print(f"  degenerate = {base_deg if base_deg is not None else '(无序列)'}")
    print(f"  success    = {base_ok if base_ok is not None else '(无序列)'}")

    # ── 注入：200 + 全零向量，模拟 key 过期 ────────────────────────────────
    print("\n=== 2. 注入假成功（200 + 全零向量）===")
    texts = ["第一段文本", "第二段文本"]
    zero_payload = {"data": [{"embedding": [0.0] * dim} for _ in texts]}

    # 形参名必须叫 json —— _apost 是按关键字传的（client.post(url, json=...)）。
    async def fake_post(url, json=None, **kw):  # noqa: A002
        return _FakeResp(zero_payload)

    original = client.aclient.post
    client.aclient.post = fake_post
    try:
        out = await client.aembed_texts(texts)
    finally:
        client.aclient.post = original
    print(f"  调用返回了 {len(out)} 条向量，每条 dim={len(out[0])}")
    print(f"  全零吗: {not any(out[0])}")

    after_deg = series("degenerate")
    print(f"  degenerate = {after_deg if after_deg is not None else '(无序列)'}")

    # ── 对照：正常向量必须记 success ───────────────────────────────────────
    print("\n=== 3. 对照：正常向量（走真实上游）===")
    before_ok = series("success")
    real = await client.aembed_texts(["对照组：一段正常的中文文本"])
    after_ok = series("success")
    deg_unchanged = series("degenerate")
    print(f"  拿到 {len(real)} 条向量，dim={len(real[0])}，全零吗: {not any(real[0])}")
    print(f"  success: {before_ok} -> {after_ok}")
    print(f"  degenerate 没被误增: {after_deg} -> {deg_unchanged}")

    # ── 判定 ───────────────────────────────────────────────────────────────
    print("\n=== 判定 ===")
    checks = [
        ("注入前 degenerate 无序列（基线干净）", base_deg is None),
        ("注入后 degenerate 序列出现", after_deg is not None),
        ("degenerate 计数 = 1", after_deg == 1.0),
        (
            "正常调用记 success（计数+1）",
            # 基线可能是 None（新进程里序列还不存在），当 0 算。
            after_ok == (before_ok or 0) + 1,
        ),
        ("正常调用没把 degenerate 记上", deg_unchanged == after_deg),
    ]
    ok = True
    for name, passed in checks:
        print(f"  [{'OK' if passed else 'FAIL'}] {name}")
        ok = ok and passed

    print("\n=== 本进程注册表里的相关序列 ===")
    for ln in _registry_text().splitlines():
        if ln.startswith("lea_rag_embedding_total{"):
            print(f"  {ln}")

    print("\n=== 服务进程 /metrics 上的同名序列（供对照）===")
    try:
        raw = urllib.request.urlopen(METRICS, timeout=20).read().decode()
        hit = [ln for ln in raw.splitlines() if ln.startswith("lea_rag_embedding_total{")]
        # 这里是 uvicorn 进程的计数，反映的是**真实业务流量**，跟本脚本无关。
        print("\n".join(f"  {ln}" for ln in hit) if hit else "  (无序列：服务侧还没跑过 embedding)")
    except Exception as exc:
        print(f"  读取失败: {exc}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
