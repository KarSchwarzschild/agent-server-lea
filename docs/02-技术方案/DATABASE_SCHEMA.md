# LEA Platform · 数据库表结构总览

生成日期：2026-07-06
对应版本：Ph1 · Skill v2 定稿（#20 Step 1-4 完成后）

数据库：MySQL 8.0（生产 / dev）· 兼容 SQLite（测试）
字符集：`utf8mb4` / `utf8mb4_unicode_ci`

---

## 1. 总览

共 **18 张表**，按业务域分五组：

| 组 | 表数 | 表清单 |
|---|---|---|
| 平台基础 | 4 | `users`, `roles`, `user_roles`, `tenant_configs` |
| 会话 | 4 | `chat_sessions`, `chat_messages`, `user_memories`, `chat_attachments` |
| 知识库 | 3 | `knowledge_bases`, `kb_documents`, `kb_document_chunks` |
| Skill | 4 | `skills`, `skill_resources`, `skill_versions`⚠, `skill_test_runs`⚠ |
| Agent + 绑定 | 3 | `agents`, `agent_skill_bindings`, `agent_kb_bindings` |

⚠ = Ph1 只建表不写业务代码（骨架，Ph1 后段激活）

**多租户机制**：绝大多数业务表继承 `TenantMixin`（`tenant_id VARCHAR(50) NOT NULL INDEX DEFAULT 'default'`），配合 SQLAlchemy event hook 自动追加 `WHERE tenant_id = ?`。少数关联子表（如 `kb_document_chunks`、`chat_messages`）**不带** tenant_id —— 通过父表 join 实现隔离。

**建表方式**：`Base.metadata.create_all()` 启动时 `CREATE TABLE IF NOT EXISTS`。修改列需手动 DDL（无 Alembic）。

---

## 2. ERD 简图

```
users ─┬─ user_roles ─┐
       │              │
       │              roles
       │
       └─→ tenant_configs (via tenant_id)
                │
                ├─→ knowledge_bases ─→ kb_documents ─→ kb_document_chunks
                │
                ├─→ chat_sessions
                │      ├─→ chat_messages (session_id)
                │      └─→ chat_attachments
                │
                ├─→ user_memories (user_id, tenant 隐式）
                │
                ├─→ skills ─┬─→ skill_resources
                │           ├─→ skill_versions⚠
                │           └─→ skill_test_runs⚠
                │
                └─→ agents ─┬─→ agent_skill_bindings ─→ skills
                            └─→ agent_kb_bindings ─→ knowledge_bases
```

---

## 3. 平台基础

### 3.1 `users` — 用户账号
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | INT | PK auto | |
| user_id | VARCHAR(100) | UNIQUE INDEX | 业务 ID（如 `user_abc123`） |
| username | VARCHAR(100) | UNIQUE INDEX | 登录名 |
| tenant_id | VARCHAR(50) | INDEX default='default' | 租户 |
| password_hash | VARCHAR(255) | | bcrypt |
| email | VARCHAR(200) | default='' | |
| avatar | VARCHAR(500) | default='' | |
| phone | VARCHAR(20) | default='' | |
| is_active | BOOL | default=1 | |
| last_login_at | DATETIME | nullable | |
| last_login_ip | VARCHAR(50) | default='' | |
| created_at / updated_at | DATETIME | now / onupdate | |

- 关系：`roles` M:N（via `user_roles`）
- 未继承 `TenantMixin`，但显式含 `tenant_id` 列

### 3.2 `roles` — 角色
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | INT | PK auto | |
| code | VARCHAR(50) | UNIQUE | `admin` / `user` |
| name | VARCHAR(100) | | 显示名 |
| description | VARCHAR(500) | default='' | |
| is_system | BOOL | default=0 | 系统角色不可删除 |
| is_active | BOOL | default=1 | |
| created_at | DATETIME | now | |

启动 seed：`admin`（管理员）+ `user`（普通用户）

### 3.3 `user_roles` — 用户角色关联（M:N 中间表）
| 字段 | 类型 | 约束 |
|---|---|---|
| user_id | INT | PK, FK → users.id |
| role_id | INT | PK, FK → roles.id |

### 3.4 `tenant_configs` — 租户配置
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| tenant_id | VARCHAR(50) | **PK** | 主键即租户 ID |
| display_name | VARCHAR(100) | default='' | |
| llm_model | VARCHAR(50) | default='qwen-max-latest' | 该租户默认模型 |
| kb_id | VARCHAR(100) | default='' | 默认知识库（旧路径，Ph2 改绑 agent） |
| rpm_limit | INT | default=20 | 每分钟请求上限 |
| daily_token_limit | INT | default=100_000 | 日 token 配额 |
| monthly_token_limit | INT | default=3_000_000 | 月 token 配额 |
| is_active | INT | default=1 | |
| created_at / updated_at | DATETIME | now / onupdate | |

---

## 4. 会话

### 4.1 `chat_sessions` — 对话会话（TenantMixin）
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | VARCHAR(64) | **PK**，default=UUID | session_id / LangGraph thread_id |
| tenant_id | VARCHAR(50) | INDEX default='default' | |
| user_id | VARCHAR(64) | INDEX NOT NULL | |
| title | VARCHAR(200) | default='新对话' | |
| app_name | VARCHAR(50) | default='chat' | 归属 App |
| message_count | INT | default=0 | |
| is_archived | BOOL | default=0 | |
| created_at | DATETIME | server_default=now | |
| last_active_at | DATETIME | server_default=now, onupdate | |

### 4.2 `chat_messages` — 消息
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | INT | PK auto | |
| session_id | VARCHAR(64) | INDEX NOT NULL | 无 FK，靠索引 |
| role | VARCHAR(20) | | `user` / `assistant` / `tool` |
| content | TEXT | | |
| token_count | INT | nullable | 用于计费 |
| created_at | DATETIME | server_default=now | |

⚠ **不含 `tenant_id`** —— 通过父表 chat_sessions join 隔离

### 4.3 `user_memories` — 用户长期记忆（跨 session）
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | INT | PK auto | |
| user_id | VARCHAR(64) | INDEX NOT NULL | |
| content | TEXT | NOT NULL | 事实/偏好文本 |
| created_at | DATETIME | server_default=now | |

⚠ **不含 `tenant_id`**（技术债 #18 待修）

### 4.4 `chat_attachments` — 附件（TenantMixin）
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | INT | PK auto | |
| tenant_id | VARCHAR(50) | INDEX default='default' | |
| user_id | VARCHAR(50) | INDEX NOT NULL | |
| session_id | VARCHAR(100) | INDEX default='' | |
| filename | VARCHAR(300) | NOT NULL | |
| file_key | VARCHAR(500) | NOT NULL | MinIO 对象 key |
| file_type | VARCHAR(20) | default='' | pdf/docx/... |
| file_size | INT | default=0 | 字节 |
| media_type | VARCHAR(20) | default='text' | text/image/audio/video |
| extracted_text | TEXT | default='' | 解析后的纯文本 |
| created_at | DATETIME | now | |

---

## 5. 知识库

### 5.1 `knowledge_bases` — 知识库容器（TenantMixin）
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | INT | PK auto | |
| tenant_id | VARCHAR(50) | INDEX default='default' | |
| name | VARCHAR(200) | NOT NULL | |
| description | TEXT | default='' | |
| embedding_model | VARCHAR(100) | default='bge-m3' | |
| retrieval_mode | VARCHAR(20) | default='hybrid' | semantic/keyword/hybrid |
| retrieval_top_k | INT | default=5 | |
| score_threshold | FLOAT | default=0.5 | |
| owner | VARCHAR(100) | INDEX NOT NULL | 拥有者 user_id |
| visibility | VARCHAR(20) | INDEX default='private' | private/public |
| document_count | INT | default=0 | 冗余统计 |
| word_count | INT | default=0 | |
| enabled | BOOL | default=1 | |
| created_at / updated_at | DATETIME | now / onupdate | |

### 5.2 `kb_documents` — 文档（TenantMixin）
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | INT | PK auto | |
| tenant_id | VARCHAR(50) | INDEX default='default' | |
| kb_id | INT | INDEX FK → knowledge_bases.id **ON DELETE CASCADE** | |
| filename | VARCHAR(300) | NOT NULL | |
| file_key | VARCHAR(500) | default='' | MinIO 对象 key |
| file_type | VARCHAR(20) | default='pdf' | |
| file_size | INT | default=0 | |
| total_pages | INT | default=0 | |
| parsed_content | TEXT | default='' | Mineru 解析结果 |
| summary | TEXT | default='' | LLM 生成的摘要 |
| key_topics | JSON | default=[] | |
| status | VARCHAR(20) | INDEX default='processing' | processing/success/failed |
| error_message | TEXT | default='' | |
| celery_task_id | VARCHAR(100) | default='' | 异步解析 task id |
| chunk_count | INT | default=0 | |
| word_count | INT | default=0 | |
| created_at / updated_at | DATETIME | now / onupdate | |

### 5.3 `kb_document_chunks` — 向量分块
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | INT | PK auto | |
| document_id | INT | INDEX FK → kb_documents.id **CASCADE** | |
| kb_id | INT | INDEX NOT NULL | 冗余，加速跨库查询 |
| content | TEXT | NOT NULL | chunk 文本 |
| chunk_type | VARCHAR(20) | default='text' | text/image/table |
| position | INT | default=0 | 在文档中的序号 |
| window_content | TEXT | default='' | 滑窗上下文（前后 chunk 拼接） |
| extra | JSON | default={} | 附加元数据 |
| created_at | DATETIME | now | |

⚠ **不含 `tenant_id`**（通过 kb_documents.kb_id join 隔离）
⚠ 向量本身不在 DB —— 存 Milvus，用 `document_id` 关联

---

## 6. Skill 系统（Ph1 · Skill v2 定稿）

skill.md 遵循 **Anthropic Skills 4-key 标准**：`name` / `description`（含 Triggers 子句）/ `license` / `metadata.*`。平台字段全部落 DB。

### 6.1 `skills` — 技能定义（TenantMixin）
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | INT | PK auto | |
| tenant_id | VARCHAR(50) | INDEX default='default' | UNIQUE(tenant_id, name) |
| name | VARCHAR(100) | INDEX NOT NULL | skill 标识（如 `slide-generator`） |
| display_name | VARCHAR(200) | NOT NULL | UI 展示名 |
| description | TEXT | default='' | 含 "Triggers: A, B, C" 子句 |
| version | VARCHAR(20) | default='1.0.0' | |
| category | VARCHAR(50) | INDEX default='general' | |
| system_prompt | TEXT | default='' | SKILL.md body 全文 |
| model_tier | VARCHAR(20) | default='mid' | low/mid/high |
| required_tools | JSON | default=[] | 依赖声明（挂到 agent 时校验），**非**运行时权限 |
| pipeline_class | VARCHAR(200) | default='' | 仅内置 skill；由 `pipeline_registry` 注入 |
| status | VARCHAR(20) | INDEX default='draft' | draft/published/disabled/archived |
| scope | VARCHAR(20) | INDEX default='private' | system/public/private |
| is_builtin | BOOL | INDEX default=0 | |
| created_by | VARCHAR(100) | nullable | |
| created_at / updated_at | DATETIME | now / onupdate | |

**唯一约束**：`UNIQUE (tenant_id, name)` — 内置 skill 走 `tenant_id='default'`，用户 skill 可跨租户重名。

### 6.2 `skill_resources` — 附属资源（references/scripts）
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | INT | PK auto | |
| skill_id | INT | INDEX FK → skills.id **CASCADE** | |
| kind | VARCHAR(20) | NOT NULL | `references` / `scripts` |
| path | VARCHAR(300) | NOT NULL | 相对 skill 根目录的 POSIX 路径 |
| content | TEXT | default='' | 文件内容全文 |
| size | INT | default=0 | 字节 |
| created_at / updated_at | DATETIME | now / onupdate | |

**唯一约束**：`UNIQUE (skill_id, path)`

### 6.3 `skill_versions` ⚠骨架 — 版本快照
| 字段 | 类型 | 约束/默认 |
|---|---|---|
| id | INT | PK auto |
| skill_id | INT | INDEX FK CASCADE |
| version_num | VARCHAR(20) | NOT NULL |
| snapshot | JSON | default={} · skill 全字段快照 |
| change_log | TEXT | default='' |
| created_by | VARCHAR(100) | nullable |
| created_at | DATETIME | now |

`UNIQUE (skill_id, version_num)`

### 6.4 `skill_test_runs` ⚠骨架 — 测试运行记录
| 字段 | 类型 | 约束/默认 |
|---|---|---|
| id | INT | PK auto |
| skill_id | INT | INDEX FK CASCADE |
| version_num | VARCHAR(20) | nullable |
| input | JSON | default={} · user message + context |
| output | JSON | default={} · final assistant message |
| tool_calls | JSON | default=[] · 调用序列 |
| status | VARCHAR(20) | INDEX default='success' · success/error/timeout |
| duration_ms | INT | default=0 |
| error_message | TEXT | default='' |
| created_by | VARCHAR(100) | nullable |
| created_at | DATETIME | now |

---

## 7. Agent + 绑定

### 7.1 `agents` — Agent 定义（TenantMixin）
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | INT | PK auto | |
| tenant_id | VARCHAR(50) | INDEX default='default' | UNIQUE(tenant_id, name) |
| name | VARCHAR(100) | INDEX NOT NULL | agent 标识 |
| description | TEXT | default='' | |
| is_system | BOOL | INDEX default=0 | 内置 agent 不可删（如 super-agent） |
| enabled | BOOL | INDEX default=1 | |
| created_by | VARCHAR(100) | nullable | |
| created_at / updated_at | DATETIME | now / onupdate | |

Ph1 只 6 列骨架。#9 Admin 核心将补 `avatar` / `soul_prompt` / `example_questions` 等字段。
启动 seed：`super-agent`（内置）。

### 7.2 `agent_skill_bindings` — Agent 挂载 Skill (M:N)
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | INT | PK auto | |
| agent_id | INT | INDEX FK → agents.id **CASCADE** | |
| skill_id | INT | INDEX FK → skills.id **CASCADE** | |
| priority | INT | default=100 | 数字越小越靠前，路由候选排序用 |
| enabled | BOOL | default=1 | agent 层面开关（skill 全局启用但对此 agent 禁用） |
| created_at / updated_at | DATETIME | now / onupdate | |

`UNIQUE (agent_id, skill_id)`

### 7.3 `agent_kb_bindings` — Agent 挂载 KB (M:N) · 权限白名单 + 默认参数
| 字段 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | INT | PK auto | |
| agent_id | INT | INDEX FK → agents.id **CASCADE** | |
| kb_id | INT | INDEX FK → knowledge_bases.id **CASCADE** | |
| strategy | VARCHAR(20) | default='hybrid' | semantic/keyword/hybrid |
| top_k | INT | default=5 | |
| weight | FLOAT | default=1.0 | 多 KB 加权（Ph2 用） |
| enabled | BOOL | default=1 | |
| created_at / updated_at | DATETIME | now / onupdate | |

`UNIQUE (agent_id, kb_id)`

**双职责**：
1. **权限白名单** — LLM tool_call `search_knowledge(kb_id, ...)` 时 kb_id 必须在此表内，否则拒绝
2. **默认检索参数** — top_k/strategy 未传时读此表默认值

---

## 8. 约定与注意事项

### 8.1 命名
- 表名：`snake_case` 复数（`chat_sessions` / `kb_documents`）；子表带前缀（`kb_*`）
- 主键：`id INT AUTO_INCREMENT PRIMARY KEY`；例外：`chat_sessions.id = UUID`、`tenant_configs.tenant_id`
- 时间戳：`created_at` + `updated_at`（`onupdate=func.now()`）；不用软删
- JSON 字段：MySQL 8.0 原生 JSON；默认值给 `list` / `dict` 保证序列化正常
- 枚举：**不用** SQLAlchemy `Enum` 类型 —— 用 `String(20)` + 应用层 `_VALUES` 元组校验（迁移灵活）

### 8.2 多租户
- 业务表继承 `TenantMixin` 自动加 `tenant_id` 列（INDEX，default `'default'`）
- SQLAlchemy event `before_execute` 自动追加 `WHERE tenant_id = ?`（`enable_tenant_filter`）
- 子表（`chat_messages` / `kb_document_chunks`）**故意不带** tenant_id → 靠父表 join
- 例外 & 技术债：`user_memories` 缺 tenant_id（#18 待修）

### 8.3 建表 & 迁移
- 启动时 `Base.metadata.create_all(engine)` = `CREATE TABLE IF NOT EXISTS`
- **修改现有表列不生效** — 需手动 `ALTER TABLE` 或 `DROP + CREATE`（skills 表 Step 1 走的后者）
- 没有 Alembic —— Ph2 会引入版本化迁移

### 8.4 Skill 同步策略（Ph1 · Skill v2）
- 启动时扫 `lea_graph/skills/builtin/**/skill.md` → `SkillService.sync_builtins(mode='insert_only')`
- **`insert_only`（默认）**：已存在 → 完全跳过（连 `skill_resources` 都不重刷 → 保护 UI 编辑）
- **`upsert`（管理员手动触发）**：只覆盖 `description / system_prompt / version / pipeline_class`；**永不覆盖** `display_name / category / model_tier / required_tools / status / scope`
- `pipeline_class` **不写在 skill.md**，从 `lea_graph.skills.pipeline_registry.BUILTIN_PIPELINES` 查表注入
