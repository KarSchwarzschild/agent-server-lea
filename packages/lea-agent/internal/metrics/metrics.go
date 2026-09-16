// Package metrics 提供 Prometheus 监控指标
package metrics

import (
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	// HTTP 请求总数
	HTTPRequestsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "lea_agent_http_requests_total",
			Help: "Total number of HTTP requests",
		},
		[]string{"endpoint", "method", "status"},
	)

	// HTTP 请求延迟
	HTTPRequestDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "lea_agent_http_request_duration_seconds",
			Help:    "HTTP request duration in seconds",
			Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10},
		},
		[]string{"endpoint", "method"},
	)

	// LLM 调用总数
	LLMCallsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "lea_agent_llm_calls_total",
			Help: "Total number of LLM API calls",
		},
		[]string{"model", "status"}, // status: success, error, timeout
	)

	// LLM 调用延迟
	LLMCallDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "lea_agent_llm_call_duration_seconds",
			Help:    "LLM API call duration in seconds",
			Buckets: []float64{.1, .5, 1, 2, 5, 10, 30, 60},
		},
		[]string{"model"},
	)

	// LLM Token 使用量
	LLMTokensUsed = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "lea_agent_llm_tokens_total",
			Help: "Total number of tokens used",
		},
		[]string{"model", "type"}, // type: input, output
	)

	// 工具调用总数
	ToolCallsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "lea_agent_tool_calls_total",
			Help: "Total number of tool calls",
		},
		[]string{"tool_name", "status"}, // status: success, error
	)

	// 工具调用延迟
	ToolCallDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "lea_agent_tool_call_duration_seconds",
			Help:    "Tool call duration in seconds",
			Buckets: []float64{.01, .05, .1, .5, 1, 2, 5, 10},
		},
		[]string{"tool_name"},
	)

	// 熔断器状态（0=closed, 1=open, 2=half-open）
	CircuitBreakerState = promauto.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "lea_agent_circuit_breaker_state",
			Help: "Circuit breaker state (0=closed, 1=open, 2=half-open)",
		},
		[]string{"breaker_name"},
	)

	// 熔断器触发次数
	CircuitBreakerTrips = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "lea_agent_circuit_breaker_trips_total",
			Help: "Total number of circuit breaker trips",
		},
		[]string{"breaker_name"},
	)

	// 活跃会话数
	ActiveSessions = promauto.NewGauge(
		prometheus.GaugeOpts{
			Name: "lea_agent_active_sessions",
			Help: "Number of active sessions in memory",
		},
	)

	// 会话重建次数
	SessionRebuildsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "lea_agent_session_rebuilds_total",
			Help: "Total number of session rebuilds",
		},
		[]string{"reason"}, // reason: attachment_changed, agent_changed
	)

	// 下游服务调用（KB/Memory/Tools）
	DownstreamCallsTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "lea_agent_downstream_calls_total",
			Help: "Total number of downstream service calls",
		},
		[]string{"service", "status"}, // service: kb, memory, tools; status: success, error, degraded
	)

	// 下游服务延迟
	DownstreamCallDuration = promauto.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "lea_agent_downstream_call_duration_seconds",
			Help:    "Downstream service call duration in seconds",
			Buckets: []float64{.01, .05, .1, .5, 1, 2, 5},
		},
		[]string{"service"},
	)
)

// RecordHTTPRequest 记录 HTTP 请求指标
func RecordHTTPRequest(endpoint, method string, statusCode int, duration time.Duration) {
	status := statusCodeToLabel(statusCode)
	HTTPRequestsTotal.WithLabelValues(endpoint, method, status).Inc()
	HTTPRequestDuration.WithLabelValues(endpoint, method).Observe(duration.Seconds())
}

// RecordLLMCall 记录 LLM 调用指标
func RecordLLMCall(model, status string, duration time.Duration, inputTokens, outputTokens int) {
	LLMCallsTotal.WithLabelValues(model, status).Inc()
	LLMCallDuration.WithLabelValues(model).Observe(duration.Seconds())
	if inputTokens > 0 {
		LLMTokensUsed.WithLabelValues(model, "input").Add(float64(inputTokens))
	}
	if outputTokens > 0 {
		LLMTokensUsed.WithLabelValues(model, "output").Add(float64(outputTokens))
	}
}

// RecordToolCall 记录工具调用指标
func RecordToolCall(toolName, status string, duration time.Duration) {
	ToolCallsTotal.WithLabelValues(toolName, status).Inc()
	ToolCallDuration.WithLabelValues(toolName).Observe(duration.Seconds())
}

// RecordDownstreamCall 记录下游服务调用指标
func RecordDownstreamCall(service, status string, duration time.Duration) {
	DownstreamCallsTotal.WithLabelValues(service, status).Inc()
	DownstreamCallDuration.WithLabelValues(service).Observe(duration.Seconds())
}

// SetCircuitBreakerState 设置熔断器状态
func SetCircuitBreakerState(breakerName string, state int) {
	CircuitBreakerState.WithLabelValues(breakerName).Set(float64(state))
}

// RecordCircuitBreakerTrip 记录熔断器触发
func RecordCircuitBreakerTrip(breakerName string) {
	CircuitBreakerTrips.WithLabelValues(breakerName).Inc()
}

// UpdateActiveSessions 更新活跃会话数
func UpdateActiveSessions(count int) {
	ActiveSessions.Set(float64(count))
}

// RecordSessionRebuild 记录会话重建
func RecordSessionRebuild(reason string) {
	SessionRebuildsTotal.WithLabelValues(reason).Inc()
}

func statusCodeToLabel(code int) string {
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
