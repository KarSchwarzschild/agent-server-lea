# syntax=docker/dockerfile:1.7
#
# lea-agent —— Go 引擎，主聊天链路 :8100。唯一直接面向浏览器的服务。
#
# ── 构建 ──────────────────────────────────────────────────────────────────
#
#     docker build \
#         --build-context agentloop=../agentloop \
#         -f packages/lea-agent/Dockerfile \
#         -t lea-agent:dev .
#
# 注意那个 --build-context。go.mod 里有：
#
#     replace gitlab.ouc-online.com.cn/aibase/agentloop => ../../../agentloop
#
# agentloop 是**本仓库之外**的兄弟仓库，而 Docker 的构建上下文进不去上下文根之外的
# 路径。所以引擎库必须显式作为第二个上下文传进来。
#
# 为什么不直接把构建上下文设成两个仓库的父目录：那个目录下还有别的仓库
# （transformers、MinerU 这类），上下文会变成好几个 GB —— 每次构建都要把它们打包
# 发给 daemon。具名上下文只带 agentloop 一个。
#
# 代价：CI 必须同时 checkout 两个仓库，并且带上这个 flag。少了它的报错是
# "replacement directory ../../../agentloop does not exist"，指向明确，不会静默出错。
#
# 长期更好的做法是给 agentloop 打 tag 发成正式 Go module、删掉 replace。现在没做是
# 因为两个仓库在同步改，每次改都要发版是实打实的摩擦。等 agentloop 稳定了值得换。

# ── 阶段 1：编译 ──────────────────────────────────────────────────────────
FROM golang:1.26-alpine AS build

# 私有 GitLab 走不了公网校验和数据库；GOPROXY 用国内镜像（跟本地开发一致）。
ENV GOFLAGS=-mod=mod \
    GOPROXY=https://goproxy.cn,direct \
    GONOSUMDB=gitlab.ouc-online.com.cn \
    GONOSUMCHECK=1 \
    GOPRIVATE=gitlab.ouc-online.com.cn \
    CGO_ENABLED=0

# 镜像内的目录结构必须跟磁盘上一致，`../../../agentloop` 这个相对路径才算得对：
#     /src/lea-platform/packages/lea-agent  →  ../../../agentloop  =  /src/agentloop
# 摆错位置的话 go 会说找不到 replacement directory，所以这不是能悄悄错的东西。
COPY --from=agentloop . /src/agentloop

WORKDIR /src/lea-platform/packages/lea-agent

# 先只拷 go.mod / go.sum 拉依赖 —— 改业务代码时这一层能命中缓存。
COPY packages/lea-agent/go.mod packages/lea-agent/go.sum ./
RUN go mod download

COPY packages/lea-agent/ ./

# -trimpath 去掉构建机的绝对路径（否则栈里会有 /src/... 和构建者的目录结构）。
# 静态链接，运行镜像里不需要 libc。
RUN go build -trimpath -ldflags="-s -w" -o /out/lea-agent ./cmd/server
RUN go build -trimpath -ldflags="-s -w" -o /out/lea-agent-worker ./cmd/worker

# ── 阶段 2：运行镜像 ──────────────────────────────────────────────────────
FROM alpine:3.20

# ca-certificates：要调 HTTPS（LLM 上游、对象存储）。少了它的报错是
#   x509: certificate signed by unknown authority
# 看起来像证书配错了，其实是根证书压根不存在。
#
# tzdata：接口返回的时间是 RFC3339 **带本地偏移**的（见 docs/FRONTEND_API.md），
# 不是 UTC。没有 tzdata 时 Go 找不到时区库，一律退化成 UTC —— 前端拿到的偏移量
# 会从 +08:00 变成 Z。这是个静默的接口行为变化，所以宁可多这 2MB。
RUN apk add --no-cache ca-certificates tzdata \
    && adduser -S -u 10001 lea

COPY --from=build /out/lea-agent /usr/local/bin/lea-agent
COPY --from=build /out/lea-agent-worker /usr/local/bin/lea-agent-worker

USER 10001

ENV LEA_AGENT_PORT=8100 \
    TZ=Asia/Shanghai

EXPOSE 8100

# 给 compose 用；k8s 走 readinessProbe 会忽略这条。
# wget 是 alpine 自带的（busybox），不用额外装。
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8100/health || exit 1

# 必须是 exec 形式：优雅退出靠进程收到 SIGTERM 才能触发（排空正在跑的 turn，
# 见 cmd/server/main.go 的 drain）。写成 shell 形式的话 PID 1 是 sh，它不转发信号，
# 容器被杀时正在生成的回答会直接消失 —— 而且 k8s 那边看起来是"正常滚动更新"。
ENTRYPOINT ["/usr/local/bin/lea-agent"]
