# LEA Platform 管理控制台 PRD

> 版本：v0.2  
> 状态：Backend Implemented / Frontend Pending  
> 日期：2026-10-08  
> 适用范围：当前 LEA Platform，不复用废弃项目 `frontend-admin` 的业务模型

## 1. 背景

LEA Platform 已经具备 Agent、Agent Team、Skill、Tool、MCP Server、知识库、记忆、模型配置、会话与运行记录等能力，但管理能力分散在多个服务中：

- `lea-config`：Agent、Skill、Tool、MCP、Team、检索模型。
- `lea-rag`：知识库、文档、用户记忆、记忆策略。
- `lea-agent`：会话、Agent Run、项目、附件与用户文件。
- `lea-parser`：文档解析用量与配额。
- `lea-gateway`：OIDC 验签、租户选择、角色映射和路由白名单。

改造前只有 Skill 具备完整的“申请发布 -> 管理员审核 -> 通过/驳回”流程。当前后端已经为 Agent、Skill、Tool、MCP Server 和知识库接入统一审批、版本快照、发布生命周期、审计和运营处置能力。

仓库中的旧 `frontend-admin` 来源于其他项目，页面与当前领域模型、接口路径和权限体系不一致。本项目管理控制台应重新定义产品边界和接口契约。

## 2. 产品结论

### 2.1 总体设计

采用：

> 一套领域 API + 两个前端入口 + 独立的管理面业务资源。

用户端和管理控制台共同使用以下领域资源：

```text
/api/agents
/api/teams
/api/skills
/api/tools
/api/mcp-servers
/api/knowledge-bases
```

同一个接口根据已验证的调用者角色、租户、资源归属、资源可见性和操作类型决定返回范围及允许动作。

不为相同资源复制两套 CRUD：

```text
不推荐：/api/user/agents + /api/admin/agents
推荐：  /api/agents
```

管理控制台独有的能力建模为独立资源，而不是“管理员版本 CRUD”：

```text
/api/publication-requests
/api/operations/runs
/api/operations/document-jobs
/api/audit-events
/api/quotas
/api/providers
/api/feedback
```

其中 `/api/feedback` 属于 Phase 2。当前 `lea-data` 只有反馈采集模型和处理组件，尚未形成在线服务，因此第一阶段不通过其他服务复制一份反馈表。

### 2.3 当前实现状态

| 能力 | 状态 | 说明 |
|---|---|---|
| 中性资源路由与角色授权 | 已完成 | Gateway 验证身份并注入 Actor；服务端继续执行租户和对象授权 |
| 五类资源统一发布审批 | 已完成 | Agent、Skill、Tool、MCP Server、知识库 |
| 发布暂停、恢复、归档 | 已完成 | 归档不可恢复；暂停和归档会禁用运行时资源 |
| Tool/MCP 自动检查 | 已完成 | 最新资源版本检查未通过时禁止审批通过 |
| Agent Run 运维 | 已完成 | 列表、详情、安全取消、创建新 Run 重试、同会话并发保护 |
| 文档任务运维 | 已完成 | 列表、详情、安全取消、清理旧分块与向量后重试 |
| 用量与配额 | 已完成 | Parser 同一路径兼容用户 JWT 与 Gateway 管理身份 |
| Provider 管理 | 已完成 | CRUD、连接测试、API Key 不回显 |
| 审计日志 | 已完成 | 发布、Provider、配额、Run、文档任务操作均写入中央审计表 |
| 记忆治理、反馈质量 | Phase 2 | 保持原服务边界，待数据服务形成稳定在线契约后接入 |

### 2.2 兼容策略

当前网关已经公开 `/api/user/agents`、`/api/user/skills` 等路径。第一阶段保留这些路径作为兼容别名，同时新增中性资源路径。新管理控制台只使用中性路径。

服务内部现有的 `/admin/*` 路由不直接作为公共契约。网关只显式映射经过评审的能力，不能开放指向下游 `/admin/*` 的通配代理。

## 3. 目标与非目标

### 3.1 第一阶段目标

1. 管理 Agent、Skill、Tool、MCP Server、知识库及其发布状态。
2. 为五类资源提供统一审批中心。
3. 查看并处置 Agent Run 和知识库文档处理任务。
4. 管理租户级、用户级配额并查看用量。
5. 记录所有高风险管理操作的审计日志。
6. 继续使用外部 OIDC 和租户成员系统作为身份与角色真源。

### 3.2 第一阶段非目标

- 不在 LEA 内维护第二套用户、密码和角色定义。
- 不实现计费、订单、订阅套餐和支付。
- 不实现复杂的自定义 RBAC 权限编辑器。
- 不复制 Grafana、Prometheus 的完整能力。
- 不提供管理员静默查看用户私有内容的能力。
- 不复用旧 `frontend-admin` 的接口层和页面信息架构。

## 4. 角色模型

角色来自外部租户成员系统，由网关映射为以下三类：

| 角色 | 范围 | 主要能力 |
|---|---|---|
| `user` | 当前租户、当前用户 | 管理自己的私有资源，提交发布申请，查看自己的运行与用量 |
| `admin` | 当前租户 | 审核租户级发布，管理租户共享资源，查看租户聚合数据和任务状态 |
| `platform_admin` | 全平台 | 审核全局发布，管理平台公共资源与模型，执行跨租户平台治理 |

### 4.1 授权原则

每个请求必须同时经过：

1. 身份认证：网关验证 OIDC JWT。
2. 租户成员校验：所选租户必须属于当前用户。
3. 角色校验：判断调用者是否具备动作所需角色。
4. 租户隔离：查询必须在数据库层限制租户范围。
5. 对象授权：结合 `owner_scope`、`owner_user_id` 和 `visibility` 判断。
6. 审计记录：高风险动作记录操作者、目标、前后差异和结果。

角色只决定能力上限，不替代对象级授权。租户管理员不能仅凭 `admin` 角色跨租户访问对象。

### 4.2 私有数据访问

- 租户管理员默认不能读取用户私有 Agent、知识库、记忆和会话正文。
- 租户管理员可以看到必要的元数据、聚合统计和风险状态。
- 平台管理员读取私有正文必须走显式的应急访问流程，并产生不可删除的审计事件。
- 跨租户资源不存在或不可见时返回 `404`，避免泄露对象存在性。

## 5. 资源与发布模型

### 5.1 分离四个概念

资源模型必须分别表达：

| 概念 | 示例 | 含义 |
|---|---|---|
| 归属 | `platform` / `user` | 谁可以修改资源 |
| 可见性 | `private` / `tenant` / `global` | 谁可以发现和使用资源 |
| 启用状态 | `enabled` | 资源当前能否被运行时调用 |
| 发布状态 | `draft` / `pending` / `published` 等 | 资源是否完成发布审批 |

不得用单个 `status` 同时表达启停、可见性和审批状态。

### 5.2 统一生命周期

```text
draft/private
      |
      v
   pending --------> rejected
      |
      v
approved/published
      |
      +-----------> suspended
      |
      +-----------> archived
```

状态定义：

| 状态 | 含义 |
|---|---|
| `draft` | 资源可继续编辑，尚未申请扩大可见范围 |
| `pending` | 某个固定版本正在审核 |
| `approved` | 审核通过但尚未完成发布动作时的短暂状态 |
| `published` | 已按目标范围发布 |
| `rejected` | 本次申请被驳回，资源本身仍可编辑 |
| `suspended` | 因风险或故障被管理员暂停使用 |
| `archived` | 不再使用，仅保留历史和审计信息 |

### 5.3 审批触发规则

| 资源 | 私有创建/编辑 | 租户共享 | 全局公开 | 额外检查 |
|---|---|---|---|---|
| Agent | 无人工审批 | 租户管理员审批 | 平台管理员审批 | Prompt、模型、绑定能力、权限范围 |
| Skill | 无人工审批 | 租户管理员审批 | 平台管理员审批 | 包内容、入口、版本、工具声明 |
| Tool | 自动检查通过后启用 | 租户管理员审批 | 平台管理员审批 | Schema、执行类型、网络、凭证、超时 |
| MCP Server | 自动检查通过后启用 | 租户管理员审批 | 平台管理员审批 | SSRF、DNS、凭证、工具清单差异 |
| 知识库 | 无人工审批 | 租户管理员审批 | 平台管理员审批 | PII、版权、文档状态、索引完整性 |

私有 Tool 和 MCP Server 不要求每次人工审批，但必须经过自动安全检查。涉及任意网络访问、代码执行或平台凭证时，可以被策略升级为人工审核。

### 5.4 审批版本快照

发布申请必须指向不可变版本或内容快照。审核期间修改资源时，原申请自动失效或继续绑定旧版本，不能出现“审核 A、发布 B”。

Agent 快照还必须包含绑定的 Skill、Tool、MCP Server 和知识库版本，避免审核后替换依赖绕过审批。

## 6. 功能范围

### 6.1 工作台

- 待我处理的审批数量。
- 失败或长时间运行的 Agent Run。
- 失败的文档解析和索引任务。
- MCP 连接异常和同步失败。
- Token、存储和解析额度预警。
- 最近高风险管理操作。

工作台只用于聚合和跳转，不在第一阶段建设复杂统计大屏。

### 6.2 审批中心

支持：

- 按资源类型、目标范围、申请人、租户、状态和时间筛选。
- 查看资源基本信息、版本差异、依赖关系和自动检查结果。
- 通过、驳回、补充审核意见。
- 批量通过仅限低风险且检查项全部通过的申请。
- 查看完整审批历史。
- 对已发布资源执行暂停、恢复、下架。

审核通过按钮必须展示实际生效范围，例如“发布到当前租户”或“发布到全平台”。

### 6.3 资源中心

#### Agent 与 Agent Team

- 查询、创建、编辑、复制、归档。
- 管理默认 Agent。
- 管理 Team 成员和顺序。
- 管理 Skill、Tool、MCP、知识库绑定。
- 发布前检查依赖资源的可见性和启用状态。
- 查看已发布版本和回滚历史。

#### Skill

- 查询、创建、上传、下载和版本管理。
- 查看包内容摘要和工具声明。
- 提交租户级或全局发布申请。
- 查看驳回原因并基于新版本重新提交。

#### Tool

- 查询、创建、编辑、启停和归档。
- 查看输入 Schema、执行类型和调用目标。
- 执行连通性测试和安全检查。
- 管理凭证引用，不直接返回明文凭证。
- 查看调用成功率、错误率和最近错误。

#### MCP Server

- 查询、创建、编辑、启停和归档。
- 测试连接、同步工具、查看同步差异。
- 对新增、删除或 Schema 变化的工具进行确认。
- 查看连接状态、最后成功时间和错误详情。
- 管理凭证引用和轮换状态。

#### 知识库

- 查询、创建、编辑、启停和归档。
- 管理文档上传、删除和处理状态。
- 对失败文档重新解析或重新索引。
- 查看 Embedding、Reranker、VLM 配置。
- 执行检索测试。
- 发布前检查文档处理完成率和敏感内容扫描结果。

### 6.4 运行与任务

#### Agent Run

- 按租户、用户、Agent、状态和时间筛选。
- 查看运行状态、阶段、耗时、错误和重试次数。
- 对 `queued`、`running`、`cancelling` 状态执行安全取消。
- 对可重试失败创建新 Attempt，不修改原运行记录。
- 查看关联会话、Agent 版本和依赖版本。

默认不向租户管理员展示完整用户 Prompt 和响应正文。

#### 文档任务

- 查看上传、解析、分块、Embedding 和索引阶段。
- 查看失败阶段及可公开的错误信息。
- 支持重试、取消和清理失败任务。
- 识别长时间无进展的任务。

### 6.5 租户、用量和配额

- 租户和成员信息只读展示，数据来自外部租户系统。
- 展示当前用户在租户中的角色和成员状态。
- 配置租户级、用户级 Token、解析、存储和调用配额。
- 按模型、Agent、Tool、用户和日期查看用量。
- 提供额度预警，不在第一阶段实现结算。

### 6.6 模型与平台配置

- 管理 LLM Provider 和可选聊天模型。
- 管理 Embedding、Reranker、VLM、ASR 模型。
- 设置各模型类型的默认项。
- 执行连接测试。
- API Key 仅支持新建、替换和删除，不回显明文。
- 只有平台管理员可以修改全局模型配置。

### 6.7 记忆治理

- 查看租户内记忆数量、分类和同步状态。
- 管理租户或 Agent 级记忆策略。
- 删除特定记忆或执行用户数据清理。
- 默认不展示记忆正文；查看正文属于敏感操作并记录审计。

### 6.8 反馈与质量

- 查看点赞、点踩和用户评论。
- 筛选连续失败、低评分和知识库无命中的会话。
- 将样本加入待评测数据池。
- 标记处理状态和责任人。

第一阶段可先实现查询与标记，不要求建设完整自动评测平台。

### 6.9 审计日志

必须记录：

- 发布申请、通过、驳回、暂停、恢复和下架。
- Tool、MCP、模型和凭证配置变更。
- 配额调整。
- 用户私有数据应急访问。
- 批量删除和数据清理。
- 管理操作失败事件。

每条审计事件至少包含：

```text
event_id
tenant_id
actor_user_id
actor_role
action
resource_type
resource_id
request_id
before_summary
after_summary
reason
result
created_at
```

审计记录只追加，不允许通过业务 API 修改或删除。

## 7. 页面信息架构

```text
工作台

审批中心
  - 待审批
  - 已处理
  - 已发布资源

资源中心
  - Agent
  - Agent Team
  - Skill
  - Tool
  - MCP Server
  - 知识库

运行与任务
  - Agent Run
  - 文档任务

治理
  - 用量与配额
  - 用户记忆
  - 反馈与质量
  - 审计日志

平台设置（仅 platform_admin）
  - 模型与 Provider
  - 全局默认项
```

菜单由后端返回的 capabilities 控制。前端隐藏菜单只改善体验，不能替代服务端授权。

## 8. 低保真原型

### 8.1 审批中心

```text
+-----------------------------------------------------------------------+
| 审批中心                         [资源类型 v] [目标范围 v] [搜索]       |
+--------------------+--------------------------------------------------+
| 待审批 12          | Skill: research-assistant                       |
| 已处理             | 申请人: alice     目标: 当前租户                 |
| 已发布             | 版本: 1.3.0       提交时间: 2026-10-08          |
|                    |--------------------------------------------------|
| [Skill] ...        | 自动检查                                         |
| [MCP] ...          |  [通过] 包结构  [通过] 权限声明  [警告] 网络访问 |
| [Agent] ...        |--------------------------------------------------|
|                    | 版本差异 / 依赖 / 风险 / 历史                    |
|                    |                                                  |
|                    | 审核意见 [_______________________________]       |
|                    |                         [驳回] [通过并发布到租户] |
+--------------------+--------------------------------------------------+
```

### 8.2 资源列表

```text
+-----------------------------------------------------------------------+
| MCP Server                 [状态 v] [可见范围 v] [搜索]   [+ 新建]     |
+-----------------------------------------------------------------------+
| 名称       范围      连接状态    同步时间       发布状态      操作     |
| weather    私有      connected   10:21          draft         ...      |
| school     租户共享  error       昨天           suspended     ...      |
+-----------------------------------------------------------------------+
```

### 8.3 运行处置

```text
+-----------------------------------------------------------------------+
| Agent Run                  [状态 v] [Agent v] [时间 v] [搜索 run_id]   |
+-----------------------------------------------------------------------+
| run_id     Agent      状态       耗时      attempt    最近错误          |
| r-1024     数据助手    failed     38s       2          tool timeout      |
| r-1025     写作助手    running    4m12s     1          -                 |
+-----------------------------------------------------------------------+
| 详情: 时间线 / 工具调用 / 检查点 / 关联版本             [取消] [重试] |
+-----------------------------------------------------------------------+
```

## 9. 目标 API 契约

### 9.1 领域资源

```text
GET    /api/agents
POST   /api/agents
GET    /api/agents/{id}
PUT    /api/agents/{id}
DELETE /api/agents/{id}

GET    /api/skills
POST   /api/skills
GET    /api/skills/{id}
PUT    /api/skills/{id}
DELETE /api/skills/{id}

GET    /api/tools
POST   /api/tools
GET    /api/tools/{id}
PUT    /api/tools/{id}
DELETE /api/tools/{id}

GET    /api/mcp-servers
POST   /api/mcp-servers
GET    /api/mcp-servers/{id}
PUT    /api/mcp-servers/{id}
DELETE /api/mcp-servers/{id}

GET    /api/knowledge-bases
POST   /api/knowledge-bases
GET    /api/knowledge-bases/{id}
PUT    /api/knowledge-bases/{id}
DELETE /api/knowledge-bases/{id}
```

同一路径根据 Actor 计算数据范围。响应中的 `allowed_actions` 用于前端展示，但服务端仍必须独立校验每次写操作。

示例：

```json
{
  "id": 42,
  "name": "research-assistant",
  "visibility": "private",
  "enabled": true,
  "publication_status": "draft",
  "allowed_actions": ["read", "update", "delete", "submit_publication"]
}
```

### 9.2 发布审批

以下 `/api/publication-requests` 是**管理后台接口**，服务端统一要求
`admin` 或 `platform_admin`。普通用户不能通过它读取审批队列、审计详情或执行审核。

普通用户只通过资源侧接口提交或撤回自己的申请：

```text
POST /api/skills/{id}/publication-requests
POST /api/skills/{id}/publication-requests/{request_id}/cancel
```

这样“用户申请发布”和“管理员审批发布”属于同一领域模型，但不属于同一个后台权限面。

```text
GET  /api/publication-requests
GET  /api/publication-requests/{id}
POST /api/publication-requests/{id}/review
POST /api/publication-requests/{id}/cancel
POST /api/publications/{resource_type}/{resource_id}/suspend
POST /api/publications/{resource_type}/{resource_id}/resume
POST /api/publications/{resource_type}/{resource_id}/archive
```

提交申请：

```json
{
  "target_scope": "tenant",
  "resource_version": "1.3.0",
  "note": "供本租户课程设计使用"
}
```

审核：

```json
{
  "decision": "approved",
  "note": "自动检查通过，依赖均为租户共享版本"
}
```

### 9.3 自动检查

```text
POST /api/tools/{id}/checks
POST /api/mcp-servers/{id}/checks
POST /api/mcp-servers/{id}/sync
GET  /api/resources/{resource_type}/{resource_id}/checks
```

检查结果应结构化返回，不以日志字符串作为前端契约。

### 9.4 运行与任务

```text
GET  /api/operations/runs
GET  /api/operations/runs/{run_id}
POST /api/operations/runs/{run_id}/cancel
POST /api/operations/runs/{run_id}/retry

GET  /api/operations/document-jobs
GET  /api/operations/document-jobs/{job_id}
POST /api/operations/document-jobs/{job_id}/cancel
POST /api/operations/document-jobs/{job_id}/retry
```

### 9.5 用量、配额与审计

```text
GET /api/usage
GET /api/quotas
PUT /api/quotas/tenant
PUT /api/quotas/users/{user_id}

GET /api/audit-events
GET /api/audit-events/{event_id}
```

### 9.6 Provider 与控制台上下文

```text
GET    /api/console-context
GET    /api/providers
POST   /api/providers
PUT    /api/providers/{provider_id}
DELETE /api/providers/{provider_id}
POST   /api/providers/{provider_id}/test
```

Provider 响应只返回 `has_api_key`，不返回明文密钥。创建和修改必须提交 `reason`，删除通过查询参数提交 `reason`。仍启用或仍为默认项的 Provider 禁止直接删除，必须先停用并切换默认项。默认项切换会在 `impact.replaced_default_provider_ids` 中返回受影响项。连接测试拒绝 localhost、非公网 IP 和解析到非公网地址的主机；带 API Key 的公网 Provider 必须使用 HTTPS。确需访问私网模型时用 `LEA_PROVIDER_HOST_ALLOWLIST` 精确放行主机名。创建、修改、删除和连接测试都写审计事件，审计快照同样不得包含密钥。

### 9.7 操作后果

- 发布审批绑定提交时的资源版本；资源变化后旧申请不能继续通过。
- Tool/MCP 审批要求最新版本的自动检查通过，旧版本检查不能复用。
- `suspend` 和 `archive` 会立即禁用资源；`archive` 是不可逆操作。
- Agent Run 重试创建新记录，保留原失败记录；同一会话已有活动 Run 时拒绝重试。
- 文档任务重试先删除旧分块、章节和向量；任一步失败都不创建新任务。
- 已进入 `STARTED` 的 Celery 文档任务不执行强杀，避免留下半写入状态。
- 配额更新即时影响后续请求，不追溯取消已开始的任务。
- 跨服务审计写入失败不会回滚已经成功的业务操作，但必须记录服务日志并触发监控告警。

## 10. 数据模型建议

### 10.1 `publication_requests`

```text
id
resource_type
resource_id
resource_version
tenant_id
source_scope
target_scope
status
requested_by
request_note
reviewed_by
review_note
reviewed_at
risk_snapshot_json
resource_snapshot_json
created_at
updated_at
```

约束：同一资源版本和目标范围最多存在一个 `pending` 申请。

### 10.2 `resource_versions`

```text
id
resource_type
resource_id
version
tenant_id
snapshot_json
content_hash
created_by
created_at
```

Skill 已经有独立版本表，第一阶段可以继续使用；其他资源逐步接入统一版本快照。

### 10.3 `audit_events`

采用只追加模型。`before_summary` 和 `after_summary` 必须脱敏，凭证只记录“已变更”，不记录值。

## 11. 当前接口复用与缺口

| 领域 | 可复用能力 | 需要新增或调整 |
|---|---|---|
| Agent | CRUD、分享、绑定、默认 Agent | 已补统一发布申请、版本快照、暂停和归档 |
| Team | CRUD、成员管理 | 发布、版本、运行统计 |
| Skill | CRUD、版本、上传、下载、审批 | 已迁移到通用审批查询模型，保留兼容接口 |
| Tool | CRUD、启停 | 已补自动检查和发布审批；调用统计后续接入 |
| MCP | CRUD、启停、测试、同步 | 已补发布审批和基础检查；同步差异与凭证轮换后续接入 |
| 知识库 | CRUD、文档上传删除、搜索 | 已补发布审批和任务重试；内容扫描后续接入 |
| 记忆 | 管理员查询删除、策略 CRUD | 网关显式映射、敏感访问审计 |
| 检索模型 | CRUD、默认模型 | 中性公共管理路径、连接测试、密钥脱敏 |
| Agent Run | 单个查询、状态、停止 | 已补管理列表、租户筛选、重试和处置审计 |
| Parser | 解析用量、租户和用户配额 | 已补统一用量入口、网关入口和配额审计 |
| 用户角色 | 本地旧 CRUD | 不复用；接外部租户系统只读能力 |
| 反馈 | 数据模型和收集器 | Phase 2：先建立稳定在线服务，再接查询、标记和审计 |

## 12. 网关与服务边界

### 12.1 网关职责

- 验证 OIDC JWT。
- 校验所选租户成员关系。
- 将外部角色收敛为 `user`、`admin`、`platform_admin`。
- 清除客户端伪造的身份头并注入可信内部身份。
- 维护显式路由白名单。
- 传递请求 ID。

### 12.2 网关不负责

- 判断具体对象是否属于调用者。
- 判断知识库、Agent 等对象能否被修改。
- 执行审批状态迁移。
- 代替服务层租户过滤。

对象级授权必须在持有对象数据的服务中执行。

### 12.3 路由迁移

第一阶段新增中性别名：

```text
/api/agents            -> lea-config /agents
/api/teams             -> lea-config /teams
/api/skills            -> lea-config /skills
/api/tools             -> lea-config /tools
/api/mcp-servers       -> lea-config /mcp-servers
/api/knowledge-bases   -> lea-rag /knowledge-bases
```

旧 `/api/user/*` 暂时保留并返回弃用响应头。不得直接开放 `/api/admin/*` 通配路由。

## 13. 非功能要求

- 所有列表接口必须分页并支持稳定排序。
- 管理写操作支持 `Idempotency-Key` 或等效幂等机制。
- 审批、删除、暂停和配额修改必须包含操作原因。
- 关键写操作返回 `request_id`，可关联审计和服务日志。
- 列表响应不得包含密钥、完整凭证、用户私有正文。
- 批量操作必须逐项返回成功或失败结果。
- 管理接口需要独立限流策略。
- 运行列表和审计列表应支持至少 90 天查询。
- 时间统一使用 UTC 存储，前端按用户时区展示。

## 14. 实施阶段

### Phase 0：契约与安全基础

1. 固定角色、租户和对象授权规则。
2. 增加中性资源路由，保留 `/api/user/*` 兼容。
3. 删除或停挂 `lea-config` 中废弃的本地用户/角色路由。
4. 建立统一错误格式、请求 ID 和审计事件写入接口。
5. 为现有管理端点补齐网关白名单和服务端授权测试。

### Phase 1：最小可运营后台

1. 统一审批中心。
2. 五类资源列表和详情。
3. Agent Run 列表、详情、取消和重试。
4. 文档任务列表、失败详情和重试。
5. 用量与配额。
6. 审计日志。

### Phase 2：治理能力

1. 资源版本和回滚。
2. Tool/MCP 安全检查与同步差异。
3. 模型与 Provider 管理。
4. 记忆治理。
5. 反馈和质量样本管理。

### Phase 3：高级运营

1. 批量治理和策略自动化。
2. 质量趋势和成本优化建议。
3. 通知与外部工单集成。
4. 商业订阅与计费，若产品后续需要。

## 15. 第一阶段验收标准

- 普通用户无法访问审批列表、租户运行列表和审计日志。
- 租户管理员只能查看和处理当前租户的数据。
- 平台管理员可以处理全局发布申请，但跨租户操作均有审计记录。
- 五类资源均可提交租户级和全局发布申请。
- 审核绑定固定资源版本，审核后修改不会静默进入已发布版本。
- 已发布资源可以暂停、恢复和归档。
- Tool、MCP 未通过自动检查时不能发布。
- Agent Run 和文档任务可以按状态筛选，并能安全取消或创建重试 Attempt。
- 管理操作不返回明文密钥或用户私有正文。
- 新管理控制台不依赖废弃 `frontend-admin` 的 API 定义。

## 16. 已确定的产品决策

1. 使用一套领域 API，不建设两套重复 CRUD。
2. 用户端与管理控制台是两个前端应用。
3. 身份、租户成员和基础角色继续以外部系统为真源。
4. Skill 现有审批流程作为统一审批模型的迁移起点。
5. 私有资源不普遍要求人工审批；扩大可见范围时审批。
6. Tool、MCP 即使私有，也必须先通过自动安全检查。
7. 管理员默认不能读取用户私有内容。
8. 管理面接口由业务资源命名，不以 `/admin` 作为授权机制。

## 17. 测试环境临时角色策略

在尚未取得基座平台管理员账号期间，测试环境允许由 Gateway 为已认证用户覆盖管理角色：

```text
LEA_ENV=test
LEA_TEST_ADMIN_ROLE=admin            # 默认：仅当前租户管理能力
LEA_TEST_ADMIN_ROLE=platform_admin   # 可选：全局审批和跨租户管理能力
LEA_TEST_ADMIN_ROLE=disabled         # 使用基座返回的真实角色
```

约束：

- 覆盖发生在 Gateway 完成 JWT 验证和租户成员校验之后，不允许匿名访问，也不允许选择自己不属于的租户。
- `admin` 是测试服务器默认值，保留租户隔离，适合日常后台开发和联调。
- `platform_admin` 会获得跨租户能力，只在联调全局审批时临时启用；共享测试环境开启后，所有已登录成员都具备该能力。
- 非 `LEA_ENV=test` 环境配置 `LEA_TEST_ADMIN_ROLE` 会导致 Gateway 拒绝启动，避免配置误带入后续生产环境。
- 下游服务不增加后门，仍然只识别标准的 `admin` 和 `platform_admin` 角色；关闭覆盖后无需修改接口代码。
