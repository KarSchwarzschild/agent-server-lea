package main

// 网关的 Prometheus 指标。
//
// ── 为什么单独开一个端口，而不是在主端口上放行 /metrics ────────────────────
//
// 网关是**公网入口**（compose 里 8840:8080 发布到宿主，那台机器有公网 IP）。
// 其他服务（lea-config / lea-rag）的 /metrics 是在内网鉴权里加白名单放行的，
// 因为它们只在 docker 网络里，外面碰不到。网关不一样：在 8080 上放行 /metrics
// 等于把请求量、各端点错误率、全部端点名、鉴权拒绝次数直接公开到互联网 ——
// 这些是侦察一个系统最想要的东西（哪个接口在用、什么时候流量低、鉴权在不在拒）。
//
// 所以指标走第二个 listener，端口不在 compose 里 publish，Prometheus 靠 compose
// 网络内的服务名访问 gateway:9108。公网打不到，也不需要给 Prometheus 配凭据
// （Prometheus 的 scrape_configs 不支持自定义请求头，只有 Basic/Bearer/mTLS）。
//
// 9108 是避开常用 exporter 端口挑的：9090 Prometheus 自己、9091 pushgateway、
// 9093 alertmanager、9100 node、9104 mysqld、9121 redis。
//
// ── endpoint 标签为什么必须是路由模板 ──────────────────────────────────────
//
// 用 r.URL.Path 当标签会炸 Prometheus：/api/sessions/<uuid> 每个会话一条时间序列，
// 而且 404 的路径是调用方随便写的 —— 外面扫一轮就能把内存顶上去，是可以远程触发
// 的。这里取 mux 匹配到的 pattern，没匹配上的统一记成 "unmatched"，**绝不回落到
// 真实路径**。lea-config / lea-rag 的 metrics.py 里有同样一段，原因一致。

import (
	"bufio"
	"log"
	"net"
	"net/http"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	httpRequestsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "lea_gateway_http_requests_total",
			Help: "Total number of HTTP requests handled by the gateway",
		},
		[]string{"endpoint", "method", "status"},
	)

	httpRequestDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "lea_gateway_http_request_duration_seconds",
			Help:    "Gateway HTTP request duration in seconds",
			Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10},
		},
		[]string{"endpoint", "method"},
	)

	// 鉴权拒绝。网关是唯一能看到这个的地方 —— 下游服务收到的请求都已经过了鉴权。
	// reason 刻意只有固定几种取值，不要把 jwt 库的 error 文本当标签（那是无界的，
	// 等于另一个基数炸弹）。
	authRejectionsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "lea_gateway_auth_rejections_total",
			Help: "Total number of requests rejected by gateway authentication",
		},
		[]string{"reason"}, // missing_token, invalid_token
	)

	// 租户归属解析失败 —— 403 的主要来源，跟 token 无效是两回事，分开看才能判断
	// 是"登录坏了"还是"租户选择坏了"。
	tenantResolveFailuresTotal = promauto.NewCounter(
		prometheus.CounterOpts{
			Name: "lea_gateway_tenant_resolve_failures_total",
			Help: "Total number of failures resolving the caller's tenant membership",
		},
	)
)

// statusWriter 记下状态码，同时**保留 ResponseWriter 的可选能力**。
//
// ⚠️ Flush 必须转发：/api/chat/ 是 SSE 长流，ReverseProxy 靠类型断言拿 Flusher
// 来逐块推送。包一层之后如果断言失败，流式回复会退化成全部缓冲到结束才吐 ——
// 表现是前端"卡住很久然后一次性出现整段回答"，而不是报错，很难往中间件上想。
// Hijack 同理转发，留给协议升级用（现在没有 websocket，但包坏了是隐性地雷）。
type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(code int) {
	if w.status != 0 {
		return
	}
	w.status = code
	w.ResponseWriter.WriteHeader(code)
}

func (w *statusWriter) Write(b []byte) (int, error) {
	if w.status == 0 {
		w.status = http.StatusOK // 没显式 WriteHeader 就直接 Write，等于 200
	}
	return w.ResponseWriter.Write(b)
}

func (w *statusWriter) Flush() {
	// Flush 会让 net/http 立即提交响应头；未显式写状态时，实际状态就是 200。
	// 先同步包装器状态，避免之后的 WriteHeader 把指标改成一个客户端从未收到的状态。
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	if f, ok := w.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// Unwrap 让 http.ResponseController 仍能发现底层 ResponseWriter 的可选能力。
func (w *statusWriter) Unwrap() http.ResponseWriter {
	return w.ResponseWriter
}

func (w *statusWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	if h, ok := w.ResponseWriter.(http.Hijacker); ok {
		return h.Hijack()
	}
	return nil, nil, http.ErrNotSupported
}

// observe 统计请求。放在 auth **外面**，这样 401/403 也会被记下来 —— 鉴权挂了
// 是最该看见的故障之一，放里面就正好漏掉它。
//
// mux 传进来只为查匹配到哪条 pattern（mux.Handler 只做路由解析，不执行 handler）。
// 不能靠 r.Pattern：那是 ServeMux 在分发时写到请求副本上的，外层中间件看不到。
func (g *gateway) observe(mux *http.ServeMux, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, pattern := mux.Handler(r)
		if pattern == "" {
			pattern = "unmatched"
		}

		sw := &statusWriter{ResponseWriter: w}
		start := time.Now()
		next.ServeHTTP(sw, r)

		if sw.status == 0 {
			sw.status = http.StatusOK
		}
		httpRequestsTotal.WithLabelValues(pattern, r.Method, statusLabel(sw.status)).Inc()
		httpRequestDuration.WithLabelValues(pattern, r.Method).Observe(time.Since(start).Seconds())
	})
}

// statusLabel 把状态码归到 2xx/3xx/4xx/5xx，跟 lea-agent 的 statusCodeToLabel
// 保持一致 —— 两个服务的指标要能用同一套告警表达式查。
func statusLabel(code int) string {
	switch {
	case code >= 200 && code < 300:
		return "2xx"
	case code >= 300 && code < 400:
		return "3xx"
	case code >= 400 && code < 500:
		return "4xx"
	case code >= 500:
		return "5xx"
	default:
		return "unknown"
	}
}

// startMetricsServer 在独立端口上起 /metrics，见文件头关于端口选择的说明。
//
// 用独立的 ServeMux，不复用业务那张路由表 —— 业务表是白名单且过 auth，而这个
// listener 上只应该有 /metrics 一条，不该因为业务加路由就跟着多出东西。
func startMetricsServer() {
	addr := ":" + env("LEA_GATEWAY_METRICS_PORT", "9108")
	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.Handler())

	server := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}
	go func() {
		log.Printf("lea-gateway metrics listening on %s", addr)
		// 指标端口起不来不该拖垮网关本身：业务照跑，只是没指标。
		// 用 Printf 而不是 Fatal，正是为了这个。
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Printf("lea-gateway metrics server stopped: %v", err)
		}
	}()
}
