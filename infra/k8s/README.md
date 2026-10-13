# CCE 部署清单

面向 CCE（华为云容器引擎，就是 k8s）。中间件用外部实例——现在是自建的
（202.205.160.9 上的 MySQL / Redis），将来换成云服务商托管的只改 ConfigMap 的值，
manifest 和镜像都不动。**所以这里没有 Redis / MySQL 的 Deployment**，那是有意的。

## 服务清单

| 服务 | 端口 | 对外 | 能扩容 | 备注 |
|---|---|---|---|---|
| lea-gateway | 8080 | **是**（Ingress） | 是 | JWT 验签和客户端路由 |
| lea-agent | 8100 | 否 | 是 | 聊天和运行时 |
| lea-agent-worker | - | 否 | 是 | Temporal 长任务执行器 |
| lea-config | 8500 | 否 | 是 | 无状态 |
| lea-rag | 8300 | 否 | 是 | 启动慢，探针延迟给大 |
| lea-tools | 8400 | 否 | 是（新） | 要 root + 共享存储 |

lea-server 不在列——它已经退出调用链（admin API 迁走了）。旧代码已归档到
`packages/lea-server-legacy`，不要再作为运行服务接入。

## Agent Run 执行链

`lea-agent` API 副本只负责鉴权、创建 `agent_runs`、启动 Temporal Workflow 和维持 SSE；
`lea-agent-worker` 从共享 Task Queue 取任务并执行模型、工具和沙箱调用。Worker 把 token
事件写入 Redis Stream，任意 API 副本都能继续向客户端转发。MySQL 是产品状态查询来源，
Temporal 是调度和工作流状态来源，Redis 不承担任务队列职责。

Nginx/Ingress 仍只做 HTTP 负载均衡，不负责把同一会话固定到某个副本。API 和 Worker
可以分别扩缩容：API 看连接数与 SSE 数，Worker 看 Task Queue backlog 和执行并发。

## 客户端入口

浏览器只访问 `lea-gateway:8080` 对应的 Ingress。它按 OIDC/JWKS 验签统一登录签发的
RS256 JWT（issuer 见本节末），随后：

```text
/api/chat/*、/api/sessions/*、/api/files/*  -> lea-agent
/api/user/agents/*、/api/user/skills/*、/api/user/tools/* -> lea-config
/api/user/knowledge-bases/*、/api/user/memories/* -> lea-rag
```

到 `lea-config` 的请求会附带网关从已验签 JWT 提取的 `sub`、`tenant_id`、
`roles`，以及服务间 `LEA_INTERNAL_TOKEN`。因此不要向浏览器公开 `lea-config`，
也不要让客户端自行填写 `X-User-*` 身份头。

网关按 OIDC/JWKS 验证统一登录的 RS256 JWT，默认 issuer 为
`https://passport-ai.ouchn.edu.cn`。以后可由 APISIX 接管 JWT 验签和这组路由；
业务服务的对象级授权不变。

## 构建镜像

```bash
# lea-agent 需要把 agentloop 作为具名上下文传进去。
# go.mod 里有 replace 指向仓库外的 ../../../agentloop，Docker 上下文进不去那儿。
docker build --build-context agentloop=../agentloop \
    -f packages/lea-agent/Dockerfile -t lea-agent:$TAG .

docker build -f packages/lea-tools/Dockerfile  -t lea-tools:$TAG  .
docker build -f packages/lea-config/Dockerfile -t lea-config:$TAG .
docker build -f packages/lea-gateway/Dockerfile -t lea-gateway:$TAG .
docker build -f packages/lea-rag/Dockerfile    -t lea-rag:$TAG    .
```

CI 必须同时 checkout lea-platform 和 agentloop 两个仓库并保持同级目录。少了
`--build-context` 的报错是 `replacement directory ../../../agentloop does not exist`
——指向明确，不会静默出错。

长期更好的做法是给 agentloop 打 tag 发成正式 Go module、删掉 replace。现在没做是
因为两个仓库在同步改，每次改都发版是实打实的摩擦。

## 部署

```bash
# 1. 先执行 migrations/031_agent_runs.sql，再建密钥
#    （值不在仓库里，见 config.yaml 顶部的注释和命令）
kubectl create secret generic lea-secrets --from-literal=...

# 2. 共享存储的 PVC（lea-tools 要）—— 按 CCE 的存储类型自己建，名字叫 lea-shared
# 3. 配置 + 各服务
kubectl apply -f infra/k8s/config.yaml
kubectl apply -f infra/k8s/lea-gateway.yaml
kubectl apply -f infra/k8s/lea-config.yaml
kubectl apply -f infra/k8s/lea-rag.yaml
kubectl apply -f infra/k8s/lea-tools.yaml
kubectl apply -f infra/k8s/lea-agent.yaml
kubectl apply -f infra/k8s/lea-agent-worker.yaml
kubectl apply -f infra/k8s/networkpolicy.yaml
```

Temporal 本身建议用官方 Helm Chart 或云托管实例部署，不在这组业务 manifest 里手写。
首次上线保持 `LEA_AGENT_EXECUTION_MODE=inline`；确认 Temporal namespace 和 Worker 都健康后，
再改成 `temporal` 并滚动更新 `lea-agent`。Worker 可提前部署，它只会等待 Task Queue。

Temporal 模式下，HTTP 进程先把 Run 以 `queued` 状态写入 MySQL，再尝试启动 Workflow。
API 和 Worker 都会按 `LEA_AGENT_DISPATCH_INTERVAL` 扫描未派发 Run，因此进程在“写库成功、
启动 Workflow 之前”崩溃也不会丢任务。Workflow ID 使用 Run ID，重复派发是幂等的。

执行 Activity 默认最多尝试 3 次。Worker 消失或心跳超时后，单 Agent 和星型团队会读取
MySQL 中最近一个 turn checkpoint，从最后一个已完整提交的边界继续，而不是重跑整轮：

```text
用户消息已提交 -> 模型请求 -> 工具批次已完整提交 -> 下一次模型请求 -> 最终回答已提交
                                  ^ Worker 在这里之后退出
                                  新 Worker 从这里继续
```

消息和 checkpoint 在同一个 MySQL 事务里提交，因此不会出现 checkpoint 已前进但对应
消息缺失的状态。累计 iteration 和 token usage 也随 checkpoint 恢复。已经提交最终回答的
run 只做收尾，不会再次调用模型。前端 `chat_messages` 以 `(run_id, role)` 幂等 upsert，
所以 Worker 在业务消息落库后、Run 终态落库前退出，也不会在重试时生成重复对话。

当前有两个明确限制：handoff 团队仍按整条接力链重跑；工具批次只有在全部工具返回后才
提交 checkpoint，因此 Worker 在批次执行中退出时会从上一个边界继续，批次内工具可能
再次执行。写文件、外部 API 等副作用工具仍需由工具侧用幂等键或业务去重保证安全。

`LEA_AGENT_RETRY_LOCK_WAIT` 控制新 attempt 等待旧 Redis 会话锁释放或过期的时间。
MySQL 的 `attempt` 栅栏同时保护消息、checkpoint、摘要和 run 终态，阻止旧 Worker 在失去
执行权后继续回写。

镜像地址和 Ingress 的 host 都标着 `CHANGE-ME` / `:dev`，改完再 apply。

## 上线后必须**实际验证**的四条

这四条的共同点是：**配错了不报错**，服务照常 Running，问题只在特定场景下显现。
所以不能靠"看 pod 起来了"来判断。

1. **流式输出是逐字出现的。**
   Ingress 的 `proxy-buffering: off` 没生效时，用户等半分钟然后整段回答一次性
   出现——看起来像"模型很慢"。这是唯一能确认注解生效的方法。
   CCE 如果用 ELB Ingress 而不是 nginx-ingress，注解名不同，务必测。

2. **停止按钮在多副本下有效。**
   `REDIS_URL` 没连通时，lock / control / eventlog 三个包会"正确地什么都不做"
   （单实例下那是对的行为），不报错也没有日志。表现是停止按钮约一半概率失效、
   断线续传全部返 410、生成中再发消息变成 409 拒绝。
   验法：连续点几次停止，确认每次回答都真的停下。

3. **滚动更新时正在生成的回答不丢。**
   `terminationGracePeriodSeconds`(200) 必须大于 `LEA_AGENT_DRAIN_TIMEOUT`(3m)。
   小了的话排空没完就 SIGKILL，回答消失**且不落库**，而 k8s 事件里是一次正常更新。
   验法：让一轮长回答跑着，`kubectl rollout restart deploy/lea-agent`，确认那轮
   跑完并且刷新后能在历史里看到。

4. **沙箱的文件跨轮次还在。**
   lea-tools 多副本时，沙箱认领要靠 Cube metadata 里的 `lea.session_id`。
   验法：让模型写个文件，再让它读回来——两次调用大概率落在不同副本上。
   日志里应该出现 `sandbox_session_adopted`。

## 探针为什么读写不一致

`readinessProbe` 用 `/health`，`livenessProbe` 用 TCP，**故意不一样**：

- 排空期间 `/health` 会故意返 503，让 k8s 把 pod 从 endpoints 摘掉，新请求不再进来，
  已在跑的 turn 继续跑完。readiness 用它正是要利用这一点。
- liveness 绝不能也用 `/health`：那样排空一开始就被判定为容器挂了，kubelet 会重启
  容器——精心实现的优雅退出反而变成强杀，比不做还糟。

## 本地验证台

`infra/docker-compose.verify.yml` 起两个 lea-agent 副本 + 轮询的 nginx，用来在本地
跑上面第 2、3 条。它不是生产编排，nginx 的 proxy 参数跟 Ingress 注解是同一套语义，
可以当参照。
