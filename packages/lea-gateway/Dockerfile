FROM golang:1.26-alpine AS build

# GOPROXY 默认是 proxy.golang.org，国内网络连不通；换成国内镜像
# （跟 lea-agent/Dockerfile 保持一致）。
ENV GOPROXY=https://goproxy.cn,direct

WORKDIR /src
COPY packages/lea-gateway/go.mod packages/lea-gateway/go.sum ./packages/lea-gateway/
WORKDIR /src/packages/lea-gateway
RUN go mod download
COPY packages/lea-gateway/ .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/lea-gateway .

FROM alpine:3.20
RUN apk add --no-cache ca-certificates tzdata \
    && adduser -S -u 10001 lea
COPY --from=build /out/lea-gateway /usr/local/bin/lea-gateway
USER 10001
ENV LEA_GATEWAY_PORT=8080 LEA_GATEWAY_METRICS_PORT=9108 TZ=Asia/Shanghai
# 8080 业务端口（compose 里发布到宿主 8840）。
# 9108 只服务 /metrics，**不要发布到宿主** —— 网关是公网入口，指标里有全部端点名、
# 请求量和鉴权拒绝数。Prometheus 走 compose 网络访问 gateway:9108 就够了。
# （EXPOSE 在 Docker 里只是文档，真正决定是否对外的是 compose 的 ports:。）
EXPOSE 8080 9108
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/health || exit 1
ENTRYPOINT ["/usr/local/bin/lea-gateway"]
