# 知识库 & 记忆系统 — 开发对接指南

> 面向对接同事，快速理解代码结构、本地调试和扩展方式。
> 最后更新：2026-07-15

---

## 一、整体架构

```
┌────────────────────────────────────────────────────────────────┐
│  前端 / Go 引擎 / Agent 图                                      │
│    └─→ HTTP 调用检索服务                                         │
└────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────────────┐
│            检索服务 FastAPI (lea-rag:8300)                       │
│                                                                │
│  路由文件            职责                                        │
│  server_kb.py       知识库 CRUD + 文档上传 + 检索               │
│  server_memory.py   记忆 CRUD + 语义检索 + 对话提取             │
│  server.py          汇总 mount + /health                        │
└────────────────────────────────────────────────────────────────┘
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
         Milvus       MySQL       Celery Worker
       (向量存储)    (元数据)     (文档异步处理)
```

### 包划分

| 包 | 路径 | 职责 |
|---|---|---|
| lea-rag | `packages/lea-rag/` | 检索服务主进程、embedding 客户端、Milvus 索引、Celery tasks |
| lea-graph | `packages/lea-graph/` | Agent 图；memory 子包提供记忆 Service/Retriever/Extractor |
| lea-server | `packages/lea-server/` | DB 模型、kb_processor（文档解析+入库 pipeline）、kb_service |
| lea-apps | `packages/lea-apps/` | 上层应用；notebook/services/parser.py 提供 MinerU/PyMuPDF 解析 |

---

## 二、核心代码文件速查

### 2.1 知识库

| 文件 | 作用 |
|------|------|
| `lea-rag/src/lea_rag/server_kb.py` | KB 路由：/kb/search, /kb/create, /kb/{id}/upload 等 |
| `lea-rag/src/lea_rag/retrieval/hybrid_v2.py` | 双路检索器：Dense + BM25 + RRF + Reranker |
| `lea-rag/src/lea_rag/index/milvus_index.py` | Milvus collection 管理、插入、删除 |
| `lea-rag/src/lea_rag/embeddings/embedding_client.py` | 通用 Embedding 客户端（OpenAI/TEI/Infinity 格式） |
| `lea-rag/src/lea_rag/config.py` | 配置类：MilvusSettings, EmbeddingSettings, RerankerSettings |
| `lea-rag/src/lea_rag/tasks.py` | Celery app + process_document 任务定义 |
| `lea-server/src/lea_server/services/kb_processor.py` | 文档处理 Pipeline（解析→分块→embed→入库→VLM caption） |
| `lea-server/src/lea_server/services/kb_service.py` | KB + Document CRUD（SQLAlchemy） |
| `lea-server/src/lea_server/models/knowledge.py` | DB 模型：KnowledgeBase, Document, DocumentChunk, RetrievalModel |
| `lea-apps/src/lea_apps/notebook/services/parser.py` | MinerU 云/本地解析 + PyMuPDF fallback |

### 2.2 记忆系统

| 文件 | 作用 |
|------|------|
| `lea-rag/src/lea_rag/server_memory.py` | 记忆路由：/memory/add, /memory/search, /memory/extract |
| `lea-graph/src/lea_graph/memory/service.py` | MemoryService：CRUD + Milvus 向量同步 |
| `lea-graph/src/lea_graph/memory/retriever.py` | MemoryRetriever：dense-only 检索 + 多信号融合排序 |
| `lea-graph/src/lea_graph/memory/extractor.py` | 从对话提取记忆（调 LLM） |
| `lea-graph/src/lea_graph/memory/config.py` | MemorySettings 配置 |
| `lea-server/src/lea_server/models/chat_session.py` | DB 模型：UserMemory |

---

## 三、本地启动

### 3.1 基础设施

```bash
# 启动 Milvus + MySQL + Redis + MinIO
docker compose -f infra/docker-compose.dev.yml up -d
```

### 3.2 检索服务 (FastAPI)

```bash
cd packages/lea-rag
uv run uvicorn lea_rag.server:app --host 0.0.0.0 --port 8300 --reload
```

### 3.3 Celery Worker（文档处理）

```bash
cd packages/lea-rag
uv run celery -A lea_rag.tasks worker -Q retrieval -l info --pool solo
```

> Windows 用 `--pool solo`；Linux/Mac 可用 `--pool prefork`。
> 改了 kb_processor.py 或 parser.py 后需重启 worker。

### 3.4 关键环境变量

```dotenv
# .env（项目根目录）
LEA_MILVUS_URI=http://localhost:19530
LEA_EMBEDDING_API_BASE=https://tenantapi-ai.ouchn.edu.cn/v1/embeddings
LEA_EMBEDDING_MODEL=bge-large-zh-v1.5
LEA_TENANT_API_KEY=sk-xxx          # 中转平台 key（embedding + LLM 共用）
MINERU_TOKEN=xxx                    # MinerU 云端 API token
REDIS_URL=redis://localhost:6379/0
DATABASE_URL=mysql+pymysql://root:password@localhost:3306/lea
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
```

---

## 四、数据流详解

### 4.1 文档入库流程

```
用户上传 PDF
  → POST /kb/{kb_id}/upload
  → 存文件到 MinIO (file_key = "kb/{kb_id}/{filename}")
  → 写 Document 记录到 MySQL (status=processing)
  → 发 Celery 任务 process_document(doc_id)

Worker 收到任务：
  1. 从 MinIO 下载文件到 /tmp
  2. 解析：MinerU 云端（VLM模式）→ fallback PyMuPDF
     产出: text_blocks + image_items + content_list
  3. 分块：sentence-window chunking（~200字/块 + 前后窗口上下文）
  4. 章节提取：从 heading blocks 构建 DocumentSection 树
  5. 文本向量化：EmbeddingClient → Milvus (chunk_type="text")
  6. 图片处理（if vlm_enabled）：
     image → qwen3-vl-plus 生成 caption → text embed → Milvus (chunk_type="image_caption")
  7. 图片处理（if multimodal）：
     image → multimodal embed → Milvus (chunk_type="image")  [预留]
  8. 更新 Document status=ready
```

### 4.2 检索流程

```
POST /kb/search {"query": "transformer 注意力机制", "kb_ids": [1]}
  │
  ├── _get_visible_kb_ids：按 visibility 自动过滤可见 KB
  ├── _group_kbs_by_model：按 embedding_model 分组（支持跨模型混搜）
  │
  └── 每组执行 HybridRetrieverV2.search()：
      ├── query → EmbeddingClient.aembed_text → dense_vector
      ├── Dense Search: Milvus ANN (COSINE, nprobe=16)
      ├── BM25 Search: Milvus BM25 (jieba 中文分词)
      ├── 结果合并（hybrid 模式两路都走）
      ├── Section Boost: 章节标题匹配 query → +0.1 分
      └── Reranker 精排（如果启用）→ top_k
```

**跨模态检索**：文本 query 能命中 image_caption 类型的 chunk（VLM 生成的描述），实现"文搜图"。

### 4.3 记忆流程

```
写入：POST /memory/add
  → MySQL 写入 UserMemory
  → EmbeddingClient 向量化
  → Milvus collection "lea_user_memories" 插入
  → 标记 embedding_synced=True

检索：POST /memory/search
  → query → embedding → Milvus dense search (user_id + tenant_id + scopes 过滤)
  → 从 MySQL 补充 importance / access_count / created_at
  → 多信号融合排序:
      final_score = semantic×0.5 + recency×0.2 + importance×0.2 + frequency×0.1
  → 更新 access_count

提取：POST /memory/extract（可选，推荐引擎侧自行提取）
  → LLM 从对话中提取 facts → 逐条调用 add
```

---

## 五、模型配置

### 5.1 RetrievalModel 注册表

位于 MySQL `retrieval_models` 表，通过 Admin API 或 DB 直接管理：

```sql
-- 示例：注册 embedding 模型
INSERT INTO retrieval_models (name, model_type, api_base, model_name, dimension, enabled, is_default)
VALUES ('bge-large-zh', 'embedding', 'https://tenantapi-ai.ouchn.edu.cn', 'bge-large-zh-v1.5', 1024, 1, 1);

-- 示例：注册 VLM 模型
INSERT INTO retrieval_models (name, model_type, api_base, model_name, enabled, is_default)
VALUES ('qwen-vl', 'vlm', 'https://tenantapi-ai.ouchn.edu.cn', 'qwen3-vl-plus', 1, 1);
```

| model_type | 用途 | 调用方式 |
|-----------|------|---------|
| embedding | 文本/图片向量化 | OpenAI /v1/embeddings 格式 |
| vlm | 图片生成文字描述 | OpenAI /v1/chat/completions (multimodal) |
| reranker | 精排 | BGE-Reranker API |

### 5.2 KB 创建时如何选模型

1. 创建 KB 时可指定 `embedding_model` 名称
2. 不指定 → 取 `retrieval_models` 表中 `model_type=embedding + is_default=True` 的模型
3. 都没有 → fallback 到 `EmbeddingSettings` 环境变量配置

```python
# kb_processor.py 中的模型查找逻辑
def _get_embedder(self, kb):
    model_name = kb.embedding_model
    if model_name:
        m = db.query(RetrievalModel).filter(name=model_name, enabled=True).first()
        if m:
            return EmbeddingClient(api_base=m.api_base, model=m.model_name)
    # fallback to default
    m = db.query(RetrievalModel).filter(model_type="embedding", is_default=True).first()
    ...
```

---

## 六、EmbeddingClient 使用说明

位于 `lea-rag/src/lea_rag/embeddings/embedding_client.py`，是整个系统统一的 embedding 入口。

### 自动协议探测

- `api_base` 含 `/v1/embeddings` 或 `/embeddings` → OpenAI 格式
- 否则 → TEI/Infinity 格式（POST `/embed`）

### 使用示例

```python
from lea_rag.embeddings.embedding_client import EmbeddingClient

# 文本 embedding
client = EmbeddingClient(
    api_base="https://tenantapi-ai.ouchn.edu.cn/v1/embeddings",
    model="bge-large-zh-v1.5",
    api_key="sk-xxx",
)
vec = await client.aembed_text("你好世界")         # -> list[float]
vecs = await client.aembed_texts(["文本1", "文本2"]) # -> list[list[float]]

# 图片 embedding（需要多模态模型支持）
img_vecs = await client.aembed_images([img_bytes])  # -> list[list[float]]
```

### 同步接口（Celery worker 中使用）

```python
vec = client.embed_text("文本")
vecs = client.embed_texts(["文本1", "文本2"])
img_vecs = client.embed_images([img_bytes])
```

---

## 七、Milvus Collection 结构

### 知识库：`lea_knowledge_v2`

```
字段              类型                 说明
─────────────────────────────────────────────────────
id               INT64 (auto PK)     自增主键
content          VARCHAR(65535)       文本（BM25 jieba 分析器自动索引）
dense_vector     FLOAT_VECTOR(1024)   语义向量
sparse_vector    SPARSE_FLOAT_VECTOR  BM25 自动生成（不需手动写入）
kb_id            INT64               知识库 ID
document_id      INT64               文档 ID
section_id       INT64               章节 ID
tenant_id        VARCHAR(64)         租户
chunk_type       VARCHAR(20)         text / image_caption / image / table
source_filename  VARCHAR(300)        文件名
source_url       VARCHAR(500)        MinIO key
page             INT32               页码
```

> `sparse_vector` 由 Milvus BM25 Function 自动从 `content` 生成，插入时不需传。

### 记忆：`lea_user_memories`

```
字段          类型                说明
──────────────────────────────────────────────
id           INT64 (auto PK)    自增主键
memory_id    INT64              MySQL UserMemory.id
user_id      VARCHAR(64)        用户
tenant_id    VARCHAR(64)        租户
category     VARCHAR(32)        fact / preference / experience / skill
scope        VARCHAR(100)       global / agent:{agent_id}
content      VARCHAR(2048)      记忆文本
embedding    FLOAT_VECTOR(1024) 语义向量
```

---

## 八、调试技巧

### 8.1 快速验证检索服务

```bash
# 健康检查
curl http://localhost:8300/health

# 创建知识库
curl -X POST http://localhost:8300/kb/create \
  -H "Content-Type: application/json" \
  -d '{"name":"测试KB","vlm_enabled":true}'

# 上传文档
curl -X POST http://localhost:8300/kb/1/upload \
  -F "file=@test.pdf" -F "tenant_id=default"

# 查看文档状态（等 worker 处理完）
curl http://localhost:8300/kb/1/documents

# 搜索
curl -X POST http://localhost:8300/kb/search \
  -H "Content-Type: application/json" \
  -d '{"query":"注意力机制","kb_ids":[1]}'
```

### 8.2 查看 Worker 日志

```bash
# Worker 日志会输出每个阶段的 structlog 信息：
# mineru_poll       → MinerU 轮询状态
# vectors_indexed_v2 → Milvus 入库成功
# images_indexed    → 图片 VLM caption 入库
# vlm_caption_failed → VLM 调用失败（看 error 字段）
# document_processed → 全流程完成
```

### 8.3 直接操作 Milvus 调试

```python
from pymilvus import MilvusClient

client = MilvusClient(uri="http://localhost:19530")

# 查看 collection 信息
client.describe_collection("lea_knowledge_v2")

# 查看某个 KB 下有多少向量
client.query("lea_knowledge_v2", filter="kb_id == 1", output_fields=["count(*)"])

# 查看 image_caption 类型
client.query("lea_knowledge_v2",
    filter='kb_id == 1 and chunk_type == "image_caption"',
    output_fields=["content", "page", "source_filename"],
    limit=10)
```

### 8.4 记忆调试

```bash
# 添加记忆
curl -X POST http://localhost:8300/memory/add \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u001","content":"用户喜欢简洁的解释风格","category":"preference"}'

# 检索记忆
curl -X POST http://localhost:8300/memory/search \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u001","query":"解释偏好"}'
```

---

## 九、已支持的文件类型 & 扩展指南

### 9.1 当前支持的格式

| 格式 | 解析方式 | 产出 | 入库 chunk_type |
|------|---------|------|----------------|
| PDF | MinerU (VLM模式) / PyMuPDF fallback | text_blocks + image_items | text, image_caption |
| DOCX | python-docx（段落+表格+内嵌图片） | text_blocks + image_items | text, image_caption |
| PPTX | python-pptx（逐slide提取文本+图片+表格） | text_blocks + image_items | text, image_caption |
| 图片 (png/jpg/webp/gif/bmp) | 直接读取 base64 | image_items | image_caption（VLM 路径） |
| TXT/MD | 直接读文本 | text_blocks | text |
| 音频 (mp3/wav/m4a/flac/ogg) | ASR 转写（需配置 ASR 服务） | text_blocks | audio_transcript |

所有解析器统一通过 `parse_document(file_path)` 自动路由（按扩展名）。

### 9.2 新增文件类型（扩展模式）

1. 在 `lea-apps/src/lea_apps/notebook/services/parser.py` 中添加 `parse_xxx(file_path) -> dict`
   - 返回格式：`{"md_content": str, "text_chunks": list[dict], "image_items": list[dict]}`
   - 每个 text_chunk: `{"content": str, "window_content": str, "page": int, "type": str}`
   - 每个 image_item: `{"base64": str, "page_idx": int, "caption": str, "img_path": str}`
2. 在 `parse_document()` 函数里按扩展名添加路由分支
3. 在 `server_kb.py` `upload_document` 的 `allowed_ext` 集合中加入新扩展名
4. （可选）在 `kb_processor._parse()` 的 file_type 条件中补充（默认所有非 txt/md 都走 parse_document）

### 9.3 新增 Embedding 模型

1. 在 `retrieval_models` 表中注册新模型
2. 创建 KB 时指定 `embedding_model` 为新模型 name
3. 如果 API 格式特殊，在 `EmbeddingClient` 中扩展协议探测逻辑

### 9.3 启用多模态 Embed（路径 B）

当多模态 embedding 模型（如 Jina v5 Omni）就绪后：

1. 在 `retrieval_models` 注册模型，`supports` 标记包含 `["text", "image"]`
2. 创建 KB 时设置 `multimodal=True`
3. `kb_processor._index_images()` 中的 multimodal 分支会自动启用：
   - 图片 bytes → `embedder.embed_images()` → Milvus (chunk_type="image")

### 9.4 自定义记忆检索排序

修改 `lea-graph/src/lea_graph/memory/retriever.py` 中的 `_fuse_scores` 方法：

```python
def _fuse_scores(self, candidates):
    for c in candidates:
        c["final_score"] = (
            c["semantic_score"] * 0.5    # 语义相关度
            + recency_score * 0.2         # 时效性（半衰期 30 天）
            + importance / 10 * 0.2       # 重要度 (1-10)
            + frequency_score * 0.1       # 使用频率
        )
    # 调整权重或添加新信号即可
```

---

## 十、已知限制 & 后续规划

| 项目 | 现状 | 改进方向 |
|------|------|---------|
| VLM caption | 同步调用，单张图片~2s | 可并行处理多图 |
| BM25 | 依赖 Milvus 内置 jieba | 可自定义词典提升专业术语召回 |
| Reranker | 需要独立部署 bge-reranker 服务 | 可用中转平台的 rerank API |
| 记忆去重 | dedup_threshold=0.85 余弦相似度 | 实际未在 add 时做检查，待补 |
| 多模态 embed | 骨架就绪，缺多模态模型 | 等 Jina v5 Omni 或对齐模型部署 |
| 文件类型 | PDF/DOCX/PPTX/图片/TXT/MD + 音频骨架 | 视频待补 |
| ASR | 骨架就绪，走 OpenAI whisper 协议 | 需配置 ASR 服务端点 |

