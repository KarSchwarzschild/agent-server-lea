# LEA Workspace 前端上下文

## 产品定位

`frontend-lab` 是新的业务工作台前端，不继续扩展旧的管理后台。目标体验接近 Codex / Cursor：

- 左侧是导航、项目、最近会话
- 中间是对话和 SSE 工作区
- 右侧默认收起，展开后显示文件、产物、引用和调试信息
- 专家与专家团使用同一入口，通过 Tab 切换
- Skill、MCP、Tool 使用同一市场入口，通过 Tab 切换

## 视觉方向

白色 Codex 风格：白色主背景、浅灰侧栏、细灰边界、深色正文、绿色状态和主操作强调色。界面是客户端工作台，不做传统后台大卡片仪表盘。

## 后端边界

浏览器通过 Vite 代理访问统一网关（默认 `http://127.0.0.1:8080`）；网关将对话、会话、附件和沙箱文件转给 `lea-agent`，并将 `/api/user/*` 的专家、市场、专家团和知识库请求转给对应服务。仅在直连调试时才以 `VITE_API_TARGET` 指向 `lea-agent:8100`。

生产 Access Token 仅由浏览器发送给 `lea-gateway`。网关完成 OIDC/RS256 验签及租户解析后，通过 `X-Internal-Token`、`X-User-ID`、`X-Tenant-ID`、`X-User-Role` 向 Config、RAG 和 Agent 传递可信身份；不得将生产 Token 原样转发给内部服务。

### 已接入接口

- `GET/POST /api/sessions`：会话列表和创建
- `GET /api/sessions/:id/messages`：历史消息
- `POST /api/chat/stream`：发送消息并消费 SSE
- `POST /api/chat/attachments/upload`：上传附件并返回附件 ID
- `GET /api/chat/attachments/:id/download`：下载用户自己的原件
- `GET /api/files/list`：列出沙箱工作区产物
- `GET /api/files/download`：下载沙箱产物
- `GET /api/user/agents`：专家/Agent 列表
- `GET/POST /api/user/teams`：专家团列表和创建
- `GET /api/user/skills`：Skill 市场
- `GET /api/user/mcp-servers`：MCP 市场
- `GET /api/user/tools`：Tool 市场
- `POST /api/user/tools`：创建 Tool
- `PATCH /api/user/mcp-servers/:id/enable`：启用或停用 MCP
- `POST /api/user/mcp-servers/:id/sync`：同步 MCP 工具
- `POST /api/user/skills/upload`：上传 Skill 包
- `DELETE /api/user/knowledge-bases/:id/documents/:document_id`：删除知识库文档

### 文件语义

对话附件上传后先保存原件到 OBS，并记录 `file_key`。普通 `Read` 可以按 `attachment:<id>` 读取解析文本；只有模型调用 `Bash` 时，后端才从 OBS 将附件懒物化到会话沙箱的 `/mnt/uploads`。沙箱产物则通过 `/api/files/list` 和 `/api/files/download` 访问。

## 前端当前状态

- 聊天、SSE、历史会话、附件上传、工作区列表、工作区文本/图片预览、工作区下载已实现
- Skill、MCP、Tool 市场真实列表已实现，Skill 默认加载当前用户可见的全部层级
- Skill 上传、编辑、删除、发布申请，MCP 启停、同步、编辑、删除，Tool 创建、编辑、删除已实现
- 专家/专家团页面已接真实列表、创建、编辑、删除和成员管理
- 专家配置支持持久绑定 Skill、MCP、Tool、知识库，并可在编辑时增删绑定
- 知识库页面已接入列表、创建、编辑、删除、文档上传和删除、检索测试；开发代理通过 `VITE_GATEWAY_TARGET` 指向网关
- 项目页面已接入创建、改名、编辑、归档、删除和项目会话列表
- 附件原件下载接口已接入右侧工作台附件列表
- 已移除演示会话、演示项目、演示文件、演示引用和假的 SSE 监听入口；无真实数据时显示空态
- SSE 已渲染 thinking、tool_event、chain_hop 的活动时间线；历史消息 extra 也会恢复到时间线
- 右侧“文件”明确展示当前 Session 沙箱工作区，支持刷新、真实文件列表、文本/图片预览和下载；上传附件单独显示，不混入沙箱列表
- SSE `references` 事件和历史消息 `extra.references` 会聚合到右侧“引用”页，未返回时才显示空态

## 本地运行提示

- 前端预览运行在 `http://127.0.0.1:9300`。
- 本机已启动 `lea-gateway`、`lea-agent`、`lea-config`、`lea-rag`、`lea-tools`、Redis、MinIO 和 Milvus；网关暴露 `8080`，前端预览运行在 `9300`。
- `lea-gateway` 已增加 `/api/user/teams*` 与 `/api/user/mcp-servers*` 的配置服务转发，部署时需要重启该服务。

## 后续顺序

1. 补充项目会话标题和更完整的会话管理
2. 补充设置页面和搜索入口
3. 评估是否需要独立任务视图（当前任务进度已在对话活动时间线展示）

## 注意事项

- SSE 必须使用 `fetch + ReadableStream`，不能使用原生 `EventSource`：主链路是 POST 且需要 Authorization Header。
- 不要把前端传入的 `tenant_id` / `user_id` 当作权限依据。
- 不要把对话附件直接当成 `/api/files/list` 的沙箱产物；两者是不同资源。
- 后端没有提供浏览器直接写入沙箱的通用接口，前端不要显示虚假的“已保存编辑”状态。
