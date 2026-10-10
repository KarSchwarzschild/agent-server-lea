# LEA Platform

## 项目简介

LEA Platform 是一个多服务智能教育平台，包含会话代理、配置服务、知识库检索、工具执行、API 网关和前端应用。仓库同时提供 Docker Compose 本地验证栈、CCE/Kubernetes 部署清单、迁移脚本和端到端验证脚本。

## 服务组成

| 服务 | 主要目录 | 默认端口或入口 | 作用 |
|---|---|---|---|
| lea-gateway | packages/lea-gateway | 8080 | 统一网关和路由 |
| lea-agent | packages/lea-agent | 8100 | 会话、流式响应和 agent 执行 |
| lea-rag | packages/lea-rag | 8300 | 知识库和检索 |
| lea-tools | packages/lea-tools | 8400 | 文件、沙箱和工具服务 |
| lea-config | packages/lea-config | 8500 | 专家、团队和配置管理 |
| frontend-user | frontend-user | 由网关或前端开发服务器提供 | 用户端 |
| frontend-admin | frontend-admin | 由网关或前端开发服务器提供 | 管理端 |
| frontend-lab | frontend-lab | 开发环境常用 9300 | 实验和调试前端 |

服务之间依赖 MySQL、Redis、对象存储、Milvus，以及部分环境中的 Cube 沙箱。

当前二期对客户端提供的线上服务以 CCE 为准，已确认主要是 lea-gateway 和 lea-config。其他服务目录属于平台源码、支撑服务或后续部署范围，不应在交接时默认描述为已经对客上线。

## 从哪里开始

- START_HERE.md：生产部署快速导航。
- QUICK_REFERENCE.md：常用命令、验证和回滚速查。
- DEPLOYMENT_CHECKLIST.md：部署前后执行清单。
- DEPLOYMENT_GUIDE.md：完整部署说明。
- CURRENT_STATUS.md：当前状态和剩余事项。
- infra/k8s/README.md：CCE/Kubernetes 部署说明。
- docs/：接口、架构、测试和专项设计文档。

## 本地验证

先复制 .env.example 为 .env，补齐本地依赖配置，不要提交真实 .env。验证栈命令以仓库现有配置为准：

    docker compose -f infra/docker-compose.verify.yml up -d
    docker compose -f infra/docker-compose.verify.yml ps

部署前应执行数据库迁移、检查 Redis 和网关配置，并按 DEPLOYMENT_CHECKLIST.md 完成健康检查、指标检查和 E2E 验证。不要只以容器 Running 作为上线依据。

## 环境变量

复制 .env.example 为 .env。最小本地验证通常需要 DB_HOST、DB_PORT、DB_USER、DB_PASSWORD、DB_NAME、REDIS_URL，以及 LEA_CONFIG_URL、LEA_RAG_URL、LEA_TOOLS_URL、LEA_PARSER_URL。

按功能再配置：

- Agent 和 Temporal：LEA_AGENT_RUN_TIMEOUT、LEA_AGENT_EXECUTION_MODE、TEMPORAL_ADDRESS、TEMPORAL_NAMESPACE、LEA_AGENT_TEMPORAL_TASK_QUEUE。
- 模型和租户：LEA_TENANT_API_BASE、LEA_TENANT_API_KEY、LEA_CHAT_APP_KEY、LEA_MODEL_CHAT、LEA_MODEL_ROUTING、LEA_MODEL_FALLBACK。
- 知识库：LEA_EMBEDDING_MODEL、LEA_EMBEDDING_API_BASE、LEA_EMBEDDING_DIM、LEA_MILVUS_URI、LEA_MILVUS_COLLECTION、LEA_RERANKER_API_BASE、LEA_RERANKER_ENABLED。
- 文件和对象存储：UPLOAD_DIR、GENERATED_FILES_DIR、MINERU_TOKEN 或 MINERU_URL、MINIO_ENDPOINT、MINIO_ACCESS_KEY、MINIO_SECRET_KEY、MINIO_BUCKET。
- 沙箱和记忆：SANDBOX_BACKEND、CUBE_API_URL、CUBE_API_KEY、LEA_MEMORY_MODE。

生产环境优先按 .env.example、对应 Compose 文件和 infra/k8s/config.yaml 的说明覆盖服务地址；真实密钥只放在 Secret 或运行环境中。

## 开发约定

- Python 服务使用 pyproject.toml、uv.lock 和各包自身说明。
- 前端使用 pnpm workspace，根目录有 pnpm-workspace.yaml 和 pnpm-lock.yaml。
- Go 服务位于 packages/lea-agent，编译和运行方式以该包的 Dockerfile、go.mod 和部署文档为准。
- migrations/ 中的 SQL 需要按顺序执行，并在生产环境记录执行结果。

## 交接记录

- 代码目录：D:\gitlab\lea-platform
- CCE 清单：infra/k8s/。
- 当前对客服务：lea-gateway、lea-config。
- CCE 控制台入口：用户提供的 lea-gateway Deployment 页面；lea-config 使用同一集群和命名空间下的对应 Deployment 页面。
- 关键环境变量包括数据库、Redis、对象存储、内部服务 Token 和 LLM 配置；只提交样例，不提交真实值。
- 本仓库当前包含较多未提交的功能和部署改动，交接时应先整理分支边界，明确哪些改动已验证、哪些仍在开发中，再形成发布 commit。
