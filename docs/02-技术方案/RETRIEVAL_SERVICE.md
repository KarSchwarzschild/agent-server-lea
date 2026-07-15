# LEA 统一检索服务 — 设计文档

## 核心设计原则

1. **万物转文字，文字做检索** — 非文本内容（图片/音频/视频）先转成文字，再走统一 embed + BM25
2. **模型可换** — embedding 模型 per-KB 配置，通过策略模板管理
3. **双路召回模型无关** — Dense（任意文本模型）+ BM25（Milvus 内置 jieba），互不依赖
4. **检索服务无 LLM 调用** — 纯机械、亚秒级；query 改写/扩写/记忆提取由引擎层负责
5. **多模态 embed 可选增强** — 不是默认路径，按需开启
6. **对引擎透明** — Go 引擎（云端/客户端）只需 HTTP REST 调用，不关心内部实现

---

## 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│        Go 执行引擎（云端 + 客户端共用）                         │
│  Agent 编排 / 工具执行 / 对话管理 / 记忆提取                   │
│      └─→ HTTP REST 调用检索服务                               │
└──────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────┐
│               检索服务 (lea-rag:8300)                          │
│               定位：纯机械检索 + 记忆存储                       │
│                                                              │
│  /kb/search         知识库混合检索                             │
│  /kb/{id}/upload    文档上传 → Celery 任务                    │
│  /kb/create         创建知识库（含策略选择）                    │
│  /kb/models         可选模型列表 + 能力声明                    │
│  /kb/strategies     可选策略模板                               │
│  /memory/search     用户记忆语义检索                           │
│  /memory/add        添加记忆（支持 scope）                    │
│  /admin/retrieval/* 模型与策略管理                             │
└──────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────┐
│              Celery Worker (retrieval-worker)                  │
│  队列: retrieval                                              │
│  任务: process_document                                       │
│  流程: 解析 → 转文字 → 分块 → embed → Milvus                 │
└──────────────────────────────────────────────────────────────┘
```

### 外部服务

| 服务 | 用途 | 默认端口 | 环境变量 |
|------|------|---------|---------|
| MinerU | PDF/文档版面解析 | :8001 | `MINERU_URL` |
| VLM | 图片 → 文字描述 | :8020 | `LEA_VLM_API_BASE` |
| ASR | 音频 → 文字转写 | :8030 | `LEA_ASR_API_BASE` |
| Embedding | 文本 → 向量 | :8012 | `LEA_EMBEDDING_API_BASE` |
| Reranker | 精排 | :6006 | `LEA_RERANKER_API_BASE` |
| Milvus | 向量存储 | :19530 | `LEA_MILVUS_URI` |

---

## 文档处理 Pipeline

### 输入类型 × 处理路径

| 输入 | 解析方式 | 产出文字 | 向量化 |
|------|---------|---------|--------|
| PDF | MinerU → text/image/table blocks | text 直接用；image → VLM | text embed |
| DOCX/PPTX | python-docx/pptx 提取 | 文本 + 图片 → VLM | text embed |
| TXT/MD | 直接读 | 原文 | text embed |
| 图片 (png/jpg) | — | VLM → 描述文字 | text embed |
| 音频 (mp3/wav) | — | ASR → 转写 | text embed (P2) |
| 视频 (mp4) | 拆音频 + 关键帧 | ASR + VLM | text embed (P2) |

### 处理流程

```
Celery Worker 收到 process_document(doc_id)
  │
  ├── 读取 KB 策略配置
  ├── 下载文件 (MinIO)
  │
  ├── Stage 1: 解析
  │   ├── pdf → MinerU → text/image/table/heading blocks
  │   ├── docx/pptx → python-docx/pptx → text + images
  │   ├── txt/md → 直接读
  │   └── png/jpg → ImageBlock
  │
  ├── Stage 2: 非文本 → 文字
  │   ├── ImageBlock + vlm_enabled → VLM → caption text
  │   └── AudioBlock + asr_enabled → ASR → transcript (P2)
  │
  ├── Stage 3: 分块
  │   ├── 文本 → sentence-window chunking (~512 tokens)
  │   └── 每 chunk 关联 section_id（从 heading blocks 提取）
  │
  ├── Stage 4: 向量化 + 写 Milvus
  │   ├── chunk.content → Embedding → dense_vector
  │   └── chunk.content → Milvus BM25 自动索引 (jieba)
  │
  └── Stage 5: [如果开了 multimodal_embed 且模型支持]
      └── 原图 bytes → Embedding → 额外存一条 image 向量
```

---

## 检索 Pipeline

### 完整流程

```
Query（agent 层已完成改写/扩写）
  │
  ├──→ Dense Search: Embedding → Milvus ANN (COSINE, IVF_FLAT)
  │
  ├──→ BM25 Search: Milvus 内置 (jieba 中文分词)
  │
  └──→ RRF Fusion (k=60) 合并双路结果
          │
          ▼
      Reranker 精排 (BGE-Reranker-v2-m3)
          │
          ▼
      top_k 结果
```

### Reranker（精排模型）

| 配置 | 说明 |
|------|------|
| `reranker_model` | 模型名（如 bge-reranker-v2-m3） |
| `LEA_RERANKER_API_BASE` | 服务地址 |
| 熔断 | 连续失败 3 次 → 60s 降级跳过 |
| 位置 | RRF 融合之后，输出 top_k 之前 |

### 检索策略（search API）

| strategy | 行为 |
|----------|------|
| `hybrid`（默认） | Dense + BM25 双路 |
| `semantic` | 仅 Dense |
| `keyword` | 仅 BM25 |

### 来源追溯

检索服务返回的每条结果包含足够的溯源字段（`section_title`, `chunk_type`, `page`, `source_url`），供上层 chat 层组装展示。

chat 层如何消费这些字段（SSE references 事件）属于聊天协议设计，见 `lea_graph/tools/knowledge.py`。

---

## 模型与策略配置

### 模型注册表（Admin 管理）

```
POST /admin/retrieval/models
{
  "name": "bge-m3",
  "model_type": "embedding",       // embedding / vlm / asr / reranker
  "api_base": "http://embed:8012",
  "model_name": "bge-m3",
  "dimension": 1024,
  "supports": ["text"],            // 能力声明
  "enabled": true
}
```

模型类型：

| model_type | 用途 | supports 可选值 |
|-----------|------|----------------|
| embedding | 向量化 | text, image, audio, video |
| vlm | 图片理解 | — |
| asr | 语音转写 | — |
| reranker | 精排 | — |

### 策略模板（Admin 管理）

策略模板打包"哪些模型 + 哪些参数"，用户创建 KB 时选模板即可：

```
POST /admin/retrieval/strategies
{
  "name": "标准文本",
  "embedding_model": "bge-m3",
  "reranker_model": "bge-reranker-v2-m3",
  "vlm_model": null,
  "retrieval_mode": "hybrid",
  "multimodal_embed": false,
  "top_k": 5,
  "score_threshold": 0.5,
  "is_default": true
}
```

### 预设策略模板

| 模板名 | embedding | reranker | vlm | multimodal | 适用 |
|--------|-----------|----------|-----|-----------|------|
| 标准文本 | bge-m3 | bge-reranker | — | off | 纯文字文档 |
| 图文增强 | bge-m3 | bge-reranker | qwen-vl | off | 含图表的 PDF |
| 多模态全量 | jina-v5 | bge-reranker | qwen-vl | **on** | 图片库+跨模态 |
| 轻量快速 | text2vec | — | — | off | 低延迟场景 |

### 创建 KB 时

```json
POST /kb/create
{
  "name": "高中数学教材",
  "strategy": "图文增强",
  "metadata": {"subject": "数学", "grade": "高中"},
  "visibility": "shared"
}
```

不传 strategy → 使用 `is_default=true` 的默认策略。

---

## 知识库配置

### KB 字段

| 字段 | 说明 |
|------|------|
| `name` / `description` | 基本信息 |
| `strategy` | 关联的策略模板名 |
| `embedding_model` | 从策略继承，或单独覆盖 |
| `embedding_dim` | 向量维度（由模型决定） |
| `vlm_enabled` | 图片是否过 VLM 生成文字 |
| `multimodal_embed` | 图片是否额外做原生 embed |
| `visibility` | private / shared / public |
| `metadata` | JSON，自定义属性（学科/年级/来源等） |
| `owner` / `tenant_id` | 归属 |

### 多级共享

| visibility | 可见范围 |
|-----------|---------|
| `private` | 仅 owner |
| `shared` | 同 tenant 所有用户 |
| `public` | 全平台 |

### 元数据过滤

KB 和 Document 都有 `metadata` JSON 字段。搜索时传 `metadata_filter`：
- DB 层先筛出匹配的 document_ids
- 再传入 Milvus filter 做向量搜索

---

## 记忆系统

### 设计定位

- 检索服务只负责**存储 + 检索**记忆，不负责从对话中提取记忆
- 记忆提取由引擎侧（Go）完成：引擎调 LLM 提取 → 调 `/memory/add` 写入
- `/memory/extract` 作为可选便利接口保留，但推荐引擎侧提取

### 作用域

| scope | 含义 |
|-------|------|
| `global`（默认） | 所有 agent 对话可见 |
| `agent:{agent_id}` | 仅该 agent 对话可见 |

- 写入不传 scope → global
- 检索传 `scopes: ["global", "agent:xxx"]` → 合并全局 + 局部

### 检索排序（多信号融合）

Dense-only 向量检索 + 四信号加权融合：

```
final_score = semantic × 0.5 + recency × 0.2 + importance × 0.2 + frequency × 0.1
```

| 信号 | 权重 | 归一化 | 含义 |
|------|------|--------|------|
| semantic | 0.5 | Milvus COSINE 直接返回 (0~1) | 语义相关度 |
| recency | 0.2 | 指数衰减，半衰期 30 天 | 越新得分越高 |
| importance | 0.2 | importance / 10（写入时指定 1-10） | 重要度 |
| frequency | 0.1 | log(1+access_count) / log(1+50) | 常被检索的记忆权重高 |

为什么不用 BM25：记忆是短 fact（10-30 字），TF-IDF 对超短文本几乎无区分度。

---

## Milvus Schema

### KB Collection: `lea_knowledge_v2`

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | VARCHAR(65535) | 文本（BM25 自动索引） |
| `dense_vector` | FLOAT_VECTOR | 语义向量（维度由 KB 模型决定） |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | BM25 自动生成 |
| `kb_id` | INT64 | 知识库 ID |
| `document_id` | INT64 | 文档 ID |
| `section_id` | INT64 | 章节 ID |
| `tenant_id` | VARCHAR(64) | 租户 |
| `chunk_type` | VARCHAR(20) | text / image_caption / table / audio_transcript |
| `source_filename` | VARCHAR(300) | 文件名 |
| `source_url` | VARCHAR(500) | 原始资源 MinIO key（图片/音频/文档） |
| `page` | INT32 | 页码 |

### Memory Collection: `lea_user_memories`

| 字段 | 类型 | 说明 |
|------|------|------|
| `memory_id` | INT64 | 关联 DB 主键 |
| `user_id` | VARCHAR(64) | 用户 |
| `tenant_id` | VARCHAR(64) | 租户 |
| `category` | VARCHAR(32) | 分类 |
| `scope` | VARCHAR(100) | global / agent:xxx |
| `content` | VARCHAR(2048) | 内容 |
| `embedding` | FLOAT_VECTOR | 语义向量 |

---

## API 参数速查

> 基础地址：`http://retrieval:8300`

### 知识库 — 检索

```
POST /kb/search
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `query` | string | ✅ | — | 检索关键词（min_length=1） |
| `top_k` | int | — | 5 | 返回结果数 |
| `kb_ids` | int[] | — | null | 指定知识库 ID；null 则按 visibility 自动过滤 |
| `document_ids` | int[] | — | null | 精确到文档级过滤 |
| `tenant_id` | string | — | "" | 租户 ID |
| `user_id` | string | — | "" | 用户 ID（用于 visibility 过滤） |
| `strategy` | string | — | "hybrid" | hybrid / semantic / keyword |
| `metadata_filter` | object | — | null | DB 层 JSON 过滤（如 `{"subject":"数学"}`) |

**返回** `SearchHitOut[]`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | string | chunk 文本 |
| `score` | float | 融合分数 |
| `kb_id` | string | 知识库 ID |
| `document_id` | string | 文档 ID |
| `section_id` | int | 章节 ID（0=无） |
| `section_title` | string | 章节标题 |
| `page` | int | 页码 |
| `chunk_type` | string | text / image_caption / table / audio_transcript |
| `source_filename` | string | 文件名 |
| `source_url` | string | 原始资源访问地址（MinIO presigned URL） |

---

### 知识库 — CRUD

```
POST /kb/create
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `name` | string | ✅ | — | 知识库名称（1-200 字） |
| `description` | string | — | "" | 描述 |
| `strategy` | string | — | null | 策略模板名；null 用 is_default 模板 |
| `owner` | string | — | "" | 所属用户 |
| `tenant_id` | string | — | "default" | 租户 |
| `visibility` | string | — | "private" | private / shared / public |
| `embedding_model` | string | — | 从策略继承 | 覆盖策略的 embedding 模型 |
| `embedding_dim` | int | — | 从策略继承 | 向量维度 |
| `multimodal` | bool | — | false | 是否开启多模态 embed |
| `metadata` | object | — | {} | 自定义属性 JSON |

```
GET /kb/list?tenant_id=default&owner=
```

```
GET /kb/{kb_id}/config
PUT /kb/{kb_id}/config
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `retrieval_mode` | string | hybrid / semantic / keyword |
| `retrieval_top_k` | int | 默认检索条数 |
| `score_threshold` | float | 最低分数阈值 |
| `visibility` | string | private / shared / public |
| `enabled` | bool | 是否启用 |
| `name` | string | 改名 |
| `description` | string | 改描述 |

---

### 知识库 — 文档管理

```
POST /kb/{kb_id}/upload
Content-Type: multipart/form-data
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `file` | File | ✅ | — | 文件（pdf/txt/md/docx） |
| `tenant_id` | string(Form) | — | "default" | 租户 |
| `metadata` | string(Form) | — | "{}" | JSON 字符串，文档元数据 |

**返回** `DocumentOut`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 文档 ID |
| `kb_id` | int | 所属知识库 |
| `filename` | string | 文件名 |
| `file_type` | string | pdf / txt / md / docx |
| `status` | string | processing / completed / failed |
| `chunk_count` | int | 分块数量 |
| `error_message` | string | 失败原因 |
| `metadata` | object | 文档元数据 |

```
GET  /kb/{kb_id}/documents          列出文档
GET  /kb/{kb_id}/documents/{doc_id} 文档详情
DELETE /kb/{kb_id}/documents/{doc_id} 删除文档+向量
```

---

### 知识库 — 章节树

```
POST /kb/sections
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `document_id` | int | ✅ | 文档 ID |

**返回** `SectionNode[]`（嵌套树）：

```json
[{
  "id": 1, "title": "第一章", "level": 1, "page_start": 1,
  "children": [
    {"id": 2, "title": "1.1 概述", "level": 2, "page_start": 1, "children": []}
  ]
}]
```

---

### 记忆 — 写入

```
POST /memory/add
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `user_id` | string | ✅ | — | 用户 ID |
| `content` | string | ✅ | — | 记忆内容 |
| `category` | string | — | "fact" | 分类：fact / preference / experience / skill |
| `importance` | int | — | 5 | 重要度 1-10 |
| `tenant_id` | string | — | "default" | 租户 |
| `source_session_id` | string | — | "" | 来源对话 ID |
| `scope` | string | — | "global" | global / agent:{agent_id} |

**返回** `{"id": 123}`

---

### 记忆 — 检索

```
POST /memory/search
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `user_id` | string | ✅ | — | 用户 ID |
| `query` | string | ✅ | — | 语义检索关键词 |
| `top_k` | int | — | 10 | 返回条数 |
| `tenant_id` | string | — | "default" | 租户 |
| `scopes` | string[] | — | null | 过滤作用域；null=全部，如 `["global","agent:tutor"]` |

**返回** `MemoryOut[]`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 记忆 ID |
| `content` | string | 内容 |
| `category` | string | 分类 |
| `importance` | int | 重要度 |
| `access_count` | int | 被检索次数 |
| `score` | float | 多信号融合分 |
| `scope` | string | 作用域 |
| `created_at` | string | 创建时间 ISO |

---

### 记忆 — 其他 CRUD

```
GET    /memory/list?user_id=&tenant_id=default&category=
PUT    /memory/{memory_id}    body: {"content": "...", "category": "..."}
DELETE /memory/{memory_id}
DELETE /memory/user/{user_id}?tenant_id=default
```

---

### 记忆 — 对话提取（可选便利接口）

> **推荐做法**：引擎侧（Go）自行调 LLM 从对话中提取记忆，然后逐条调 `POST /memory/add` 写入。
> 这样检索服务保持"无 LLM 调用"原则，且引擎有完整对话上下文提取质量更高。
>
> 下面的 `/memory/extract` 作为便利接口保留——适合快速接入场景，但会在服务端触发 LLM 调用。

```
POST /memory/extract
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `user_id` | string | ✅ | 用户 ID |
| `messages` | object[] | ✅ | `[{"role":"user","content":"..."},...]` |
| `tenant_id` | string | — | 租户 |
| `session_id` | string | — | 关联会话 ID |

**返回** `{"extracted": 2, "memories": [...]}`

---

### Admin — 模型注册（P1 待实现）

```
POST   /admin/retrieval/models       注册模型
GET    /admin/retrieval/models        列出所有模型
PUT    /admin/retrieval/models/{id}   更新模型
DELETE /admin/retrieval/models/{id}   删除模型
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | ✅ | 模型标识（如 bge-m3） |
| `model_type` | string | ✅ | embedding / vlm / asr / reranker |
| `api_base` | string | ✅ | 服务地址 |
| `model_name` | string | — | 传给服务的 model 参数 |
| `dimension` | int | — | 向量维度（embedding 类型必填） |
| `supports` | string[] | — | 能力声明：text / image / audio / video |
| `enabled` | bool | — | 是否启用（默认 true） |

---

### Admin — 策略模板（P1 待实现）

```
POST   /admin/retrieval/strategies       创建策略
GET    /admin/retrieval/strategies        列出策略
PUT    /admin/retrieval/strategies/{id}   更新策略
DELETE /admin/retrieval/strategies/{id}   删除策略
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `name` | string | ✅ | — | 模板名（如"标准文本"） |
| `embedding_model` | string | ✅ | — | 关联的 embedding 模型名 |
| `reranker_model` | string | — | null | 精排模型名；null=不用 reranker |
| `vlm_model` | string | — | null | VLM 模型名；null=图片不做描述 |
| `retrieval_mode` | string | — | "hybrid" | 默认检索模式 |
| `multimodal_embed` | bool | — | false | 是否对图片做原生 embed |
| `top_k` | int | — | 5 | 默认返回条数 |
| `score_threshold` | float | — | 0.5 | 最低分数阈值 |
| `is_default` | bool | — | false | 是否为默认策略 |

---

## 待实现

| 优先级 | 项目 | 状态 |
|--------|------|------|
| P0 | PDF 解析 (MinerU → chunk → embed → Milvus) | ✅ |
| P0 | 双路检索 (Dense + BM25 + RRF) | ✅ |
| P0 | Reranker 精排 (BGE + 熔断) | ✅ |
| P0 | 知识库 CRUD + 多级共享 + metadata 过滤 | ✅ |
| P0 | Celery 异步文档处理 | ✅ |
| P0 | 记忆系统 (CRUD + scope + 语义检索) | ✅ |
| **P1** | **模型注册 + 策略模板 Admin API** | 待做 |
| **P1** | **VLM 接入（图片 → 文字描述）** | 待做 |
| **P1** | **DOCX/PPTX 解析器** | 待做 |
| P2 | ASR 接入（音视频 → 文字） | 待做 |
| P2 | 多模态 embed 增强 | 已有骨架 |
| P2 | 视频关键帧 + 分段处理 | 待做 |

> **PageIndex** 已从检索服务中移出，作为独立服务设计（单独文档）。
> 定位：LLM 递归摘要树索引，agent 可按需调用，不在检索热路径上。
