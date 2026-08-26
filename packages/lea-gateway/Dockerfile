FROM golang:1.26-alpine AS build

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
ENV LEA_GATEWAY_PORT=8080 TZ=Asia/Shanghai
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/health || exit 1
ENTRYPOINT ["/usr/local/bin/lea-gateway"]
