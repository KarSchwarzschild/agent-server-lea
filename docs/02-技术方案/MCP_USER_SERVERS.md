# MCP 后端方案

> 状态：**P0 已实现并通过真链路验收**（2026-08-25）；P1 / P2 待评审
> 本版取代第一版（2026-08，未评审）——那版有四处被实测推翻或补上，逐条列在 §8
>
> 文件名偏窄了（覆盖范围已不止「用户级」），评审通过时建议改名 `MCP_BACKEND.md`
>
> 关联：`packages/lea-config/{models,api}/mcp_server*.py`、
> `packages/lea-tools/api/admin_mcp.py`、`packages/lea-tools/executor/mcp_proxy.py`、
> `migrations/016`、`TOOL_GATEWAY.md`
>
> **已定的前提**（评审时不必再议）：
> - 用户「上传 MCP」= **填一个远端 URL 注册**，平台不托管、不跑用户的 server
> - MCP 绑到 agent 的粒度 = **server 级**（开关一整个服务），收窄用 `tool_filter`
> - **所有服务和 agent 都是分布式多节点的**，任何"进程内记住了什么"的设计都不成立

---

## 0. 一句话

MCP 原来**不是「缺用户级支持」，是端到端不通**：唯一支持的创建路径建出来的 server，
`/sync` 和执行两条路都会失败。P0 修通了这条链，并顺带修掉两个只有真链路才能暴露的
缺陷。剩下的是分表（P1）和放开用户私有（P2）。

它不通的根因，跟「一个 server 要填 12 把 key」这个荒谬结论，是**同一件事**的两面：
别人定义的东西被放进了「我们自己定义的东西」那张表。

---

## 1. MCP 和工具是什么关系

这是后面所有设计的前提，先说清楚。

**工具**是模型能调的一个能力：有名字、有参数 schema、调了会发生点什么。引擎发给模型
的清单从头到尾就是一串工具——**模型不知道 MCP 是什么，也永远看不到**。

**MCP 是一种进货渠道**：一个远端服务器，连上去问它「你有啥」，它答「我有这三个」，
我们把这三个变成三个工具挂进清单。所以关系是 **一个 MCP server → N 个工具**。

```
① 用户填 URL + key                → mcp_servers 一行（这一行不是工具）
② 平台问它 tools/list             → 它答：search_pages / create_page / update_page
③ 变成模型看得见的工具             → mcp_7_search_pages 等三个
④ 模型调 mcp_7_search_pages       → 查到属于 server 7 → 用 7 的 URL+key 发 JSON-RPC
                                    ⚠️ 发过去的名字必须是 search_pages，不带前缀
```

**①③ 是两种东西**：① 是一个服务，用户开关它、填 key；③ 是一批工具，模型能点。

工具一共三个来源，MCP 只是其中一个：

| 来源 | 谁定义的 | 模型看到的名字 |
|---|---|---|
| 内置（实现在我们代码里） | 我们 | `kb_search` |
| 用户填的一个 HTTP 接口 | 用户 | `my__weather` |
| 接的 MCP server | **别人** | `mcp_7_search_pages` |

三个来源，同一个出口。**分歧全在第三种：别人定义的那批，存哪。**

---

## 2. 现状核查（全部实测，2026-08-25）

### 2.1 `mcp_servers` 是两个模型的并集 —— `tools` 表那个病的第二例

| lea-config 的模型**写**的 | lea-tools 的执行侧**读**的 |
|---|---|
| `config` (JSON, 对齐 MCP 客户端 dict shape) | `endpoint` / `command` / `env_config` / `auth_config` / `tool_filter` |
| `connection_status` / `last_connected_at` / `last_error` | |

`create_all()` 只建表不改表，两个服务先后打在同一张表上，于是真实表是并集。而
`MCPServerCreate` 里**根本没有 endpoint 字段**，用户想填也填不了：

```
现网 4 行，endpoint 全 NULL，config 全有值
execution_type='mcp' 的工具行：0
mcp_server_id 非空的工具行：0
```

⚠️ `admin_mcp.py`（sync）和 `mcp_proxy.mcp_call`（执行）**两处都读 `endpoint`**。
只修一处等于没修。

**已修（P0）**：`migrations/016` 收口到 `endpoint`，删掉 `config` / `command` /
`env_config`。判据跟 `migrations/006`（tools 表那次）一致：**运行时真正决定能不能调通
的那一侧，它的命名是权威的**。不新发明判据。

### 2.2 工具 schema 在下发时丢失 —— 不属于 MCP，但挡在 MCP 前面

`/internal/agent-config`（引擎拿工具清单的**唯一**入口）下发的键是 `schema_json`，
而 Go 的 `ToolDef.InputSchema` 标的是 `input_schema`。`json.Decode` 对不认识的键静默
丢弃 → `InputSchema` 恒为 nil → `jsonSchemaToToolSchema(nil)` 返回一个空对象 schema。

来历：老的 lea-server 下发的键确实叫 `input_schema`，Go 那个结构体是照着它写的；
lea-config 接手时把列统一成执行器的词汇，Go 没跟。而 `LEA_CONFIG_URL` 在 `.env` /
`k8s/config.yaml` / 两个 compose 里**全部**指向 lea-config。

⚠️ 症状不是「调不动」，是「全靠猜」。A/B 实测（同一个中性提问，只换 tag 重编译）：

```
tag 错   get_weather({"latitude": "39.9042", "longitude": "116.4074"})   ← 字符串
tag 对   get_weather({"latitude": 39.9042,   "longitude": 116.4074})     ← 数字
```

**两次都答对了**——模型从工具名和描述里把参数名猜了出来，open-meteo 又收字符串。
所以这个 bug 坏得**不均匀**：参数名好猜、上游宽容的工具看起来完全正常，参数名猜不出
来的（`q` / `sku_id` / `dept_code`）、上游对类型严格的、有必填项要强制的则全废。
抽查几个"看起来能用"完全证明不了没问题。

同一次迁移还掉了两个停用开关：老服务的 JOIN 里带着 `AgentToolBinding.enabled` 和
`Tool.is_enabled`，搬过来时只剩 `filter_by(agent_id)`。

**已修（P0.0，独立于 MCP）**：Go 改读 `schema_json`；两个过滤补回源头；不再下发
零消费者的 `binding_enabled`。

### 2.3 `/session/capabilities` 是死代码，命名前缀不对称

lea-tools 的 `registry.list_tools_for_session`（连同 `exposed_name` 的 `my__` 前缀）
**没有任何 Go 调用方**——引擎只打 `/tools/execute`、`/sandbox/*`、`/api/files/*`。
工具清单来自 lea-config 的 `/internal/agent-config`，它下发的是**裸名**。

而执行侧的 `resolve_tool` 严格认前缀。所以用户私有工具今天：下发裸名 → 模型调裸名
→ `resolve_tool` 在 `owner_scope != USER` 里找 → **404**。

现在 0 行 user 工具，所以潜伏着。**它是 P2 的前置**：放开用户私有 MCP 之前必须先解决
这个不对称，否则放开了也调不通。

这一条同时改了 P1 的设计：拆表之后要加 MCP 分支的是 `/internal/agent-config`，
**不是** `capabilities`（第一版写错了）。

### 2.4 发给远端的名字带着我们的前缀

`mcp_call` 以前是 `session.call_tool(call.tool_name, ...)` —— 把 `mcp_27_lookup_ticket`
原样发给远端，远端回 `unknown tool 'mcp_27_lookup_ticket'`。前缀是**我们**加的消歧
手段，远端只认识自己那个名字。

**这个缺陷单测挡不住**：所有 mcp 单测都 mock 掉 `call_tool`，正好跳过这一跳。它是
真链路验收抓出来的（第 5 步模型传对了参数，工具却报"功能不可用"）。

一直没显形的原因还是那条：sync 从没成功过，一行 MCP 工具都不存在，没有任何调用能走
到这里。**上游断路挡住下游的坑**，这份文档里已经是第三例。

**已修（P0）**：`remote_tool_name()`，只剥本 server 的前缀。

### 2.5 一个 server 要填 12 把 key

凭证按 `tool_id` 存。这对前两种来源是对的——一个工具 = 一个外部服务 = 一把钥匙。
MCP 打破了这个假设：一个 server 出 12 个工具，key 却是 server 的。

更乱的是**今天 key 有两个落点，各对一半**（`mcp_call` 同时读了这两个）：

| 落点 | 粒度 | 隔离 | 加密 |
|---|---|---|---|
| `mcp_servers.auth_config` | ✅ 一个 server 一份 | ❌ 行共享，市场 MCP 全租户一把 | ❌ 明文 JSON |
| `tool_credentials` | ❌ 一个工具一份（12 份） | ✅ 按租户 | ✅ 加密 |

要的是三样全占，现有两个机制没一个是全的。P1 的 `mcp_credentials` 收掉它。

**为什么至今没人发现**：sync 从没成功过，也就没人去配那 12 把 key。修通 sync 的那天
它立刻变成用户能看见的问题——所以 P1 不能拖太久。

P0 顺手修了一个相关的坑：凭证覆盖以前是**就地改** SQLAlchemy 的 JSON 属性
（`auth["bearer_token"] = ...`），同一个 session 后续任何 commit 都会把一次调用的临时
凭证写回库，变成整行的默认值。现在改成拷贝。

### 2.6 进程内缓存：多节点下的凭证串号

```python
_sessions: dict[int, MCPSession] = {}            # 只按 server_id 分键
auth["bearer_token"] = credential["api_key"]     # 凭证塞进被缓存的对象
```

缓存键比数据的归属粒度粗。今天一行 server 只属于一个租户，最坏是用了过期凭证；
**放开每用户 key 之后就是 alice 的 token 发 bob 的请求**。

而且 `MCPSession` 里**根本没有连接**——三个字段而已，每次调用去拿共享 httpx pool。
所以这个缓存缓存不了任何有价值的东西，只缓存了凭证。

同一个事实还推翻了 `admin_mcp.py` 里那句「`/sync` 只能留在 lea-tools，因为连接池在
本进程里」。那不是连接池，那个端点可以随便搬。

**已修（P0）**：删掉。单测里留了一条 `not hasattr(m, "_sessions")` —— 重新引入一个
进程内缓存正是最可能发生的"优化"。

### 2.7 用户私有 MCP：表支持，接口不支持

`models/mcp_server.py` 第 4 行明写 `owner_scope='user' → 用户私有`，现网 seed 里
**已经有两行**（id 23/25）。但 `api/mcp_servers.py` 六个路由**每一个**都硬过滤
`owner_scope == PLATFORM`，那两行通过接口看不见也改不了。P2 处理。

### 2.8 `stdio` 声明了没实现，而它是默认值

`stdio` 的语义是**在网关进程里 fork 子进程**（现网 id=22/24 就是 `command: npx`）。
今天没实现所以是死值；一旦实现，「用户能加 MCP」就等于「用户能在网关进程里执行任意
命令」。

既然已定「只填 URL、不托管」，这条路直接关死。

**已修（P0）**：ENUM 收成一个值。写测试时发现这比预期更强——SQLAlchemy 的 Enum 在
bind 阶段就拒绝，**连造一个 stdio 的行都造不出来**。因此 `mcp_call` 里那道
`check_transport` 在 ORM 路径上打不到；留着它是策略的显式声明，测试里写明了这一点，
免得下一个人以为它在防一个活着的场景。

### 2.9 连通性检查完全没有

`connection_status` / `last_connected_at` / `last_error` 三列只在响应里被读出来，
**全仓没有一处写**。于是状态永远是 `unknown`。

**已修（P0）**：新增 `POST /admin/mcp-servers/{id}/test`，跟 `/sync` 分开——它们回答
的是两个问题（「地址配对了吗」和「把工具同步进来」），绑在一起的话用户填错一个 URL
得先触发一次会写库的操作才知道。

### 2.10 `invalidate_capabilities` 零调用点

有定义，没有任何地方调它。而 `capabilities` 本身还是死代码（§2.3）。P1 一起处理。

---

## 3. 别人怎么做

| | 配置层级 | 传输 | 管理员控制 |
|---|---|---|---|
| **Dify** (v1.6.0+) | workspace 级 | **仅 HTTP** | 工作区凭证 + 节点级覆盖 |
| **ChatGPT** | 个人可加（Developer Mode） | 远程 only | 可全局关掉 / 白名单 |
| **Claude** | 个人可加 | 远程 + 本地 | Team/Enterprise 组织级 |
| **Perplexity** | 显式两档 Individual / Organization | 远程 | 可要求成员各自授权 |

收敛方向：**双档（个人 + 组织）+ 管理员一键收口**。我们的表已经是那个形状。
传输上 Dify 只做 HTTP，是值得抄的克制。

---

## 4. 目标形状

### 4.0 先修下发契约（P0.0，已完成）

见 §2.2。它不属于 MCP，但 MCP 工具走的是同一条下发路——不修的话「sync 修通了、工具
建出来了、绑上了」之后，模型拿到的 MCP 工具照样没有参数，而失败原因跟 MCP 无关。

### 4.1 三张表，各自一个职责（P1）

```
mcp_servers    连接配置 + 归属          用户/管理员写
mcp_tools      发现缓存（新）            只有 sync 写，机器的
tools          我们自己定义的工具        用户/平台写
```

`mcp_tools`：`id / mcp_server_id / name / description / schema_json / fetched_at`，
`UNIQUE(mcp_server_id, name)`，外键 **ON DELETE CASCADE**。

**关键：它没有归属列。** 一个 MCP 工具属于谁，靠 JOIN 它的 server 拿。这让三个问题
从「要记得处理」变成「结构上不可能发生」：

| 问题 | 混在 `tools` 里 | 分开之后 |
|---|---|---|
| 归属 | sync 要**复制**归属到每个工具行——迟早忘 | JOIN 拿，不会漂 |
| 凭证 | 挂 tool_id → 12 份 | 挂 server → 天然一份 |
| 删除 | 要记得清工具行（**已经踩过一次**） | 外键级联 |

⚠️ **P0 之后这件事的代价不再是零。** 第一版写「现在搬代价是零，sync 从没成功过，
`tools` 里一行 MCP 工具都没有」——P0 修通之后就会有真实数据了。所以 P1 要么尽快跟上，
要么接受一次数据搬迁。

拆表还要**同步改 `/internal/agent-config`**：它今天只查 `tools`，拆走之后 MCP 工具就
不在下发清单里了。第一版漏了这一条（它以为要改的是 `capabilities`，而那是死代码）。

### 4.2 三层可见性，不加新列

用已有的 `(tenant_id, owner_scope, owner_user_id)`，跟 `tools` / `skills` 完全一致：

| | 编码 | 谁看得见 |
|---|---|---|
| 市场 | `(default, platform, NULL)` | 所有租户 |
| 租户共享 | `(t1, platform, NULL)` | t1 所有人 |
| 用户私有 | `(t1, user, alice)` | 只有 alice |
| **发布** | 改 `owner_scope` / `owner_user_id` 两列 | 一个人 → 全租户 |

同一套规则管三种资源，不用学三遍。`migrations/014` 的头部已经这么写了。

### 4.3 key 挂 server（P1）

新表 `mcp_credentials`：`mcp_server_id / tenant_id / owner_user_id / api_key_enc /
is_platform_default`。查找按现有降级逻辑照搬：**自己的 → 租户的 → 平台默认**。

不复用 `tool_credentials` 加一列——那会让它变成「要么 tool_id 要么 mcp_server_id」的
二选一外键，又是一个多态，而这个库已经被"一张表两种含义"坑过两次。

于是产品流程直接成立：

- 「从市场选择后输入 key」= 市场那行 server 共用，各人建自己的凭证，**一把**
- 「直接就能拿来用」= 不需要凭证，或平台配了 `is_platform_default` 那把

废掉 `mcp_servers.auth_config` 明文列。

### 4.4 绑定：server 级（P1）

新表 `agent_mcp_bindings(agent_id, mcp_server_id, enabled)`。收窄到某几个工具用已有的
`mcp_servers.tool_filter`。

P0 阶段 MCP 工具住在 `tools` 表里，所以复用 `agent_tool_bindings` 就行（已验证）。

**权限表先不做 MCP 侧**——归属 + 绑定已经管住"谁能用"，角色门和限流等有需求再加。

### 4.5 命名：`mcp_{server_id}_{tool}`，不叠 `my__`

搬出 `tools` 之后 MCP 工具没有归属列，不进 `exposed_name()` 那条路。也不需要：
`server_id` 本身唯一——alice 和 bob 各接一个 Notion 是两行 server、两个 id、两批不撞
的工具名。

⚠️ 前缀是**平台侧**的名字。发给远端时必须剥掉（§2.4）。

### 4.6 `/sync` 放哪（P2 的前置，**本版改了结论**）

`sync` 这一个动作其实是三件事，而它们各自该归谁不一样：

| | 今天 | 第一版提案 | **本版提案** |
|---|---|---|---|
| 判断有没有资格同步 | lea-tools（看 `X-User-Role` 头） | lea-config | **lea-config**（有 actor） |
| 打远端问 tools/list | lea-tools | lea-config | **lea-tools**（有 egress + 凭证） |
| 写 `tools` 行 | lea-tools（**别人的表**） | lea-config | **lea-config**（拥有这张表） |

第一版是「整个搬到 lea-config」。问题是那会让 lea-config **开始打用户填的任意 URL**
——而它今天一次外网都不打，NetworkPolicy 可以把它的 egress 锁死。「哪个服务允许往
用户填的地址发请求」是一条安全边界，越窄越好。

本版提案是拆两跳：lea-config 出 `POST /mcp-servers/{id}/sync`，鉴权和写表都在它这边，
中间内部调 lea-tools 的 `POST /internal/mcp/discover {endpoint, auth}` 只做出网。

代价：多一跳，`auth_config` 在服务间传一次（走 internal_auth，内网）。

**P0 不动。** 今天的 sync 是 admin-only，且租户从 server 行上读（不是请求体），
这一点本来就是对的。这个决策只在放开用户私有 MCP 时才咬人——那时归属判断需要 actor，
而 lea-tools 没有。

### 4.7 连通性检查（P0，已完成）

`POST /admin/mcp-servers/{id}/test` —— 连一下、写三列状态、**不碰 tools 表**。

连不通时返 **200 + `{"ok": false}`**，不是 502：请求本身成功了，我们成功地测出了
"连不通"。502 会让调用方分不清「检查这个动作失败了」和「检查结果是失败」。

---

## 5. 多节点约束

所有服务和 agent 都是多副本。这条改了三处决策，不是补充说明。

### 5.1 只支持 `streamable_http`，**SSE 也不要**

SSE 是长连接，绑在**某一个副本**上。负载均衡把下一次工具调用发到别的副本，那边没有
这条连接。要支持就得引入会话亲和或连接注册表——为一个还没人要的传输方式付这个价不值。

`streamable_http` 是请求-响应，**无状态，天然多副本安全**。

### 5.2 缓存：DB 里的可以，进程里的不行

| 缓存 | 在哪 | 多节点 |
|---|---|---|
| `mcp_tools` 发现缓存 | **DB** | ✅ 共享，任何副本读到同一份 |
| capabilities | **Redis** | ✅ 共享 |
| `_sessions` | **进程内** | ❌ 已删（见 §2.6） |

这也是「发现缓存放 DB 而不是进程内」的独立理由：换成进程内的话每个副本各连一次远端、
各存一份，工具清单会因为落在哪个副本而不同。

### 5.3 `/sync` 必须幂等且并发安全（P0 已实现）

两个副本、或两个管理员，可能同时 sync 同一个 server。

- 先按 `(tenant_id, mcp_server_id)` 把现有行一次查出来按名字索引，再决定 update 还是
  insert，不盲插
- 「远端已经没有的工具」要删，但**不能先删后插**——那个中间窗口里别的请求会读到一份
  空的工具清单，表现是"工具偶尔全没了"。用同一个事务里的集合差集删

⚠️ 仍然缺一道：`UNIQUE(tenant_id, mcp_server_id, name)`。今天靠"先查后写"收敛，两个
副本真的同时跑仍可能各插一行。P1 建 `mcp_tools` 时用唯一索引 + upsert 收掉。

### 5.4 跨服务的缓存失效：先不造机制

lea-config 改了 MCP server，lea-tools 的 capabilities 缓存不知道（而
`invalidate_capabilities` 本来就零调用点，capabilities 本身还是死代码）。

**建议先靠 60 秒 TTL 兜着，并把这个延迟明写进接口文档**，不为它造 pub/sub。配置变更
是低频操作，60 秒延迟对「我刚加了个 MCP 怎么还没出现」是可接受的，而一个跨服务失效
通道会成为一个新的、需要自己保证多副本正确性的东西。

真要做的时候，正确的形状是 Redis pub/sub 或直接把 TTL 调低，**不是**在 lea-config
里去删 lea-tools 的键。

---

## 6. 分期与验收

| 期 | 内容 | 状态 |
|---|---|---|
| **P0.0 下发契约** | §2.2 —— Go 读 `schema_json`；两个停用开关补回源头 | ✅ 已完成 |
| **P0 修通** | §2.1 模型收口（migrations/016）· §2.4 剥前缀 · §2.6 删进程内缓存 · §2.8 传输白名单 · §2.9 `/test` · §5.3 幂等 sync | ✅ 已完成 |
| **P1 分表** | §4.1 三张表 · §4.3 `mcp_credentials` · §4.4 `agent_mcp_bindings` · `/internal/agent-config` 加 MCP 分支 · `tools` 清掉 mcp 分支 | 待评审 |
| **P2 放开** | §2.3 前缀不对称先修 · §4.2 去掉六处 platform 硬过滤 · §4.6 sync 拆两跳 · 租户开关 `allow_user_mcp` 默认关 | 待评审 |

### P0 的验收（已跑通）

真 MCP server（`scripts/mock_mcp_server.py`）+ 真模型 + 真库，五个环节：

```
[1] 注册    endpoint 落库、transport 默认 streamable_http、config 死列没了
[2] 连通    /test ok=true，远端报 2 个工具，connection_status 写回 connected
[3] 同步    tools 出现 2 行，execution_type=mcp，schema 落在 schema_json
[4] 绑定    绑上、且出现在 /internal/agent-config 的下发清单里（schema 完整）
[5] 调用    mcp_28_lookup_ticket({"ticket_ref": "OUC-2291"})
            → 「工单 OUC-2291 的当前状态是 紫貂-待复核，负责团队为 运维二组」
[6] 清理    远端下线的工具被 sync 删掉；删 server 级联清干净工具行
```

判据的设计是刻意的：远端工具叫 `lookup_ticket(ticket_ref)`，**参数名不可猜**（不是
id/query/name，schema 没到模型面前它没法知道）、**返回值不可编**（「紫貂-待复核」这种
生僻组合）。链路断掉时 HTTP 同样是 200——模型会道歉说查不到。第一次跑正是这样：
第 5 步参数传对了但工具报"功能不可用"，那就是 §2.4 那个 bug。

复现：`scripts/mcp_p0_e2e.py`。

---

## 7. 还需要定的

1. **`tool_filter` 收进 `config` 还是保留独立列？** —— 已定：**保留独立列**。它是
   **我们的策略**，不是 MCP 客户端配置的一部分。（`config` 列本身也已经删了。）

2. **租户开关默认关，会不会挡住产品预期？**
   现网 4 行全是 seed 数据，没有真实用户，所以默认关不影响谁。但要确认产品是否希望
   开箱即用。

3. **市场条目的更新怎么传播？**
   现在的设计是 server 行共享，所以平台改了 URL 所有人跟着变（像应用商店推更新）。
   代价是用户不能给市场里的 server 加个自己的 header。真出现这个需求再加"安装时复制
   成自己的实例"，那是加法不是改法。

4. **SSRF 防护做到哪一档？**
   P0 只挡了协议（必须 http/https）。真防护要解析 DNS 查网段、防 TOCTOU 和重定向。
   今天能建 server 的只有管理员，风险面已知；**P2 放开用户自己填 URL 之前必须补**。

---

## 8. 本版相对第一版改了什么

| | 第一版（未评审） | 本版（实测后） |
|---|---|---|
| P0 验收「引擎能调通」 | 假定下发链路是好的 | §2.2 下发丢 schema，必须先修，拆成 P0.0 |
| 「capabilities/resolve 加 MCP 分支」 | 假定 capabilities 在链路上 | §2.3 它是死代码，该改的是 `/internal/agent-config` |
| P2 只提六处 platform 硬过滤 | | §2.3 还得先修 `my__` 前缀的不对称，否则放开了也调不通 |
| 连通性检查 | 没提 | §2.9 三列从来没被写过，P0 补 `/test` |
| 发给远端的工具名 | 没提 | §2.4 带着我们的前缀，远端不认——真链路才抓得到 |
| 「现在搬（分表）代价是零」 | 成立 | §4.1 P0 之后会有真实数据，不再是零 |
| `/sync` 搬去 lea-config | 整个搬 | §4.6 拆两跳，别让 lea-config 开始打外网 |
