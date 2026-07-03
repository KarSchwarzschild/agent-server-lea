# LEA Platform 开发指南

> 面向开发者的完整参考手册，涵盖架构、开发、调试、部署全流程。
> 最后更新：2026-06-08

---

## 一、项目概览

LEA Platform 是终身教育 AI Agent 平台，采用 Python monorepo + Vue3 前端架构。

### 技术栈

| 层 | 技术 |
|---|---|
| Agent 框架 | LangChain + LangGraph |
| 后端 API | FastAPI + SQLAlchemy |
| 异步任务 | Celery + Redis |
| 向量检索 | Milvus + BGE-M3 |
| 前端 | Vue3 + Naive UI + Vite |
| 包管理 | uv (Python) / pnpm (前端) |
| 容器化 | Docker Compose |
| 对象存储 | MinIO |
| 消息队列 | Kafka |

---

## 二、Monorepo 结构

```
lea-platform/
├── pyproject.toml              # workspace 根（uv workspace）
├── uv.lock                     # 锁文件
├── .env                        # 本地开发环境变量
│
├── packages/
│   ├── lea-graph/              # Agent 框架层
│   │   └── src/lea_graph/
│   │       ├── llm.py          # LLM 工厂（统一入口，含降级链）
│   │       ├── state.py        # LangGraph State 定义
│   │       ├── orchestrator/   # Supervisor + Router
│   │       ├── skills/         # Skill 框架（schema/executor/runner/pipeline）
│   │       ├── tools/          # 公共 Tools（kb_search 等）
│   │       ├── services/       # 服务层（knowledge_base 检索入口）
│   │       ├── infra/          # 基础设施（resilience/tracing/logging/rate_limiter）
│   │       ├── sandbox/        # 代码沙箱
│   │       ├── mcp/            # MCP 协议适配
│   │       └── harness/        # 评估框架（LLM Judge）
│   │
│   ├── lea-server/             # 服务层（FastAPI）
│   │   └── src/lea_server/
│   │       ├── main.py         # FastAPI app 入口
│   │       ├── database.py     # SQLAlchemy engine/session
│   │       ├── api/            # 路由（chat/admin/knowledge/user_skills）
│   │       ├── models/         # ORM 模型（User/Role/Skill/Knowledge）
│   │       ├── services/       # 业务服务（kb_service/skill_service/object_storage）
│   │       ├── core/           # 核心（deps/tenant/auth）
│   │       ├── tasks/          # Celery 任务（kb_process）
│   │       ├── engine/         # SSE 引擎
│   │       └── events/         # Kafka 事件
│   │
│   ├── lea-rag/                # RAG 引擎
│   │   └── src/lea_rag/
│   │       ├── index/          # MilvusRAGIndex（混合检索）
│   │       ├── retrieval/      # HybridRetriever + Reranker
│   │       ├── embeddings/     # BGEEmbedding（自部署 BGE-M3）
│   │       └── academic/       # 学术搜索
│   │
│   ├── lea-apps/               # 可插拔应用
│   │   └── src/lea_apps/
│   │       ├── chat/           # 智能对话 App
│   │       ├── notebook/       # 智能笔记 App
│   │       ├── research/       # 文献综述 App
│   │       └── _example/       # App 开发模板
│   │
│   └── lea-data/               # 数据层（预留）
│
├── frontend/                   # Vue3 前端
│   └── src/
│       ├── shell/              # AppShell 布局
│       ├── chat/               # 智能对话页面
│       ├── notebook/           # 智能笔记页面
│       ├── knowledge/          # 知识库管理页面
│       ├── research/           # 文献综述页面
│       ├── admin/              # 管理后台（用户/技能/知识库）
│       ├── router/             # 路由
│       ├── stores/             # Pinia stores
│       └── utils/              # request.ts 等工具
│
├── infra/                      # 基础设施
│   ├── docker-compose.dev.yml  # 本地开发（所有服务）
│   └── docker-compose.yml      # 生产部署
│
└── scripts/                    # 项目工具脚本
```

### 包依赖关系

```
lea-server → lea-apps → lea-rag → lea-graph
                          ↑
                     (workspace deps)
```

---

## 三、数据库 Schema

### users 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增 |
| user_id | VARCHAR(100) | 业务 ID（唯一） |
| username | VARCHAR(100) | 用户名（唯一） |
| tenant_id | VARCHAR(50) | 租户 ID |
| password_hash | VARCHAR(255) | BCrypt 密码 |
| email, avatar, phone | VARCHAR | 基本信息 |
| is_active | BOOL | 是否启用 |

### roles 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增 |
| code | VARCHAR(50) | 角色编码（admin/user） |
| name | VARCHAR(100) | 角色名 |
| is_system | BOOL | 系统角色不可删 |

### user_roles 表（多对多）
| user_id | role_id |

### skills 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增 |
| name | VARCHAR(100) | 唯一标识 |
| display_name | VARCHAR(200) | 显示名 |
| execution_mode | VARCHAR(20) | react / pipeline |
| scope | VARCHAR(20) | system / public / private |
| is_builtin | BOOL | 内置 Skill |
| trigger_keywords | JSON | 触发关键词 |
| allowed_tools | JSON | 允许使用的 Tools |
| system_prompt | TEXT | Skill 系统提示词 |
| resources | JSON | 关联资源（模板文件等） |

### knowledge_bases 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增 |
| tenant_id | VARCHAR(50) | 租户 |
| name | VARCHAR(200) | 知识库名 |
| owner | VARCHAR(100) | 创建者 user_id |
| visibility | VARCHAR(20) | private / public |
| embedding_model | VARCHAR(100) | 默认 bge-m3 |
| retrieval_mode | VARCHAR(20) | hybrid / vector / fulltext |
| retrieval_top_k | INT | 默认 5 |
| document_count | INT | 文档数 |

### kb_documents 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增 |
| kb_id | INT FK | 所属知识库 |
| tenant_id | VARCHAR(50) | 租户 |
| filename | VARCHAR(300) | 文件名 |
| file_key | VARCHAR(500) | MinIO 存储路径 |
| status | VARCHAR(20) | processing / ready / error |
| chunk_count | INT | 分块数 |

### kb_document_chunks 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增 |
| document_id | INT FK | 所属文档 |
| kb_id | INT | 知识库 ID（冗余，加速查询） |
| content | TEXT | 分块文本 |
| chunk_type | VARCHAR(20) | text / image / table |
| position | INT | 顺序号 |
| extra | JSON | 附加元数据 |

### chat_sessions 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(64) PK | session_id (UUID) |
| user_id | VARCHAR(64) | 所属用户 |
| tenant_id | VARCHAR(50) | 租户 ID |
| title | VARCHAR(200) | 对话标题（首条消息截取） |
| app_name | VARCHAR(50) | 默认 'chat' |
| message_count | INT | 消息计数 |
| is_archived | BOOL | 软删除 |
| created_at / last_active_at | DATETIME | 时间戳 |

### chat_messages 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增 |
| session_id | VARCHAR(64) | 所属会话 |
| role | VARCHAR(20) | user / assistant |
| content | TEXT | 消息内容 |
| token_count | INT NULL | token 消耗（user=input_tokens, assistant=output_tokens） |
| created_at | DATETIME | 时间戳 |

### user_memories 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT PK | 自增 |
| user_id | VARCHAR(64) | 所属用户 |
| content | TEXT | 记忆内容（用户事实） |
| created_at | DATETIME | 提取时间 |

---

## 四、核心机制

### 4.0 Supervisor 编排图（ReAct 动态规划）

整体执行流：

```
START → summarize → context_inject → router → executor → memory_extract → END
```

- **summarize** — langmem SummarizationNode，消息 >6000 token 时自动压缩
- **context_inject** — 加载用户长期记忆(MySQL)，递增 turn_count，checkpoint 过期时从 MySQL 恢复历史
- **router** — LLM 语义路由，从 Skill/Agent 候选中选择；支持 ask_user 续接检测
- **executor** — 三种：
  - `fallback_qa` — 带 show_plan/ask_user 工具的 ReAct agent（GPT/Coze 风格）
  - `skill_executor` — 根据 Skill 的 execution_mode 选择 ReAct Agent 或 Pipeline
  - `{app}__{agent}` — App 内置 Agent
- **memory_extract** — 每 5 轮用 LLM 提取用户事实，去重后存入 MySQL

关键设计：
- 没有固定 Planner 节点 — LLM 通过 `show_plan` tool 自主展示计划
- ask_user 续接 — router 检测上一轮有 ask_user 时，用 LLM 判断回复是 reply 还是 new_topic
- Redis 仅做运行时 Checkpointer — 过期后从 MySQL 恢复

内置工具 (builtin_tools.py)：

| 工具 | 功能 | 触发场景 |
|------|------|---------|
| `show_plan` | 向前端推送执行计划 | 复杂任务 |
| `ask_user` | 向用户追问 | 信息不足时 |

### 4.0.1 记忆系统

数据流：
- Redis Checkpointer — LangGraph 运行时 state（有 TTL，过期自动从 MySQL 恢复）
- MySQL user_memories — 跨 session 持久事实
- MySQL chat_messages — 消息永久保存

记忆提取：每 5 轮 → LLM 提取事实 → bigram Jaccard 去重 → INSERT（只增不改）
记忆注入：context_inject_node → 加载最近 20 条 → SystemMessage 注入 LLM

### 4.0.2 Token 追踪

- `get_chat_model()` 自动挂载 `_TokenCallback`（stream_usage=True）
- streaming.py 累加 AIMessageChunk.usage_metadata
- `done` SSE 事件携带 `usage: {input_tokens, output_tokens, total_tokens}`
- chat.py 流结束后写入 chat_messages.token_count
- `@track_tokens` 装饰器写入 Redis 配额

### 4.0.3 会话管理

- `POST /api/chat/sessions` — 创建
- `GET /api/chat/sessions` — 列表
- `GET /api/chat/sessions/{id}/messages` — 历史消息
- 每轮自动：_touch_session + _save_messages

### 4.1 SSE 事件协议

对话流式输出通过 `stream_graph_as_sse()` 适配，支持两种流事件：

| 事件 | 来源 | 数据 |
|------|------|------|
| `thinking` | router | `{content: "正在分析..."}` |
| `intent` | router | `{skill/app, confidence}` |
| `content` | LLM token | `{text: "..."}` |
| `plan_created` | show_plan tool | `{plan: [{step, task, status}]}` |
| `ask_user` | ask_user tool | `{question: "..."}` |
| `slide` | slide-generator | `{index, svg, title}` |
| `progress` | pipeline | `{content: "..."}` |
| `error` | 异常 | `{message: "..."}` |
| `done` | 结束 | `{status, usage: {input_tokens, output_tokens, total_tokens}}` |

### 4.2 LLM 降级链

```
get_chat_model(purpose="chat") 返回带 fallback 的模型：
  在线 API (tenant_api_base) → 本地模型 (local_api_base) → 异常
```

配置：
```bash
LEA_TENANT_API_BASE=https://tenantapi-ai.ouchn.edu.cn  # 在线
LEA_LOCAL_API_BASE=http://202.205.160.8:11434          # 本地 Ollama
LEA_LOCAL_MODEL=qwen3:32b
```

使用 LangChain `.with_fallbacks()` 原生支持，流式输出兼容。

### 4.3 Skill 框架

- **Tool**（原子操作）：单个函数，如 `kb_search`、`web_search`
- **Skill**（编排逻辑）：DB 驱动，两种执行模式
  - `react`：构建 ReAct Agent，LLM 自主调用 Tools
  - `pipeline`：预定义执行流，适合确定性任务

触发方式：
1. 前端 + 按钮面板 / @ 搜索 / toolbar 显式选择
2. Router LLM 语义路由自动匹配

### 4.4 知识库检索

```
用户查询 → kb_search Tool
  → search_knowledge_base(kb_ids/document_ids/自动可见)
    → HybridRetriever(metadata_filters=[kb_id IN [...]])
      → Milvus hybrid_search (dense BGE-M3 + sparse BM25 + RRF)
        → [Reranker] → top_k 结果
```

智能对话：自动搜索用户可见的所有知识库
智能笔记：用户选择具体知识库/文档

### 4.5 文档处理 Pipeline

```
上传 → MinIO 存储 → Celery 异步任务
  → MinerU/PyMuPDF 解析 → 分块 → DB 存储
    → BGE-M3 向量化 → Milvus 索引
```

### 4.6 熔断 + 重试

- `tenacity`：指数退避 + jitter，3 次重试
- `RedisCircuitBreaker`：分布式熔断器，5 次失败 → OPEN → 60s 后 HALF_OPEN → 探测恢复

---

## 五、本地开发

### 5.1 环境准备

```bash
# Python
uv sync                       # 安装所有 workspace 依赖

# 前端
cd frontend && pnpm install

# 启动基础设施
cd infra && docker compose -f docker-compose.dev.yml up -d
```

### 5.2 端口约定

| 服务 | 端口 |
|------|------|
| 后端 API | 8000 |
| 前端 (Vite) | 5176 |
| MySQL | 3307 (映射) |
| Redis | 6379 |
| MinIO | 9000 (API) / 9001 (Console) |
| Milvus | 19530 |
| Kafka | 9092 |

### 5.3 启动命令

```bash
# 方式一：Docker 全包（推荐）
cd infra && docker compose -f docker-compose.dev.yml up -d

# 方式二：本地启动后端（适合频繁调试）
cd packages/lea-server
uv run uvicorn lea_server.main:app --reload --port 8000

# 前端
cd frontend && pnpm dev
```

### 5.4 环境变量

所有变量在 `.env` 中配置，LEA_ 前缀的由 `LLMSettings` 读取：

| 变量 | 用途 |
|------|------|
| DB_HOST/PORT/USER/PASSWORD/NAME | MySQL 连接 |
| REDIS_URL | Redis 连接 |
| LEA_TENANT_API_BASE | 在线 LLM API |
| LEA_TENANT_API_KEY | 在线 API Key |
| LEA_LOCAL_API_BASE | 本地模型（降级） |
| LEA_MODEL_CHAT | 对话模型 |
| LEA_MODEL_ROUTING | 路由模型 |
| MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY | MinIO |

---

## 六、调试指南

### 6.1 后端日志

```bash
docker logs lea-backend-dev -f --tail 50    # FastAPI 日志
docker logs lea-celery-worker-dev -f        # Celery worker 日志
```

使用 `structlog` 输出结构化 JSON，关键日志标签：
- `llm_retry` — 模型重试
- `model_circuit_open` — 熔断器打开
- `fallback_succeeded` — 降级成功
- `hybrid_retrieved` — 检索完成
- `document_processed` — 文档处理完成

### 6.2 API 调试

```bash
# 登录获取 token
curl -s http://localhost:8000/api/auth/login \
  -X POST -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | jq .token

# 带 token 请求
curl -s http://localhost:8000/api/knowledge-bases \
  -H "Authorization: Bearer <token>" | jq .
```

### 6.3 Milvus 调试

```python
from pymilvus import connections, Collection
connections.connect(uri="http://localhost:19530")
col = Collection("lea_knowledge")
col.num_entities  # 向量数量
```

### 6.4 常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| ImportError: lea_xxx | 依赖未安装 | `docker compose up -d --force-recreate backend` |
| Milvus dimension mismatch | embedding 维度不匹配 | 删除 collection 重建 |
| 502 Bad Gateway | 后端未启动完成 | 等待 `uv sync` 完成 |
| Celery task stuck | Worker 未连接 Redis | 检查 REDIS_URL 配置 |
| 前端 CORS 错误 | 不能用绝对路径 | baseURL 必须为空字符串 |

---

## 七、部署

### 7.1 服务器信息

| 项 | 值 |
|----|-----|
| IP | 202.205.160.9 |
| 部署目录 | /opt/lifelong-edu-agent |
| 容器名 | lifelong-edu-backend / admin / user |
| 端口 | 40001 / 40002 / 40003 |

### 7.2 部署流程

```bash
# 1. 本地跨平台构建（ARM→AMD64）
docker buildx build --platform linux/amd64 -t lifelong-edu-agent-backend:latest ./

# 2. 导出 + 推送
docker save lifelong-edu-agent-backend -o /tmp/backend.tar
scp /tmp/backend.tar 4pdadmin@202.205.160.9:/tmp/

# 3. 服务器加载 + 重启
ssh 4pdadmin@202.205.160.9
sudo docker load -i /tmp/backend.tar
cd /opt/lifelong-edu-agent && sudo docker-compose up -d --force-recreate
```

### 7.3 GPU 服务器（202.205.160.8）

| 服务 | 端口 | 用途 |
|------|------|------|
| BGE-M3 Embedding | 8012 | 向量化 |
| Reranker | 6006 | 重排序 |
| MinerU | 8001 | 文档解析 |
| Ollama | 11434 | 本地 LLM（降级） |

---

## 八、新增功能开发流程

### 添加新 App

1. 复制 `packages/lea-apps/src/lea_apps/_example/` 为新目录
2. 实现 `AppProtocol` 接口
3. 在 `lea_server/main.py` 注册路由
4. 前端对应创建页面 + 路由

### 添加新 Skill

1. 管理后台 → 技能管理 → 新建
2. 填写名称、描述、触发关键词、允许的 Tools
3. 选择执行模式（react / pipeline）
4. 如果是 pipeline 模式，需要在代码中实现 `SkillPipeline` 子类

### 添加新 Tool

1. 在 `packages/lea-graph/src/lea_graph/tools/` 新建文件
2. 使用 `@tool` 装饰器定义
3. 在 Skill 的 `allowed_tools` 中引用

---

## 九、API 路由总览

| 前缀 | 模块 | 说明 |
|------|------|------|
| /api/auth | 认证 | 登录/注册/token |
| /api/chat | 智能对话 | SSE 流式 |
| /api/apps/notebook | 智能笔记 | 文档+RAG对话 |
| /api/apps/research | 文献综述 | 综述生成 |
| /api/knowledge-bases | 知识库 | 用户 CRUD |
| /api/skills | 技能 | 用户可见技能 |
| /api/admin/* | 管理后台 | 需要 admin 角色 |

---

## 十、前端开发约定

- `baseURL` 必须为空字符串（nginx 代理 `/api/` 到后端）
- 流式接口用原生 `fetch` + `ReadableStream`
- 组件库：Naive UI
- 图标：@vicons/ionicons5
- 状态管理：Pinia
- 请求封装：`src/utils/request.ts`（axios，自动带 token）
