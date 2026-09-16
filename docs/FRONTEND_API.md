# lea-agent 前端接口契约

> 面向前端开发。本文档描述**浏览器实际调用的全部接口**，以 `packages/lea-agent` 源码为准，
> 校验日期 2026-08-13。
>
> ⚠️ 不要看 `docs/API_CONTRACT.md` —— 那份是老系统 `lifelong-edu` 的接口规格，
> 留着做迁移参照用，路径全是 `/api/user/super-agent/*`，跟本平台一个都不对应。
>
> ⚠️ 也不要看 `docs/02-技术方案/E2E_FLOW.md` 第 7-11 行的"生产路径" —— 那里写的
> `lea-server:8000` 已经不在调用链上了。

---

## 1. 只有一个后端地址

浏览器**只跟 lea-agent（`:8100`）说话**。KB、记忆、工具、解析这些服务在内网，前端碰不到，
也不需要知道它们存在。唯一的例外是 Agent CRUD —— 那组接口挂在 lea-agent 上做反向代理，
真实实现在 lea-config，但对前端来说仍然是同一个域。

开发环境 `frontend-user/vite.config.ts` 已经把 `/api` 代理到 `127.0.0.1:8100`。

## 2. 接口总览

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/chat/stream` | **发消息并在同一条连接上回 SSE（主链路）** |
| GET | `/api/chat/stream/:session_id` | **断线重连**：从 `?from=` 补齐事件再接直播 |
| GET | `/api/chat/status/:session_id` | 这个会话此刻还在生成吗 |
| POST | `/api/chat/send` | 发消息（立即返回，结果走 SSE）—— 旧链路 |
| POST | `/api/chat/stop/:session_id` | 中断当前回答 |
| POST | `/api/chat/attachments/upload` | 上传附件 |
| POST | `/api/sessions` | 新建会话 |
| GET | `/api/sessions` | 会话列表 |
| GET | `/api/sessions/:id/messages` | 历史消息 |
| DELETE | `/api/sessions/:id` | 归档会话 |
| GET | `/api/files/list` | 列出工作区产物 |
| GET | `/api/files/download` | 下载产物 |
| ANY | `/api/user/agents`、`/api/user/agents/*` | Agent CRUD（反代 lea-config）|
| GET | `/health` | 健康检查（无需鉴权）|

## 3. 通用约定

### 鉴权

`/api/*` 全部需要 `Authorization: Bearer <jwt>`。HS256，claims 至少要有 `sub`；
可选 `tenant_id`（缺省 `"default"`）和 `roles`（字符串数组，注意是复数）。

三种 401，body 都是 `{"error": "..."}`：

| 触发 | error |
|---|---|
| 没带 Authorization 头 | `missing token` |
| 签名错 / 过期 / 格式错 | `invalid token` |
| 缺 `sub` claim | `missing sub claim` |

**没有 refresh token**。401 就是重新登录，前端要有全局拦截。

`tenant_id` / `user_id` 一律从 token 里取，请求里带这两个字段不起作用（反代那条会把
客户端传的 `X-Tenant-ID` / `X-User-ID` 覆盖掉）。

### 错误格式

除两个例外，所有错误都是 `{"error": "<描述>"}`：

- `/api/user/agents/*` 反代失败时返回 502，body 是纯文本不是 JSON
- `OPTIONS` 预检返回 204 空 body

### 时间格式

RFC3339 带纳秒和时区偏移，例如 `2026-07-29T10:04:05.123456789+08:00`。**不是 UTC**，
偏移量取决于服务器时区，前端要按带偏移的格式解析。

### CORS

`Access-Control-Allow-Origin: *`，不允许 credentials。token 必须放在 header 里，
放 cookie 不会生效。

### 一个必须理解的分裂

数据在三个地方，生命周期不同：

| 存放位置 | 内容 | 谁读它 | 活多久 |
|---|---|---|---|
| 进程内存 | 正在跑的 turn（cancel 函数、订阅者） | `send` / `stop` | pod 换了就没 |
| Redis | 本轮的 SSE 事件日志 | `GET /chat/stream/:id` 续传 | **5 分钟** |
| MySQL | 定稿的消息、会话列表、产物 | `sessions` / `messages` / `files` | 永久 |

**从历史列表点开一个旧会话，能读到消息，但没有可续传的流** —— 5 分钟前结束的那一轮日志
已经过期了，`GET /chat/stream/:id` 会返 410。这是正常的，不是错误：那一轮的内容已经在
`/messages` 里了。

多副本部署下这三条路都不需要会话亲和：

- `POST /api/chat/stream` —— 干活和事件流共用一条 TCP 连接，物理上必然同进程。
- `GET /api/chat/stream/:session_id` —— 读 Redis，跟哪个 pod 在跑无关。
- `stop` / `status` —— 落到没有这个会话的副本时，服务端会转发给持有执行权的那个 pod。

⚠️ **以上依赖服务端配了 `REDIS_URL`。** 没配（单实例部署）时 `GET /chat/stream/:session_id`
退回读进程内存，于是老的三条限制回来了：必须同副本、必须先连流再发消息、重启即失。
前端不需要区分这两种模式 —— 按下面文档写的做，两种部署都能工作，只是单实例下断线会丢内容。

---

## 4. 聊天

### POST /api/chat/stream（推荐）

发消息并在**同一条连接**上回 SSE。请求体跟 `send` 一样，多一个 `on_disconnect`：

```json
{
  "session_id": "uuid",
  "agent_id": 0,
  "message": "帮我分析这份数据",
  "attachment_ids": [12, 13],
  "on_disconnect": "continue"
}
```

`on_disconnect` 决定**回答还没生成完、客户端就断了**（关标签页、断网、LB 空闲超时）时
服务端怎么办：

| 取值 | 行为 | 什么时候用 |
|---|---|---|
| `"continue"`（默认） | turn 继续跑完并落库，之后可从 `messages` 取回 | 长任务（PPT、报告）—— 用户切个网不该让三分钟白跑 |
| `"cancel"` | 立刻中断，等价于调 `stop` | 随手问一句 —— 关了标签页就该省下 token |

不传或传了无法识别的值都按 `continue` 处理。**注意服务端分不清"关标签页"和"进隧道"**，
两者在 TCP 上一模一样，所以 `cancel` 的代价是一次网络抖动就可能弄死正在等的回答。

**200** 响应体直接就是事件流（`text/event-stream`）。事件顺序：

1. `session` —— `{"session_id": "uuid"}`。新建会话时前端从这儿拿 id（旧链路是从
   `send` 的 JSON body 拿的，现在整个 body 是事件流）。**这条没有 `id:` 字段**，
   见下面那条注意。
2. 之后是 `start` / `delta` / `thinking` / `tool_event` / `result` / `ask_user` / `done`，
   跟续传端点同一套（见 §5）。

⚠️ **除 `session` 之外的每个事件都带 `id:`，前端必须把它记下来当续传游标。** 断线重连时
拿最后收到的 `id` 去 `GET /api/chat/stream/:session_id?from=<id>`。`session` 事件不进事件
日志所以没有 `id` —— 如果前端无脑记录 `event.lastEventId`，它在收到 `session` 时会是空串，
按 0 处理（从头重放）也是对的，只是多收一遍已有内容。

流里还会掺**SSE 注释行**（`: keepalive`），静默满 20 秒发一次，防中间的反代按空闲超时
掐连接。按 SSE 规范以 `:` 开头的行就是注释，解析时整行跳过即可（成熟的解析库都已经这么做，
自己写的话别忘了这条 —— 漏掉会当成一个空事件）。

错误在写响应头之前返，所以拿得到真状态码：

| 状态码 | 场景 |
|---|---|
| 400 | JSON 解析失败 |
| 403 | `session_id` 不属于当前用户 |
| 409 | 罕见异常，见下 |
| 503 | 会话数打满（`Retry-After: 10`），或**校验不了会话归属**（`Retry-After: 5`，见下）|
| 500 | 其他 |

两种 503 要分开处理，body 不同：

| body | 含义 | 前端该做什么 |
|---|---|---|
| `server busy, retry shortly` | 服务端会话数打满 | 等 10 秒重试 |
| `cannot verify session ownership, retry shortly` | 数据库暂时不可达，服务端**不敢**确认这个 `session_id` 是不是你的 | 等 5 秒重试。**别提示"无权访问"** —— 这不是权限问题 |

第二种是安全设计的一部分：带 `session_id` 的请求，如果服务端内存里没有这个会话
（多副本下很常见，或者服务刚重启），必须查库确认归属才能继续。查不了就拒，因为
放行意味着任何人拿别人的 `session_id` 就能把别人的对话历史读进模型上下文。
不带 `session_id` 的新会话不受影响 —— 它压根不需要校验归属。

**回答生成中再发一条消息 = 打断上一轮，然后开始新的一轮。** 不管落到哪个副本都是这个
行为（跨副本时服务端会先请远端让位，实测约 260ms）。前端不需要先 `stop` 再发。

409 只在让位失败时出现：跑那一轮的 pod 被强杀、控制信令丢了、或者收尾超过 5 秒。当成
"稍后重试"处理即可，不要当常态路径设计 UI。

### POST /api/chat/send（旧链路）

```json
{
  "session_id": "uuid",
  "agent_id": 0,
  "message": "帮我分析这份数据",
  "attachment_ids": [12, 13]
}
```

字段全部可选：

- `session_id` 不传就新建一个
- `agent_id` 传 `0` 走租户默认的 super-agent
- `attachment_ids` 是 `/attachments/upload` 返回的 `id` 数组（int64）

**202** `{"session_id": "uuid", "status": "processing"}`

只是受理，回答内容完全走 SSE。

| 状态码 | 场景 |
|---|---|
| 400 | JSON 解析失败 |
| 409 | 让位失败，见上一节 |
| 503 | 会话数打满（带 `Retry-After: 10`）|
| 500 | 加载 agent 配置失败（`load agent config: ...`）或 Send 失败 |

配了 `REDIS_URL` 时**没有时序要求**了 —— 后连的 `GET /chat/stream/:session_id` 会从事件
日志把本轮从头补给你。没配时老的时序要求仍然成立：`broadcast` 对没有订阅者的事件直接丢弃
（非阻塞 select + default），晚连会丢掉 `start` 和开头几个 `delta`。

### GET /api/chat/stream/:session_id（断线重连）

**这是唯一的重连入口。** 主链路 `POST /chat/stream` 是单连接，断了不能重发（重发 POST 会
真的再问模型一遍，多花一次 token 还会插一条重复的用户消息）。

| 参数 | 说明 |
|---|---|
| `from` | 最后收到的 SSE `id`。不传或传 `0` = 从本轮开头全量重放 |

**200** `text/event-stream`。服务端先把 `from` 之后的事件一次性补出来，然后接上直播继续
推，直到本轮 `done` 或客户端断开。补出来的事件跟直播**逐字节一致**，前端不需要为重放写
另一套解析。

事件都带 `id:`，续传过程中要继续更新游标 —— 二次断线时用的是新游标。

```js
// 重连
const url = `/api/chat/stream/${sessionId}?from=${lastEventId ?? 0}`;
```

| 状态码 | 场景 | 前端该做什么 |
|---|---|---|
| 200 | 正常续传 | 接着消费事件 |
| 400 | `from` 不是非负整数 | 修 bug |
| 403 | 不是自己的会话 | |
| 404 | 会话在库里也不存在 | |
| **410** | **事件日志没了**，body 带 `"code": "log_expired"` | **退回 `GET /api/sessions/:id/messages`** |
| 503 | 读不了事件日志（Redis 抖动） | 退避重试，或退回 `/messages` |

410 的完整 body：

```json
{
  "error": "event log unavailable for this session",
  "code": "log_expired",
  "hint": "fall back to GET /api/sessions/:id/messages"
}
```

三种情况会走到 410，前端**不需要区分**（处理方式都一样）：日志过了 5 分钟保留期、这个
会话从没跑过 turn、写日志时 Redis 出问题导致服务端主动作废了整条流（宁可让你退回拉历史，
也不给一段中间缺字的正文）。

⚠️ **保留窗口是 5 分钟，容量上限约 1 万个事件。** 逐 token 写，所以极长的回答会把开头的
内容挤掉 —— 这时 `from=0` 补到的是"从中间开始"的正文。要完整正文一律去 `/messages`。

订阅缓冲（未启用日志时的退路）是 128 条事件，前端消费慢了会**静默丢事件**。

跟主链路一样会掺 `: keepalive` 注释行（静默 20 秒一次），解析时整行跳过。

### GET /api/chat/status/:session_id

**200** `{"session_id": "uuid", "running": true}`

跨副本有效。断流之后靠它决定下一步 —— 没有它的话"还在写"和"写完了但我没收到"在
`/messages` 上长得一模一样（assistant 消息要等整轮结束才落库，所以两种情况都表现为
"列表里没有新消息"）。

| 状态码 | 场景 |
|---|---|
| 403 | 不是自己的会话 |
| 404 | 会话不存在 |
| 503 | 判断不了（控制面/库不可达）。**别当 `running: false` 处理** —— 会过早放弃正在生成的回答 |

### POST /api/chat/stop/:session_id

无 body。

**200** `{"session_id": "uuid", "stopped": true}`，跨副本转发时多一个 `"forwarded": true`
（纯排查信息，前端可忽略）。

`stopped: false` 表示这一轮已经自己结束了 —— 用户点停的同一瞬间回答刚好完成，属于正常
竞态，**不是错误**，前端不用提示。

| 状态码 | 场景 |
|---|---|
| 404 | 全集群都没有在跑的 turn（服务端查过执行锁，不是猜的） |
| 403 | 不是自己的会话 |
| 503 | 够不着跑这一轮的副本。**不是 404** —— 真相是"不知道，信号也送不出去"，前端该提示失败让用户重试 |

⚠️ `stopped: true` 只表示**信号已发出**。权威的完成信号是 SSE 流上的 `result` + `done`。
按钮状态应该等流上的 `done`，不要收到 200 就立刻切回"可发送"。

停掉之后 SSE 会收到 `result`（`stopped: true`，`text` 是已经流出去的部分）然后 `done`，
**不会收到 `error`**。已经生成的内容会落库，刷新后仍能看到停在哪里；如果一个字都没生成，
库里内容是 `[已中断]`。

---

## 5. SSE 事件

`id:` + `event:` + `data:` 三行一组，空行分隔。**七种事件类型，其中只有两种是 JSON。**

`id:` 是本会话内单调递增的序号，用途只有一个：断线时拿最后一个 `id` 去
`GET /chat/stream/:session_id?from=<id>` 续传。它**不跨会话唯一**，也不是消息 id，
别拿它做别的用途。握手用的 `session` 事件没有 `id`（它不进事件日志）。

⚠️ **两条流都用不了原生 `EventSource`**：主链路是 POST（`EventSource` 只能 GET），续传
链路虽然是 GET 但要 `Authorization` 头（`EventSource` 不能设请求头）。两条都得用
`fetch` + `ReadableStream` 自己解析，或者用 `@microsoft/fetch-event-source` 之类的库。

因此重连也得自己做：服务端不发 `retry:`，也**不读 `Last-Event-ID` 请求头**，只读
`?from=` 查询参数。

| event | data 类型 | 说明 |
|---|---|---|
| `start` | 字面量 `<nil>` | 回答开始，**忽略 payload** |
| `delta` | 裸文本 | 正文增量，直接追加 |
| `thinking` | 裸文本 | 思考过程增量 |
| `tool_event` | JSON | 工具调用，见下 |
| `result` | JSON | 本轮定稿 |
| `error` | 裸文本 | 错误描述 |
| `done` | 字面量 `<nil>` | 流结束，**忽略 payload** |

**`delta` / `thinking` / `error` 不要 `JSON.parse()`** —— 它们是裸字符串，直接取
`event.data`。`start` / `done` 的 payload 是字面的 `<nil>` 四个字符（Go 的 `fmt.Sprint(nil)`
产物），解析会报错，忽略即可。

多行文本会被拆成多个 `data:` 行，按 SSE 规范要用 `\n` 拼回来。既然上面说了必须自己解析，
这条就是最容易踩的坑：**只读第一个 `data:` 行会静默截断**，表现是代码块和长文本莫名少几行。
注意 `\r` 不能正确往返。

一轮里 `result` 和 `error` **只会出现一个**，之后必定有 `done`。用户中断走 `result`
不走 `error`。

### result

```json
{"text": "完整回答", "iterations": 3, "stopped": false}
```

`stopped: true` 有两种来源 —— 用户主动停，或者引擎因为工具被拒而终止。**从这个字段
分不出是哪种**。

### tool_event

```json
{
  "type": "started",
  "toolName": "code_interpreter",
  "toolUseID": "toolu_xxx",
  "input": { "language": "python", "code": "..." },
  "time": "2026-07-29T10:04:05+08:00"
}
```

`type` 取值：`started` / `progress` / `output` / `completed` / `failed` /
`agent_delta` / `agent_usage`。同一个 `toolUseID` 会依次出现 `started` →
（可选 `progress`）→ `completed` 或 `failed`，前端按 `toolUseID` 聚合。

其余字段都是 `omitempty`，用到再取：

| 字段 | 类型 | 说明 |
|---|---|---|
| `message` | string | **引擎的状态描述，不是工具输出**。只有八个固定值：`started` / `completed` / `completed with error` / `failed` / `cancelled` / `unknown tool` / `blocked by hook` / `denied:...`。别拿它当结果展示 |
| `output` | `[{stream, text}]` | 真实输出，按流分类 |
| `outputTotal` / `outputTruncated` | int / bool | 输出被截断时 |
| `data` | any | 见下 |
| `files` | `[{path, kind}]` | `kind` 是 `read` 或 `matched` |
| `image` | `{media_type, data, url}` | `data` 是 base64 |
| `inputTokens` / `outputTokens` / `durationMs` | int | |
| `parentToolUseID` / `agentName` | string | 子智能体归属（暂未启用）|

`output[].stream` ∈ `stdout` / `stderr` / `match` / `info` / `diff_add` / `diff_del` /
`diff_context`。

`progress` 事件的 `data` 有两种形状：

- `code_interpreter`：沙箱结果 `{artifacts, stdout, stderr, duration_ms}`
- 其他平台工具：`{result: "200字摘要", result_full: <完整值>}`

---

## 6. 会话

### POST /api/sessions

```json
{"agent_id": 3}
```

**201** `{"session_id": "uuid"}`

只在内存里建会话，**`chat_sessions` 表要等第一次 `send` 才写入**。所以刚建完就调
`/api/files/list` 会得到 `store: "pending"` 而不是 404。

| 状态码 | 场景 |
|---|---|
| 400 | JSON 解析失败 |
| 500 | 加载配置失败 |

### GET /api/sessions

无参数。

```json
{
  "sessions": [{
    "id": "uuid",
    "title": "会话标题",
    "agent_id": 7,
    "message_count": 12,
    "last_active_at": "2026-07-29T10:04:05+08:00",
    "created_at": "2026-07-28T09:00:00+08:00",
    "is_archived": false
  }]
}
```

三个坑：

1. **`agent_id` 是这个会话的"激活 agent"**（专家/专家团），来自 `active_agent_id` 列。
   **`null` 表示用默认 superagent**，不是"没有数据" —— 列里的 `0` 会被转成 `null`。
2. **没有分页，硬编码返回最近 50 条**，`limit` / `offset` 不可传，也没有总数。超过 50 个
   会话的用户会静默看不到更早的。
3. DB 不可用时返回 **200 `{"sessions": []}`** 而不是报错 —— 空列表可能意味着"没有会话"
   也可能意味着"数据库挂了"，从响应上分不出来。

排序 `last_active_at DESC, id DESC`，已归档的不返回。`sessions` 始终是数组不会是 null。

### GET /api/sessions/:id/messages

```json
{
  "session_id": "uuid",
  "messages": [
    {"id": 1, "role": "user", "content": "...", "created_at": "..."},
    {"id": 2, "role": "assistant", "content": "...", "created_at": "...", "extra": {}}
  ]
}
```

`role` 是 `user` 或 `assistant`。**`extra` 只出现在 assistant 消息上，且没内容时整个
字段不存在** —— 前端要按"可能缺失"处理，见 §8。

按 `id ASC` 排，**一次返回全部历史，没有分页**。长会话要留意首屏性能。

| 状态码 | 场景 |
|---|---|
| 503 | `persistence unavailable` |
| 404 | 会话不存在 |
| 403 | 不是自己的会话 |

### DELETE /api/sessions/:id

软删除（`is_archived = 1`）。**200** `{"ok": true}`

**404** 把三种情况混在一起了：不存在、已归档、不是你的。

⚠️ DB 不可达时会跳过归档但**仍然返回 200 `{"ok": true}`** —— 前端乐观更新后刷新一下，
会话可能还在。

---

## 7. 附件与产物

### POST /api/chat/attachments/upload

`multipart/form-data`：

| 字段 | 必填 | 说明 |
|---|---|---|
| `file` | 是 | |
| `session_id` | 否 | |

**上限 30 MiB。扩展名不再是白名单** —— 任何类型都能传。下面这张表现在只决定
**要不要打 parser 抽文本**，抽不出文本的文件（zip / sqlite / ipynb）照样落地，
模型在沙箱里自己处理。不在表里时 `media_type` 是 `file`。

| media_type | 扩展名 |
|---|---|
| `text` | pdf docx doc pptx ppt md txt csv json |
| `image` | png jpg jpeg webp gif bmp |
| `audio` | mp3 wav m4a flac ogg |
| `video` | mp4 avi mkv mov wmv flv webm |

**200**

```json
{
  "id": 123,
  "filename": "报告.pdf",
  "file_type": "pdf",
  "file_size": 4096,
  "media_type": "text",
  "text_preview": "提取出的正文前 200 字节"
}
```

拿到 `id` 之后放进 `send` 的 `attachment_ids`。

⚠️ `text_preview` 是按**字节**截断的，可能把一个 UTF-8 字符切断，尾部出现乱码。当展示
用的近似值看，别用来做判断。

| 状态码 | error |
|---|---|
| 400 | `missing file field` |
| 404 | `session not found` —— 带了 `session_id` 但它是别人的 |
| 413 | `file too large (>30MB)` |
| 502 | `文件未能保存，请稍后重试` |
| 503 | `persistence unavailable` |
| **507** | **存储配额已满，见下** |

#### 507：唯一一个"重试没用"的失败

```json
{ "error": "存储空间不足：已用 1980MB / 上限 2048MB，这个文件需要 12MB。请在文件管理里删掉一些不需要的文件后重试。", "code": "storage_quota_exceeded" }
```

**必须靠 `code` 判，不要匹配 `error` 文案**（文案会改）。这个错误重试一万次都是 507，
正确的反应是把用户带到文件库去删东西，而不是弹一个"请重试"。`error` 那句话本身就是
给用户看的，直接展示即可 —— 个人配额说"删文件"，租户配额说"联系管理员"，因为后者
用户自己解决不了。

**原文件会落对象存储**（内容寻址 `uploads/<tenant>/<sha256>.<ext>`），可以通过
`GET /api/chat/attachments/:id/download` 取回。响应里的 `downloadable` 为 `false` 时
说明当时对象存储不可达、只有文本存下来了，这时下载会返回 410。

### GET /api/chat/attachments/:id/download

按 `attachment_id` 取回用户上传的原件，透传 `Content-Type` / `Content-Length` /
`Content-Disposition`。

走 id 而不是路径，是因为原件的权威位置是对象存储上的 blob：会话归档后共享存储那份
已经清理掉了，blob 还在。

| 状态码 | 说明 |
|---|---|
| 400 | id 不合法 |
| 404 | 不存在**或**不是你的（故意不区分：id 自增，分开报就是一个可枚举的存在性预言）|
| 410 | `该附件上传时未保存原件，无法下载` |

### GET /api/files/list

沙箱工作区里的产物（模型跑代码生成的 xlsx / png / pptx 等）。

| 参数 | 必填 |
|---|---|
| `session_id` | 是 |
| `show_hidden` | 否，只认字面 `true` |

成功时**透传 lea-tools 的响应**（状态码、Content-Type、body 原样转发），schema 归
lea-tools 定。

| 状态码 | 说明 |
|---|---|
| 400 | `session_id required` |
| 200 | `{"files": [], "truncated": false, "store": "pending"}` —— 会话还没落库，故意不报 404 |
| 403 / 503 / 502 | 非本人 / 库不可用 / 上游不可达 |

### GET /api/files/download

| 参数 | 必填 |
|---|---|
| `session_id` | 是 |
| `path` | 是 |

透传 `Content-Type` / `Content-Disposition` / `Content-Length`。

**限流：每 60 秒 30 次**，按 `tenant_id + user_id` 计。超了返回 **429**
`{"error": "download rate limit exceeded, try again later"}`。限流器在进程内存里，
多副本时是每副本独立计数，重启清零。

客户端超时 60 秒，大文件可能被截断。

⚠️ 两个 files 接口都是**先写状态码再拷 body**。上游中途断开时前端看到的是 200 + 不完整
内容，没有错误标记。要校验实收字节数和 `Content-Length` 是否一致。

---

## 7.5 用户文件库（跨会话）

上面那几条都是**会话内**的：附件挂在某一轮对话上，工作区产物随会话 30 天回收。
这一节是长期的那一层 —— "我的文件"，在 A 对话里生成的报告能在 B 对话里接着用。

### 一个库，两张表

用户看到的是**一个**列表，底下是两张表，方向相反：

| kind | 来源 | 谁写的 |
|---|---|---|
| `upload` | 用户传给我们的原件 | `POST /api/chat/attachments/upload` |
| `artifact` | 我们交付给用户的成品 | 模型显式调 `deliver_artifact` 工具 |

合并展示是因为用户不关心这个区别；分开存是因为**降级方向相反**：上传超配额硬拒
（用户还拿着原件，删几个重传很便宜），交付超配额软放行（那背后是几分钟的模型劳动，
丢掉换不来任何东西 —— 这时行还在、`stored` 为 `false`）。

`kind` 必须一路带着：删除和导入都要知道去哪张表。**两张表的 id 各自自增会撞**，
`upload#7` 和 `artifact#7` 是两个不同的文件 —— 前端做列表 key 时要用 `kind:id`。

> ⚠️ **成品只有模型显式声明的才进来。** 不猜、不按扩展名白名单扫、不看修改时间。
> 零条记录的意思是"这一轮没有产出可交付的东西"，不是"我们没检测出来"。

### GET /api/user/storage

```json
{
  "upload_bytes": 12345678, "artifact_bytes": 2345678, "total_bytes": 14691356,
  "quota_bytes": 2147483648,
  "upload_count": 12, "artifact_count": 3,
  "percent": 0.68
}
```

**`quota_bytes` 为 `0` 表示不限额**，这时 `percent` 恒为 `0` —— 前端要靠
`quota_bytes === 0` 决定画不画进度条，不要靠 `percent`。

用量按 `SUM(DISTINCT sha256, size)` 算：同一份内容传十次只占一份（内容寻址）。
所以**删掉一个文件占用未必会降** —— 可能还有别的行指向同一个对象。前端不要自己
减，删完重新拉一次。

### GET /api/user/files

| 参数 | 默认 | 说明 |
|---|---|---|
| `kind` | 空 = 两种都要 | `upload` \| `artifact` |
| `limit` | 50 | 上限 200 |
| `offset` | 0 | |

```json
{
  "files": [
    {
      "kind": "artifact", "id": 9, "filename": "季度分析.xlsx",
      "title": "Q3 销售分析", "description": "按区域拆分的三张透视表",
      "size": 45678, "media_type": "application/vnd...", 
      "session_id": "sess-abc", "created_at": "2026-09-09 14:03:11",
      "stored": true, "can_import": true
    }
  ],
  "limit": 50, "offset": 0
}
```

- `title` / `description` 只有成品有 —— 模型交付时写的，用户几周后靠这两句认出它。
- **`stored: false` 的行不能下载也不能导入**，按钮要置灰并标出来（上传件是当时对象
  存储不可达，成品是配额满时的软放行）。让用户点了才拿到 410 是最差的做法。
- 两段在**内存里合并后按 `created_at` 倒序**，不是 SQL UNION。`limit` 是**每张表**
  的上限，所以不带 `kind` 时最多可能返回 `2 × limit` 条。

### DELETE /api/user/files/:kind/:id

```json
{ "status": "ok", "deleted": true, "filename": "季度分析.xlsx", "object_deleted": false }
```

`object_deleted: false` 且 `deleted: true` 是**正常情况**，不是失败：同租户内还有
别的行指向同一份内容（内容寻址去重），对象得留着。前端不用区分这两种，用户视角
文件已经没了。

`deleted: false` 也返回 200 —— 并发下另一个请求先删了，用户要的结果已经成立。

| 状态码 | 说明 |
|---|---|
| 400 | `kind 只能是 upload 或 artifact` / `bad id` |
| 404 | 不存在或不是你的（同样不区分）|

### POST /api/sessions/:id/files/import

把库里的文件物化进某个会话，让模型能读能改。

```json
{ "kind": "artifact", "id": 9 }
```

**200**

```json
{
  "status": "ok", "attachment_id": 456,
  "filename": "季度分析.xlsx", "file_type": "xlsx",
  "media_type": "text", "file_size": 45678,
  "staged_name": "季度分析.xlsx", "sandbox_path": "/mnt/uploads/季度分析.xlsx",
  "has_text": false
}
```

> **`attachment_id` 是这个响应里唯一必须用的字段。** 把它放进下一条消息的
> `attachment_ids`，模型才知道这个文件存在 —— 光物化字节是不够的，注入只认
> `attachment_ids`。这条跟上传后那条路完全一样，前端可以共用同一套处理。

几件值得知道的事：

- **导入是拷贝，不是引用。** 模型在 B 对话里改的是副本，文件库里的原件一个字节不动。
- **不多占存储配额**：新登记行带同一个 `sha256`，配额只算一次。
- 可以在**会话还没落库时**导入（新开对话 → 导入 → 发第一条消息）。会话行是第一条
  消息才写的，这条路径必须能走通。
- `has_text: false` 的成品意味着没有提取过文本，模型只能进沙箱处理它 —— 对一份它
  自己刚生成的文件来说，那本来就是对的做法。

| 状态码 | 说明 |
|---|---|
| 400 | `kind 只能是 upload 或 artifact` / `bad id` |
| 404 | 会话不是你的，或文件不是你的 |
| **410** | `这个文件没有保存原件，无法导入到会话` —— 即 `can_import: false` |
| 502 / 503 | 对象存储故障 / 没有共享存储 |
| 507 | **会话**工作区配额满（跟用户配额是两条线）|

导入**不需要重建引擎**，下一条消息自然生效 —— 沙箱按路径实时读共享存储，注入靠
`attachment_ids`，两条都不经过引擎对象。所以响应里没有 `applies` 字段。

---

## 8. `extra` 结构（消息可视化）

`GET /api/sessions/:id/messages` 里 assistant 消息的 `extra` 字段，原样透传数据库里的
JSON。**四个顶层 key 全部是条件出现的**，都没有时整个 `extra` 不存在。

```json
{
  "tool_calls": [],
  "sandbox_calls": [],
  "reasoning_blocks": [],
  "segments": []
}
```

### segments —— 渲染顺序的唯一来源

有它就按它渲染。`tool_calls` / `sandbox_calls` 只是首次出现的顺序，**不表示它们在文本
里的位置**。

```json
[
  {"kind": "text",     "content": "我先查一下资料。"},
  {"kind": "tool",     "index": 0},
  {"kind": "text",     "content": "找到了，接着跑代码。"},
  {"kind": "sandbox",  "index": 0},
  {"kind": "text",     "content": "完成。"}
]
```

`kind` ∈ `text` / `thinking` / `tool` / `sandbox`。前两种带 `content`，后两种带 `index`
指向对应数组的下标（避免对象重复）。相邻同类文本段会合并。

**纯文本回答没有 `segments`** —— 只有一段 text 时不落库（跟 `content` 重复）。这时直接
渲染 `content` 即可。

### tool_calls

```json
{
  "toolCallId": "toolu_xxx",
  "tool": "kb_search",
  "input": {"query": "..."},
  "status": "success",
  "result": "命中 5 条",
  "result_full": {},
  "durationMs": 42
}
```

- `status` ∈ `running` / `success` / `error`
- `input` 可能是 `null`（参数缺失或解析失败）
- `result` 截断到 2000 字符；`result_full` 只在工具提供了完整值时出现
- `durationMs` 只在 > 0 时出现

⚠️ **`status` 可能永久停在 `running`** —— 回答被中断、工具的完成事件还没到就落库了。
前端需要兜底显示，不能等一个永远不来的终态。

### sandbox_calls

```json
{
  "callId": "toolu_xxx",
  "language": "python",
  "codePreview": "import pandas as pd...",
  "status": "success",
  "artifacts": ["output/report.xlsx", "output/chart.png"],
  "stdoutPreview": "done\n",
  "stderrPreview": "",
  "durationMs": 1880,
  "outputTruncated": false,
  "message": ""
}
```

- `codePreview` 前 200 字符
- **`artifacts` 是联合类型**：可能是 `["文件名", ...]`，也可能是
  `[{name, path, size}, ...]`，没有产物时是 `null`。前端要逐项判断元素类型
- `stdoutPreview` / `stderrPreview` 截断到 2000
- `message` 只在两个 preview 都为空时才有值，内容是引擎的状态描述而非沙箱输出 ——
  **不要当 stdout 展示**

拿 `artifacts` 里的路径去 `/api/files/download?session_id=...&path=...` 下载。

### reasoning_blocks

固定一个元素、固定三个 key：

```json
[{"expertId": "__model__", "expertName": "思考过程", "reasoning": "完整思考文本"}]
```

**不截断** —— 长思维链会原样落库，前端默认折叠。

### 大小提醒

2000 字符的上限是**按字段**的，`extra` 整体没有上限：text segment 的 `content` 不限长，
`result_full` 可以是任意结构。历史消息一次全量返回，前端渲染长会话时要考虑虚拟滚动。

---

## 9. Agent CRUD（反代）

`/api/user/agents` 和 `/api/user/agents/*` 反代到 lea-config，路径重写
`/api/user/agents/xxx` → `/agents/xxx`。请求和响应 schema 归 lea-config，可以看它的
`/docs`（内网）。

常用的几个：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/user/agents` | 可用 Agent 列表 |
| GET | `/api/user/agents/default` | 租户默认 Agent |
| GET | `/api/user/agents/{id}/skills` | 该 Agent 绑定的技能（输入框 `/` 面板的候选）|
| GET | `/api/user/agents/{id}/kbs` | 绑定的知识库 |

Agent 对象里有 `welcome_message` 和 `example_questions`，用来渲染空会话的欢迎页。

上游挂掉时是 502 + 纯文本，不是 JSON。

---

## 10. 前端接入顺序建议

1. 登录拿 token → 全局 `Authorization` 头 + 401 拦截跳登录
2. `GET /api/user/agents` + `/default` → 助手选择器；空态渲染 `welcome_message` 和
   `example_questions`
3. `GET /api/sessions` → 左侧会话列表（记住只有 50 条、`agent_id` 是 null）
4. 点开历史会话 → `GET /api/sessions/:id/messages`，按 `segments` 渲染，**此时不要连流**
5. 发消息 → `POST /api/chat/stream`（`fetch` + `ReadableStream`，不要 `EventSource`）
6. 消费事件：`delta` 追加正文、`thinking` 追加折叠区、`tool_event` 按 `toolUseID` 聚合成
   卡片、`result` 定稿、`done` 收尾。**每收一个事件就更新 `lastEventId`**
7. 停止按钮 → `POST /api/chat/stop/:session_id`，`stopped:false` 不提示；按钮状态等流上的
   `done`，不要收到 200 就切回可发送
8. 断线兜底 → 见下一节
9. 附件 → `upload` 拿 `id` → 塞进 `attachment_ids`
10. 产物 → `sandbox_calls[].artifacts` 渲染文件卡片 → 点击走 `/api/files/download`

### 断线兜底的完整流程

这套是 §4 三个接口配合出来的，前端照抄即可：

```
流断了（fetch 抛错 / reader 提前结束，且没收到 done）
  ↓
GET /api/chat/status/:session_id
  ├─ 503 → 退避重试几次，仍失败则提示"连接异常"，走 /messages 兜底
  ├─ running: false → 这轮已经结束了，GET /api/sessions/:id/messages 取最终结果
  └─ running: true  → 还在生成
        ↓
     GET /api/chat/stream/:session_id?from=<lastEventId>
        ├─ 200 → 接着消费（继续更新 lastEventId，可能再断一次）
        ├─ 410 → 日志没了，GET /api/sessions/:id/messages
        └─ 503 → 退避重试；连续失败退回 /messages
```

两个容易做错的地方：

- **`from` 必须用最后一个真收到的 `id`**，不是自己数的事件条数。多一会重复正文，少一会缺字。
- **续传成功后要继续更新游标**，别一直用第一次断线时的值。
- 5 分钟外的老会话不要试续传，直接 `/messages`（试了也只会拿到 410）。

## 11. 已知限制汇总

给前端排期时需要知道的，都不是 bug 而是当前架构的边界：

| 限制 | 影响 |
|---|---|
| 事件日志只留 5 分钟 | 超过就只能从 `/messages` 拿定稿，拿不回逐字过程 |
| 事件日志上限约 1 万条 | 极长回答的开头会被挤掉，`from=0` 补到的正文可能缺开头 |
| 两条流都用不了 `EventSource` | 必须 `fetch` + `ReadableStream` 自己解析 SSE |
| 服务端不读 `Last-Event-ID` 头 | 重连要自己拼 `?from=` |
| 未配 `REDIS_URL` 时无回放 | 单实例部署下断线仍会丢本轮内容、且必须先连流再发消息 |
| 订阅缓冲 128 条（仅未配 Redis 时） | 消费慢会静默丢事件 |
| 会话列表无分页，固定 50 条 | 重度用户看不到更早会话 |
| 消息无分页，全量返回 | 长会话首屏慢 |
| `artifacts` 联合类型 | 需要逐项类型判断 |
| `tool_calls[].status` 可能停在 running | 需要终态兜底 |
| 下载限流每副本独立 | 多副本时实际配额是 30 × 副本数 |
| 文件库两张表各自分页 | 不带 `kind` 时最多返回 2 × `limit` 条 |
| 删文件不保证释放空间 | 内容寻址去重，同租户还有引用时对象保留 |
