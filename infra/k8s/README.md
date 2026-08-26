# CCE 部署清单

面向 CCE（华为云容器引擎，就是 k8s）。中间件用外部实例——现在是自建的
（202.205.160.9 上的 MySQL / Redis），将来换成云服务商托管的只改 ConfigMap 的值，
manifest 和镜像都不动。**所以这里没有 Redis / MySQL 的 Deployment**，那是有意的。

## 服务清单

| 服务 | 端口 | 对外 | 能扩容 | 备注 |
|---|---|---|---|---|
| lea-gateway | 8080 | **是**（Ingress） | 是 | JWT 验签和客户端路由 |
| lea-agent | 8100 | 否 | 是 | 聊天和运行时 |
| lea-config | 8500 | 否 | 是 | 无状态 |
| lea-rag | 8300 | 否 | 是 | 启动慢，探针延迟给大 |
| lea-tools | 8400 | 否 | 是（新） | 要 root + 共享存储 |

lea-server 不在列——它已经退出调用链（admin API 迁走了）。旧代码已归档到
`packages/lea-server-legacy`，不要再作为运行服务接入。

## 客户端入口

浏览器只访问 `lea-gateway:8080` 对应的 Ingress。它用外部 HS256 JWT 验签，随后：

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
# 1. 先建密钥（值不在仓库里，见 config.yaml 顶部的注释和命令）
kubectl create secret generic lea-secrets --from-literal=...

# 2. 共享存储的 PVC（lea-tools 要）—— 按 CCE 的存储类型自己建，名字叫 lea-shared
# 3. 配置 + 各服务
kubectl apply -f infra/k8s/config.yaml
kubectl apply -f infra/k8s/lea-gateway.yaml
kubectl apply -f infra/k8s/lea-config.yaml
kubectl apply -f infra/k8s/lea-rag.yaml
kubectl apply -f infra/k8s/lea-tools.yaml
kubectl apply -f infra/k8s/lea-agent.yaml
kubectl apply -f infra/k8s/networkpolicy.yaml
```

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
