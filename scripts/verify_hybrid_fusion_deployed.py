"""部署后验证：混合检索真的双路召回，而不是只走 dense。

构造 A/B 数据集，让两条路各自只能命中一边：
  A 文档：含一个罕见精确词（BM25 能命中），语义上和查询无关
  B 文档：语义贴合查询（dense 能命中），但不含那个罕见词

于是三种 strategy 的期望结果是可区分的：
  keyword  -> 只见 A
  semantic -> 只见 B
  hybrid   -> A 和 B 都在

如果 hybrid 的结果等于 semantic 的结果，说明 BM25 那一路没走 —— 这正是改之前
的行为（_hybrid_search 只发了一次 dense search）。

跑完清理插入的数据。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import urllib.request

sys.path.insert(0, "/app/src")

# kb_id 每轮随机。检索缓存的键是 (query, kb_id, top_k, strategy)——**不含 ranker**，
# 所以切了 LEA_RAG_HYBRID_RANKER 再用同一个 kb_id 跑，三种 strategy 会全部命中上一轮
# 的缓存：结果看起来对，但一次 Milvus 都没发，等于什么都没验。
# 换 kb_id 同时隔离了缓存和数据，比在 query 里塞随机串干净（不动检索语义）。
KB_ID = 990000 + random.randint(1, 8999)
TENANT = "verify-hybrid"
RARE_TOKEN = "蓝鲸编码器X7Q"
QUERY = f"{RARE_TOKEN} 怎么给神经网络做图像特征提取"

DOCS = [
    # A：含罕见词，语义无关（讲的是食堂菜单）
    f"{RARE_TOKEN} 是本周食堂三号窗口的招牌菜编号，配菜含土豆和青椒，供应到下午两点。",
    # B：语义贴合（讲图像特征提取），不含罕见词
    "卷积神经网络通过多层卷积核对图像做特征提取，浅层学边缘纹理，深层组合成语义部件。",
    # 填充，避免 top_k 太容易全覆盖
    "财务报销流程：发票先在系统里登记，再交由部门负责人审批，最后财务复核。",
    "会议室预订规则：提前一天在日历上占用，连续占用不得超过四小时。",
    "打印机耗材更换：墨盒型号写在机身背面标签上，更换后需要跑一次校准页。",
]


def post(path: str, payload: dict) -> object:
    # token 从容器自己的环境变量读，不经命令行（命令行会进 ps 和 history）
    headers = {"Content-Type": "application/json"}
    token = os.getenv("LEA_INTERNAL_TOKEN", "").strip()
    if token:
        headers["X-Internal-Token"] = token
    req = urllib.request.Request(
        f"http://127.0.0.1:8300{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def metrics_lines(needle: str) -> list[str]:
    # /metrics 对内部认证豁免（Prometheus 的 scrape_configs 不支持自定义头）
    raw = urllib.request.urlopen("http://127.0.0.1:8300/metrics", timeout=20).read().decode()
    return [ln for ln in raw.splitlines() if needle in ln and not ln.startswith("#")]


async def main() -> int:
    from lea_rag.index.milvus_index import MilvusKBIndex
    from lea_rag.server_kb import _get_embedder

    index = MilvusKBIndex()
    ranker = (os.getenv("LEA_RAG_HYBRID_RANKER") or "rrf").strip().lower()
    if ranker not in {"rrf", "weighted"}:
        ranker = "rrf"  # 服务端也是这么回退的
    print(f"collection = {index.collection_name}  ranker = {ranker}  kb_id = {KB_ID}")

    embedder = _get_embedder("default")
    vecs = await embedder.aembed_texts(DOCS)
    print(f"embedding 完成：{len(vecs)} 条，dim={len(vecs[0])}")

    rows = []
    for i, (text, vec) in enumerate(zip(DOCS, vecs, strict=True)):
        rows.append({
            "content": text,
            "dense_vector": vec,
            "kb_id": KB_ID,
            "document_id": 990100 + i,
            "section_id": 0,
            "tenant_id": TENANT,
            "chunk_type": "text",
            "source_filename": f"verify_{i}.txt",
            "source_url": "",
            "page": 1,
        })

    try:
        index.insert(rows)
        index.client.flush(collection_name=index.collection_name)
        print(f"插入 {len(rows)} 行，kb_id={KB_ID}")

        results: dict[str, list[str]] = {}
        for strategy in ("keyword", "semantic", "hybrid"):
            hits = post("/kb/search", {
                "query": QUERY,
                "top_k": 5,
                "kb_ids": [KB_ID],
                "tenant_id": TENANT,
                "strategy": strategy,
            })
            tags = []
            for h in hits:
                c = h["content"]
                tag = "A(罕见词)" if RARE_TOKEN in c else (
                    "B(语义)" if "卷积神经网络" in c else "填充")
                tags.append(f"{tag}:{h['score']:.4f}")
            results[strategy] = tags
            print(f"\n[{strategy}] {len(hits)} hits")
            for t in tags:
                print(f"    {t}")

        def has(strategy: str, tag: str) -> bool:
            return any(t.startswith(tag) for t in results[strategy])

        # 决定性判据是 RRF 的分数算术，不是"结果看起来不一样"。
        #
        # RRF 给一个文档的分是 Σ 1/(k+rank)，对每个它出现过的候选列表各加一项。
        # 所以只被 dense 召回的文档拿 1/(k+rank)，被两条路都召回的拿两项之和 ——
        # 大约翻倍。拿 scores 去反推"这个文档出现在几个列表里"，就能把"真融合"
        # 和"只发了一次 dense search"区分开：后者**所有**文档都只能是单列表分。
        #
        # 下面还会用单路 hybrid_search 做一次反证，确认这个判据真的会红。
        k = 60
        hybrid_scores = [float(t.split(":")[1]) for t in results["hybrid"]]
        top_score = max(hybrid_scores) if hybrid_scores else 0.0
        if ranker == "rrf":
            single_list_top = 1.0 / (k + 1)      # 0.01639
            dual_list_top = 2.0 / (k + 1)        # 0.03279
            dual = abs(top_score - dual_list_top) < 1e-4
            arith = (f"hybrid 头名分数 {top_score:.5f} ≈ 2/(k+1)={dual_list_top:.5f}"
                     f"（单列表只会是 {single_list_top:.5f}）→ 两条路都贡献了名次")
        else:
            # WeightedRanker(0.5, 0.5, norm_score=True)：分数是归一化后的加权和，
            # 单列表文档的上限就是它那一路的权重 0.5，超过 0.5 只能来自两路相加。
            w = 0.5
            single_list_top = w
            dual = top_score > w + 1e-3
            arith = (f"hybrid 头名分数 {top_score:.5f} > dense 权重 {w}"
                     f"（单列表上限就是 {w}）→ 两条路都贡献了分数")

        print("\n=== 判定 ===")
        ok = True
        checks = [
            ("keyword 命中 A", has("keyword", "A")),
            ("semantic 命中 B", has("semantic", "B")),
            ("hybrid 同时命中 A 和 B", has("hybrid", "A") and has("hybrid", "B")),
            (arith, dual),
        ]
        for name, passed in checks:
            print(f"  [{'OK' if passed else 'FAIL'}] {name}")
            ok = ok and passed

        # ── 反证：只喂一个 AnnSearchRequest，上面那条算术判据必须变红 ──────
        #
        # 这是在验证"验证本身"。一个永远绿的检查和没有检查是一回事，所以把
        # BM25 那一路摘掉，直接问 Milvus 同一个 RRF ranker，看头名分数是否
        # 掉回单列表的 1/(k+1)。掉回去了，说明上面的 2/(k+1) 确实只能由双路产生。
        from lea_rag.server_kb import _get_embedder as _ge
        from pymilvus import AnnSearchRequest, RRFRanker, WeightedRanker
        qvec = (await _ge("default").aembed_texts([QUERY]))[0]
        solo_ranker = (RRFRanker(k=k) if ranker == "rrf"
                       else WeightedRanker(0.5, norm_score=True))
        dense_only = index.client.hybrid_search(
            collection_name=index.collection_name,
            reqs=[AnnSearchRequest(
                data=[qvec], anns_field="dense_vector",
                param={"metric_type": "COSINE", "params": {"nprobe": 16}},
                limit=20, filter=f"kb_id == {KB_ID}",
            )],
            ranker=solo_ranker,
            limit=5,
            output_fields=["content"],
        )
        solo_top = max(
            (h.get("distance", 0.0) if isinstance(h, dict) else h.distance)
            for rl in dense_only for h in rl
        )
        falsified = solo_top <= single_list_top + 1e-3
        print(f"  [{'OK' if falsified else 'FAIL'}] 反证：单路 {ranker} 头名 "
              f"{solo_top:.5f} ≤ 单列表上限 {single_list_top:.5f}"
              f" → 判据能区分单路/双路，不是恒真")
        ok = ok and falsified

        print("\n=== operation 标签 ===")
        ops = metrics_lines("vector_db_queries_total")
        for ln in ops:
            print(f"  {ln}")

        # 没有任何 operation 序列 = 这一轮根本没发出 Milvus 请求（全缓存命中）。
        # 上面那些判定此时全都是在检查上一轮的缓存结果，必须判失败而不是绿着过。
        expect = {f"search_hybrid_{ranker}", "search_dense", "search_bm25"}
        got = {ln.split('operation="')[1].split('"')[0] for ln in ops}
        missing = expect - got
        if missing:
            print(f"  [FAIL] 这一轮没发出这些检索: {sorted(missing)}"
                  f"（缓存命中？判定结果不可信）")
            ok = False

        return 0 if ok else 1
    finally:
        index.delete_by_kb(KB_ID)
        index.client.flush(collection_name=index.collection_name)
        left = index.client.query(
            collection_name=index.collection_name,
            filter=f"kb_id == {KB_ID}",
            output_fields=["count(*)"],
        )[0]["count(*)"]
        print(f"\n清理完成，kb_id={KB_ID} 剩余 {left} 行")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
