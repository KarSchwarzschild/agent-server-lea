"""给《二期功能明细表》的"完成情况"列（I 列）填判定结果。

判定三档：
    已完成    代码在、部署在测试服、且验证过
    部分完成  有一部分，备注里写明缺什么
    未开始    没有对应实现

**写副本，不覆盖原文件**（原件在微信的 FileStorage 下，是只读的）。

判定依据一律写进 K 列（备注说明），便于复核"凭什么这么判"。依据只引用这个
仓库里能查到的事实：文件路径、表名、容器状态、commit。拿不准的一律降档到
"部分完成"或"未开始" —— 高估比低估危险，它会让人以为某件事不用做了。

用法：
    uv run --with openpyxl python scripts/fill_phase2_status.py <源 xlsx> <输出 xlsx>
"""

from __future__ import annotations

import sys

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

# ── 判定表 ──────────────────────────────────────────────────────────────────
#
# key = 序号（A 列），value = (完成情况, 备注依据)
#
# 空备注表示沿用同模块上一条的依据（下面展开时会填上）。

STATUS: dict[int, tuple[str, str]] = {}


def mark(seq_range, status: str, note: str) -> None:
    """给一段连续序号打同一个判定。"""
    if isinstance(seq_range, int):
        seq_range = [seq_range]
    for s in seq_range:
        STATUS[s] = (status, note)


# ══ L1 Agent核心运行层 ═══════════════════════════════════════════════════════

# Agent推理引擎（1-3）
mark(1, "已完成",
     "agentloop Go 引擎库提供推理循环（思考→工具调用→观察→继续）；"
     "packages/lea-agent 在测试服运行中（lea-agent-test healthy）")
mark(2, "部分完成",
     "简单问答与多 Agent 协作（星型/handoff 两种拓扑）已实现，见 "
     "internal/session/agent_session.go::runChain；"
     "但“按复杂度自动选择执行策略”没有判定逻辑，是由用户/配置显式指定的")
mark(3, "部分完成",
     "超时保护（timeout，20 处）、对话压缩（agent_session.go + "
     "persistence/engine_backend.go）、Token 预算（costCeilingTokens）已有；"
     "异常恢复有 run_attempt + internal/router/resume.go；"
     "并发限制未找到实现（concurrency/semaphore 仅出现在工具侧，非会话级）")

# 结构化计划执行（4-5）
mark(4, "部分完成",
     "计划工具三件套已实现：save_plan / get_plan / update_plan_step"
     "（internal/runtime/platform_tools.go）；"
     "但“方案审查”和“用户审批”两个环节没有实现 —— 运行时无人工确认机制"
     "（confirm/approval/require_user 在 lea-agent/internal 零命中）")
mark(5, "部分完成",
     "执行状态管理有 plan_store.go；长任务断点继续有 Temporal "
     "（internal/temporalrun/temporalrun.go::AgentRunWorkflow）+ "
     "SSE 断点续传（internal/eventlog，写 Redis Stream 按 seq 续传）；"
     "失败步骤的自动重试策略未见独立实现")

# 记忆系统（6-10）
mark(6, "已完成",
     "长期记忆：packages/lea-rag/src/lea_rag/server_memory.py + memory/retriever.py；"
     "任务级中间结果：plan_store.go。memory_search / memory_add 已注册为平台工具")
mark(7, "部分完成",
     "多 Agent 的用量与结果在 collector 层聚合（setChainUsage / agentUsages），"
     "子 Agent 间可通过 handoff 传递上下文；"
     "但“共享记忆”没有独立的跨 Agent 记忆空间，记忆 scope 是 global/agent 两级")
mark(8, "已完成",
     "记忆按 scope 隔离（global / agent:<id>），查询时带 user_id + tenant_id，"
     "见 platform_tools.go 的 memory_search 入参")
mark(9, "部分完成",
     "后端完整（server_memory.py 的 CRUD）；frontend-lab 有 /api/user/memories 调用。"
     "9395f17 commit 做的“会话级模型选择 + 用户长期记忆管理”")
mark(10, "未开始",
     "基座平台侧的长期记忆配置开关、应用发布页按签名用户配置记忆，均未实现"
     "（发布页签名机制本身也没有，见 87）")

# Agent安全管控（11-19）
mark(11, "部分完成",
     "工具权限控制已有（三段式可见性 + Tool Gateway 权限表）；"
     "Token 预算有 costCeilingTokens；执行终止有 interrupt（agent_session.go）；"
     "行为记录有 audit_events 表（migration 032）。"
     "**但“输入输出安全围栏”完全没有** —— guardrail/moderation 零命中")
mark(12, "未开始",
     "输入内容审核无实现。guardrail / moderation / sanitize 在全仓业务代码零命中")
mark(13, "未开始", "输出内容审核无实现（含流式审核），同上零命中")
mark(14, "未开始",
     "Prompt 注入防护无实现（prompt_injection 零命中）。"
     "风险是实际存在的：KB 文档、MCP 工具返回、沙箱代码都进模型上下文")
mark(15, "未开始", "敏感信息识别与脱敏无实现（redact / mask 零命中）")
mark(16, "已完成",
     "三段式可见性（全局平台层 / 租户内共享 / 用户私有）覆盖 agent / skill / "
     "tool / mcp / kb，见 models/agent.py::visible_to 等；"
     "写操作走 _require_mine，admin 操作走 _is_admin")
mark(17, "未开始", "安全策略接入机制不存在（依赖 12/13 先落地）")
mark(18, "未开始", "应用编排处选择安全策略 —— 依赖 17")
mark(19, "未开始",
     "安全审核日志不存在。audit_events 记的是治理动作（发布/审批/配置变更），"
     "不是内容安全命中")

# Agent管理与对话（20-31）
mark(20, "已完成",
     "GET /api/agents 带检索筛选，按 visible_to 过滤；"
     "控制台已验证可用（返回 19 个 agent）")
mark(21, "部分完成",
     "手工创建/编辑完整（POST/PUT /api/agents）；"
     "**AI 对话模式创建 Agent 未实现**（见 80，同一能力）")
mark(22, "部分完成",
     "名字/描述/头像/提示词/可见性/关联 skill 与知识库/模型/温度等均可配置"
     "（AgentUpdate 16 个字段 + 三张 binding 表）；"
     "**AI 优化提示词未实现**；max_turns 前端表单缺失")
mark(23, "部分完成",
     "后端有 internal/router/debug.go::handleDebugTrace（跑一轮返回逐阶段耗时"
     "和记忆注入内容）；frontend-lab 有 debug 字样 7 处。"
     "但这是一次性调试接口，不是编排页里的调试面板")
mark(24, "已完成",
     "发布 + 审核链路完整：publication_requests / resource_publications / "
     "audit_events（migration 032/033），控制台 frontend-console 已部署可用"
     "（8890 端口，接口已用真实登录态验证）")
mark(25, "未开始",
     "Agent 复制未实现。POST /agents 只能从零建，没有 clone/duplicate 端点")
mark(26, "部分完成",
     "**后端刚完成（commit 8f65831）**：agent_versions 表 + "
     "GET /agents/{id}/versions、GET .../versions/{rev}、POST .../rollback。"
     "追加式回滚，历史不改写。migration 037 已在测试服执行（26 个 agent 补了基线）。"
     "**前端界面未做** —— 编排页没有版本记录入口")
mark(27, "部分完成",
     "对话页在 frontend-lab/src/App.vue（4005 行单文件）；SSE 流式输出已验证"
     "逐行增量；附件上传有 /api/chat/attachments/upload（Word/PDF/Excel/TXT/MD "
     "由 lea-parser 解析）；开场白/开场问题字段在 Agent 配置里。"
     "具体 UI 完备度未逐项核对")
mark(28, "部分完成",
     "工具调用过程有数据支撑：extra.tool_calls / sandbox_calls / segments 落库，"
     "references 带来源（title/path/source）；App.vue 有“引用”7 处、“工具调用”1 处。"
     "但 references 只存标题和路径，**不存命中的原文片段**")
mark(29, "部分完成",
     "后端有对话模式的 system prompt 注入开关（R32 设计：开关式注入，不做多轮"
     "状态机）；frontend-lab 的 App.vue 里“对话模式”零命中 —— 前端入口未做")
mark(30, "部分完成",
     "计划工具（save_plan/get_plan/update_plan_step）在后端已注册；"
     "前端“计划模式”零命中 —— 没有模式切换入口，也没有计划面板")
mark(31, "已完成",
     "会话历史：chat_sessions（176 条）+ chat_messages（899 条）；"
     "GET /api/sessions 在 frontend-lab/src/api.ts 里")

# Agent工作台（32-34）
mark(32, "部分完成",
     "统一认证已接入（lea-gateway 用 JWKS 验外部 OIDC JWT，"
     "X-Selected-Tenant-ID 去问宿主的 TenantUser/CurrUserTenants 校验成员身份）；"
     "用户姓名在 JWT 的 Nickname 里。但“工作台”作为独立形态未实现")
mark(33, "未开始",
     "工作台的场景管理/子场景/子场景默认提示词/子场景关联 skill 这一整套"
     "配置模型不存在（无对应表，无对应端点）")
mark(34, "部分完成",
     "模型/知识库/skill 的权限处理本身有（三段式可见性），"
     "但没有“工作台”这一层的权限边界")

# 代码沙箱执行服务（35-40）
mark(35, "已完成",
     "Cube 沙箱已接入（envd :49983 读写/健康，:49999 执行），"
     "packages/lea-tools/src/lea_tools/api/sandbox.py。"
     "SANDBOX_BACKEND 未设时静默回退 mock")
mark(36, "已完成", "Python 执行已验证可用（Cube :49999 原生只认 Python）")
mark(37, "部分完成",
     "**Node.js 不支持**。沙箱语言白名单是 {python, bash, shell, sh}"
     "（lea_tools/metrics.py::_SANDBOX_LANGUAGES）；"
     "bash 需要包一层子进程才能跑（Cube 只认 Python）。exit_code==2 是"
     "两个后端对“语言不支持”的统一约定")
mark(38, "部分完成",
     "执行超时有（req.timeout）；CPU/内存/磁盘/网络限制由 Cube 侧提供，"
     "本平台未做显式配置和校验")
mark(39, "已完成",
     "每次任务独立 session（session_id = uuid4().hex[:12]）；"
     "生命周期 15min pause / 1h kill 分层；产物目录剪枝后推 S3")
mark(40, "部分完成",
     "有指标埋点（record_sandbox_execution 记 language/status/duration，"
     "status 区分 success/unsupported_language/error/unavailable）；"
     "stdout/stderr 落库时截断 2000 字符。"
     "但没有独立的沙箱执行日志表，也不记资源消耗")

# 模型路由与成本治理（41-45）
mark(41, "未开始",
     "按场景/复杂度/成本/性能选模型的路由策略不存在。"
     "现在的模型选取是：会话级显式指定 > Agent 配置的 model > provider 默认")
mark(42, "未开始",
     "主备模型切换不存在。llm_providers 表没有主备概念，"
     "匹配不到 provider 时退到 env 兜底（_tenant_llm_fallback），不是模型降级")
mark(43, "部分完成",
     "Token 统计完整（collector 记主会话/每个子会话/逐轮/peak input，"
     "落 chat_messages.extra.usage + token_count 列）；"
     "**但没有价目表、没有成本换算、没有预算阈值和告警**。"
     "成本由中转平台按 apikey 计费（这是项目决定，不在本平台重复实现）")
mark(44, "部分完成",
     "用户端可选模型（会话级模型选择，commit 9395f17，chat_sessions.model）；"
     "**Agent 的备用模型、租户级路由等级配置、意图识别自动路由均未实现**")
mark(45, "部分完成",
     "模型管理后台有（llm_providers CRUD + 控制台 ResourceManager 页面，"
     "/api/providers 已验证）；"
     "**档位配置、主备模型、自动切换均未实现**")

# ══ L2 通用能力增强层 ════════════════════════════════════════════════════════

# 多Agent编排引擎（46-48）
mark(46, "部分完成",
     "链式（handoff）和星型（团长派活）两种拓扑已实现并在跑"
     "（internal/session/agent_session.go::runChain + engine.RunChain）；"
     "**条件分支和循环迭代未实现**")
mark(47, "部分完成",
     "子任务委派（团长→成员）+ 结果聚合（collector 的 agentUsages 逐条记账）已有；"
     "Agent 间通信限于 handoff 传递，没有通用消息机制")
mark(48, "部分完成",
     "串行（handoff）、并行（星型多成员）、结果汇总已有；"
     "**条件分值（分支）未实现**")

# 多Agent配置与管理（49-54）
mark(49, "未开始",
     "群聊模板（学习小组/辩论/自由讨论）不存在。agent_teams 表有 5 条数据，"
     "但没有模板概念")
mark(50, "部分完成",
     "agent_teams + agent_team_members 表和 CRUD 已有"
     "（models/team.py，/api/user/teams 在 frontend-lab 的 api.ts 里）；"
     "“群”的形态和群聊 UI 未做")
mark(51, "未开始",
     "主持人模式/自动路由模式/任务完成暂停/允许主动发言/最大轮次 —— "
     "这些调度开关在 team 模型里不存在（只有 role 字段，自由字符串）")
mark(52, "未开始", "多 Agent 群聊对话界面未做")
mark(53, "未开始",
     "多 Agent 群共享未实现。三段式可见性覆盖 agent/skill/tool/mcp/kb，"
     "**不覆盖 team**")
mark(54, "未开始", "多 Agent 群审核未实现（依赖 53）")

# skill市场与管理（55-63）
mark(55, "部分完成",
     "本地上传 skill 已有（/api/user/skills/upload）；"
     "skill-creator 这个 skill 本身存在（packages/lea-tools/skills/skill-creator）；"
     "但“自然语言描述→系统辅助生成 skill”的平台级能力未串起来")
mark(56, "部分完成",
     "skill_versions 表（migration 015）支持多版本迭代；"
     "但没有“改造”的工作流（读旧版→改→发新版）")
mark(57, "已完成",
     "skill 三段式共享（个人/租户内/全平台）+ skill_publish_requests 表，"
     "见 models/skill_publish.py")
mark(58, "未开始",
     "skill 安全检测不存在。上传的 skill 是 bundle（含可执行内容），"
     "没有任何静态检查或高危判定")
mark(59, "已完成",
     "后台多级审核已实现：publication_requests + 控制台审批页，"
     "租户级和平台级分开（risk_snapshot 记风险快照）")
mark(60, "已完成", "skill 删除/卸载有 DELETE /api/skills/{id}")
mark(61, "部分完成",
     "库里 7 个 skill（pptx/xlsx/pdf/ppt_generator/skill-creator 等），"
     "三级可见性可用，skill 缓存分层已做（OBS 真源 + 本地 FS 二级 + 进程 LRU，"
     "Redis 只放索引）；"
     "**市场形态的前端页面未做**")
mark(62, "已完成",
     "agent_skill_bindings 表支持多对多（一个 skill 多 agent 复用，"
     "一个 agent 挂多个 skill），带 priority")
mark(63, "未开始",
     "skill 调用次数/调用人数统计不存在。"
     "注：get_skill_list_cache 等三个缓存函数零调用点（死代码）")

# 工具与MCP（64-73）
mark(64, "已完成",
     "GET /api/mcp-servers 已验证（返回 1 个 DeepWiki MCP），库里 5 条；"
     "控制台 ResourceManager 页面可管理")
mark(65, "已完成",
     "MCP 创建管理完整（models/mcp_server.py + api/mcp_servers.py，"
     "含 /sync 同步工具清单）")
mark(66, "部分完成",
     "按租户范围控制已有（三段式可见性）；"
     "“给普通租户下放创建/管理 MCP 的权限”这个授权模型未实现 —— "
     "现在是 require_admin 一刀切")
mark(67, "未开始",
     "API/JS 函数类型的工具不存在（tools 表里只有 MCP 代理工具和内置工具），"
     "权限下放同 66")
mark(68, "部分完成",
     "MCP 调用有指标埋点（record_mcp_call 记 server_name/status/duration，"
     "5 种状态含 unreachable/http_error）；"
     "**但没有调用日志表** —— 参数摘要、结果、错误详情不落库")
mark(69, "部分完成",
     "工具网关调用协议已定义（/tools/execute 请求响应格式 + 权限表 schema）；"
     "但“校内业务系统以 MCP 方式接入”的对外规范文档未成文")
mark(70, "未开始", "工具市场前端页面未做")
mark(71, "已完成",
     "Tool Gateway 已实现并在跑（lea-tools 部署在 10.192.241.137:8400）；"
     "分层策略：读写自己空间直连、code_interpreter 走网关（按 blast radius 判定）")
mark(72, "未开始", "MCP Server 开发框架/适配器/模板未提供")
mark(73, "未开始",
     "工具测试沙箱不存在。MCP 有 /sync 能拉工具清单，"
     "但没有“上线前在隔离环境验证异常路径和权限边界”的流程")

# 知识增强（74-79）
mark(74, "已完成",
     "**刚完成（commit 226cea3）**：Milvus 原生 hybrid_search，"
     "dense(COSINE) + BM25 双路 + RRF 融合（k=60）。"
     "在真 Milvus 上用构造的 A/B 数据集验证过双路都贡献了名次"
     "（RRF 头名 0.0328 = 2/61，单路只会是 1/61），"
     "Milvus RPC 中位 14.2ms。WeightedRanker 作为 A/B 对照保留")
mark(75, "部分完成",
     "章节结构已有：document_sections 表 + section_boost（标题命中加成，"
     "按候选最高分的 10% 而非固定值）；"
     "**但“推理导航”（按树逐层定位）未实现** —— 现在是平铺检索 + 章节加成")
mark(76, "已完成",
     "三级共享（个人/租户内/全平台）已实现，"
     "见 lea_rag 的 KBService.get_visible_kb_ids")
mark(77, "部分完成",
     "图片：embedding_client.py 有 aembed_images，KB 支持 vlm_enabled 配置；"
     "**音频、视频未实现**")
mark(78, "部分完成",
     "表格解析有（lea-parser 的 xlsx/docx/pdf 解析器 + anthropic-xlsx skill）；"
     "**但“数据集处理 + 数据查询”（结构化查询而非向量检索）未实现**")
mark(79, "未开始", "三方知识库 API 打通未实现")

# 能力增强（80-83）
mark(80, "未开始",
     "AI 创建对话/文本生成应用未实现（自动解析意图→命名→填描述→"
     "生成系统提示词/开场白/开场问题）。同 21 的“AI 对话模式创建 Agent”")
mark(81, "未开始", "声音克隆未实现")
mark(82, "未开始", "AI 生成人物形象和背景未实现")
mark(83, "未开始",
     "工作流 AI 生成未实现 —— 而且**工作流本身在这个平台不存在**"
     "（没有节点编排模型）")

# ══ L3 开放接入与组件输出层 ══════════════════════════════════════════════════

mark(84, "部分完成",
     "frontend-lab 是 Vue 3，对话面板和工具调用展示在 App.vue 里（4005 行单文件）；"
     "**但没有抽成可复用的组件库** —— 无独立 package、无对外发布形态。"
     "计划面板、测试题、Word/PPT 预览、html 预览等组件未做")
mark(85, "未开始", "Java SDK 未提供（三层 SDK 架构未实现）")
mark(86, "部分完成",
     "Agent 对话 API 存在（POST /chat/stream，SSE）且网关做了鉴权；"
     "但“开放 API”的对外形态（独立的 API key 体系、文档、限流）未做")
mark(87, "未开始",
     "应用接入的鉴权签名/Token 管理未实现。"
     "现在的鉴权是宿主平台的 OIDC JWT + 服务间 internal token，"
     "没有“给第三方应用发 key 并签名”这一层")
mark(88, "未开始",
     "Webhook 未实现（webhook 在全仓业务代码零命中）。"
     "任务完成/异常/安全命中的事件回调都没有")

# ══ L1~L3 横向治理与运营保障 ═════════════════════════════════════════════════

mark(89, "部分完成",
     "数据层面有：extra.tool_calls / sandbox_calls / references / segments / "
     "usage 落库（899 条消息里 408 条有 extra，平均 4KB 最大 141KB）；"
     "后端有 handleDebugTrace 返回逐阶段耗时。"
     "**缺的是：①哪个模型答的查不出来**（model 列在 chat_sessions 上，是会话级的，"
     "切模型就被覆盖）；②没有完整链路的查询界面；③references 不存命中原文")
mark(90, "未开始",
     "意图识别日志不存在 —— 意图识别本身也没有（见 44 的“意图识别 router”）")
mark(91, "部分完成",
     "用量日志有（token_count 列 + extra.usage 细账）；"
     "**但成本消耗没有**（无价目表，成本由中转平台按 apikey 算）；"
     "而且用量埋在 JSON 里，没法按租户聚合查询")
mark(92, "部分完成",
     "失败会落库（runErr 时写 [执行失败] 消息）+ agent_run_attempts 有 run_attempt；"
     "有 AgentWorkerActivityFailing 等告警。"
     "**但没有“异常任务列表”的查询端点和页面**")
mark(93, "部分完成",
     "Prometheus + Grafana 已部署（system-health.json 仪表板 20 个面板，"
     "8 个抓取目标全 up，29 条告警规则 health 全 ok）；"
     "**但那是基础设施视角的看板，不是业务侧的“Agent 数据统计看板”“")
mark(94, "未开始",
     "报表导出、费用统计优化、大数据量导出、按 key 统计用量 —— 均未实现。"
     "注：按 key 统计这一项的前提是每个租户/应用有自己的 apikey，"
     "而现在全平台共用一把（llm_providers 只有 1 行挂在 default 下，"
     "其 key 与环境变量 LEA_TENANT_API_KEY 指纹一致）")
mark(95, "未开始",
     "点赞点踩和反馈未接通。packages/lea-data 里有 feedback.py / evaluation.py "
     "（含 FeedbackType），但 **lea-data 不在 test-server 栈里，"
     "且在跑的四个服务都没有引用它** —— 是未接线的代码")
mark(96, "未开始",
     "任务中心和定时跑任务未实现。Temporal 在跑但用途是“长任务别卡住 HTTP 连接”，"
     "**没有用它的 Schedule API**（ScheduleClient/CreateSchedule 全仓零命中）。"
     "SDK 是 v1.49，Schedule API 从 v1.22 起可用，所以基础设施是够的")
mark(97, "部分完成",
     "内部可观测完整（指标/告警/仪表板/SSE 断点续传）；"
     "**但“对外提供”的服务形态没有** —— 无对外的 trace 查询 API")
mark(98, "未开始",
     "成本计量服务未实现，且按项目决定不在本平台做（中转平台按 apikey 计费）")
mark(99, "未开始", "反馈评价服务未实现（同 95）")
mark(100, "未开始", "批任务调度服务未实现（同 96）")

# 智能体中心与作品集运营（101-103）
mark(101, "部分完成",
     "发布审核链路完整（publication_requests + 控制台审批），"
     "审核通过的 Agent 可按可见性被使用；"
     "**但“智能体中心”的前台展示页面未做**；多 Agent 群聊分享未做（见 53）")
mark(102, "未开始", "精选作品集未实现（无对应表、无端点、无页面）")
mark(103, "未开始", "做同款未实现（依赖 102）")

# 渠道接入（104-108）
mark(104, "未开始",
     "Web 嵌入第三方平台未实现（依赖 87 的签名鉴权和 84 的可嵌入组件）")
mark(105, "未开始", "飞书接入未实现（feishu/lark 零命中）")
mark(106, "未开始", "企业微信接入未实现（wecom 零命中）")
mark(107, "未开始", "微信公众号接入未实现")
mark(108, "未开始", "统一渠道网关服务未实现（依赖 104-107）")

# ══ L4 示范应用层（109-213）══════════════════════════════════════════════════
#
# 这一整层是"超级办公智能体客户端"—— 一个桌面客户端。本仓库里**没有桌面客户端
# 工程**：只有四个 web 前端（frontend-lab / console / user / admin），而
# electron 和 tauri 在全仓 package.json 里零命中。
#
# 所以这 105 行的客户端形态一律"未开始"。但其中一部分的**后端能力**是有的
# （pptx/xlsx/pdf 解析与生成 skill、文档解析服务、沙箱数据分析），
# 这些在备注里分别标明 —— 区别是"能力在、载体不在"，不是"什么都没有"。

_L4_NO_CLIENT = "本仓库无桌面客户端工程（electron/tauri 在全仓 package.json 零命中），整层未开始"

# 客户端框架与安装升级（109-112）
mark(range(109, 113), "未开始", _L4_NO_CLIENT)

# 统一认证与权限加载（113-116）
mark(range(113, 117), "未开始",
     _L4_NO_CLIENT + "。注：平台侧的统一认证已接入（lea-gateway 验外部 OIDC JWT "
     "+ 问宿主校验租户成员身份），客户端只需对接，但客户端不存在")

# 本机资源连接器（117-122）
mark(range(117, 123), "未开始",
     _L4_NO_CLIENT + "。本机文件/应用/剪贴板这类能力按定义只能在客户端侧实现")

# 超级办公Agent工作台（123-131）
mark(range(123, 132), "未开始", _L4_NO_CLIENT + "。另见 32-34（工作台形态本身未实现）")

# 成果预览、导出与历史（132-135）
mark(range(132, 136), "未开始",
     _L4_NO_CLIENT + "。注：产物存储有（Cube 沙箱产物推 S3 + artifacts 落库），"
     "缺的是客户端侧的预览/导出/历史界面")

# 客户端基础Skill（136-138）
mark(range(136, 139), "未开始", _L4_NO_CLIENT)

# 帮我写作（139-144）
mark(range(139, 145), "未开始",
     _L4_NO_CLIENT + "。注：写作类 skill 库里只有 internal-comms 和 "
     "alice_weekly_report 两个样例，讲话稿/公文/学术报告/长文档等专用 skill 未建")

# 录音纪要（145-148）
mark(range(145, 149), "未开始",
     _L4_NO_CLIENT + "。且语音转写能力本身不存在（ASR 相关实现零命中），"
     "不只是缺客户端")

# 幻灯片（149-152）
mark(range(149, 153), "部分完成",
     "**后端生成能力已有**：pptx skill（anthropic-pptx）+ pptx-generator + "
     "库里注册的 ppt_generator，可生成 pptx；"
     "但客户端载体不存在，国开模板/HTML 动画/咨询风这三种具体形态也未做")

# 调研研究（153-157）
mark(range(153, 158), "未开始",
     _L4_NO_CLIENT + "。注：平台侧有学术检索能力（lea-rag 的 academic 模块："
     "arxiv / semantic_scholar / wanfang / merged），可作为这组功能的后端支撑")

# 格式校验（158-163）
mark(range(158, 164), "未开始", _L4_NO_CLIENT + "。格式校验规则引擎未实现")

# 创意图文（164-168）
mark(range(164, 169), "未开始",
     _L4_NO_CLIENT + "。图像生成能力本身也不存在（见 82）")

# 数据分析（169-171）
mark(range(169, 172), "部分完成",
     "**后端能力部分具备**：xlsx skill（anthropic-xlsx）+ 沙箱 Python 执行"
     "（pandas 类数据处理可在 Cube 沙箱里跑）+ lea-parser 的表格解析；"
     "但客户端载体不存在，专业可视化未做（见 78：结构化数据查询也未实现）")

# 教育教学（172-180）
mark(range(172, 181), "未开始",
     _L4_NO_CLIENT + "。教育教学类专用 skill（备课/出题/批改等）库里未建")

# 思维工具（181-197）
mark(range(181, 198), "未开始",
     _L4_NO_CLIENT + "。这 17 项都是“内置 skill”形态的思维框架"
     "（批判性思维/第一性原理/多框架发散等），库里一个都没有。"
     "注：skill 机制本身是现成的（skill_versions + 三级共享 + 缓存分层），"
     "建这些 skill 不需要新能力，只需要内容")

# 文档处理（198-201）
mark(range(198, 202), "未开始",
     _L4_NO_CLIENT + "。WPS 云文档对接未实现；"
     "注：本地文档解析有（lea-parser 支持 docx/pdf/xlsx/pptx 等），"
     "网页转 Markdown 未实现")

# 软件联通（202-204）
mark(range(202, 205), "未开始", _L4_NO_CLIENT)

# OA查询（205-207）
mark(range(205, 208), "未开始",
     _L4_NO_CLIENT + "。OA 系统对接未实现（MCP 接入规范见 69，尚未成文）")

# 业务系统数据查询（208-210）
mark(range(208, 211), "未开始",
     _L4_NO_CLIENT + "。校内业务系统对接未实现（同 69）")

# 自动化（211-212）
mark(range(211, 213), "未开始",
     _L4_NO_CLIENT + "。定时/触发式执行在平台侧也未实现（见 96）")

# 远程控制（213）
mark(213, "未开始", _L4_NO_CLIENT)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]

    wb = load_workbook(src)
    ws = wb.worksheets[0]

    fills = {
        "已完成": PatternFill("solid", fgColor="C6EFCE"),
        "部分完成": PatternFill("solid", fgColor="FFEB9C"),
        "未开始": PatternFill("solid", fgColor="FFC7CE"),
    }
    fonts = {
        "已完成": Font(color="006100", bold=True),
        "部分完成": Font(color="9C5700", bold=True),
        "未开始": Font(color="9C0006", bold=True),
    }

    filled = 0
    missing: list[int] = []
    counts = {"已完成": 0, "部分完成": 0, "未开始": 0}

    for row in range(3, ws.max_row + 1):
        raw = ws.cell(row=row, column=1).value
        if raw in (None, ""):
            continue
        try:
            seq = int(str(raw).strip())
        except ValueError:
            continue
        entry = STATUS.get(seq)
        if entry is None:
            missing.append(seq)
            continue
        status, note = entry
        c = ws.cell(row=row, column=9, value=status)   # I 列
        c.fill = fills[status]
        c.font = fonts[status]
        c.alignment = Alignment(horizontal="center", vertical="center")
        k = ws.cell(row=row, column=11)                # K 列：依据
        if not k.value:
            k.value = note
            k.alignment = Alignment(wrap_text=True, vertical="top")
        counts[status] += 1
        filled += 1

    wb.save(dst)
    print(f"已填 {filled} 行 -> {dst}")
    for s, n in counts.items():
        print(f"  {s}: {n}")
    if missing:
        print(f"未覆盖的序号（{len(missing)} 个）: {missing[:40]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
