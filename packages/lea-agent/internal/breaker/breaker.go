package breaker

import (
	"log"
	"time"

	"github.com/sony/gobreaker"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/metrics"
)

// Config 熔断器配置
type Config struct {
	// Name 熔断器名称
	Name string
	// MaxRequests 半开状态允许的最大请求数
	MaxRequests uint32
	// Interval 统计窗口时长
	Interval time.Duration
	// Timeout 熔断持续时间（打开后多久进入半开状态）
	Timeout time.Duration
	// FailureThreshold 失败次数阈值（连续失败多少次触发熔断）
	FailureThreshold uint32
}

// DefaultConfig 默认配置：10 秒内连续失败 5 次 → 熔断 30 秒
var DefaultConfig = Config{
	Name:             "default",
	MaxRequests:      3,
	Interval:         10 * time.Second,
	Timeout:          30 * time.Second,
	FailureThreshold: 5,
}

// New 创建熔断器
func New(cfg Config) *gobreaker.CircuitBreaker {
	if cfg.Name == "" {
		cfg = DefaultConfig
	}

	settings := gobreaker.Settings{
		Name:        cfg.Name,
		MaxRequests: cfg.MaxRequests,
		Interval:    cfg.Interval,
		Timeout:     cfg.Timeout,
		ReadyToTrip: func(counts gobreaker.Counts) bool {
			// 连续失败达到阈值时触发熔断
			return counts.ConsecutiveFailures >= cfg.FailureThreshold
		},
		OnStateChange: func(name string, from gobreaker.State, to gobreaker.State) {
			log.Printf("[breaker] %s: %s → %s", name, from, to)

			// 记录熔断器状态到 Prometheus
			var state int
			switch to {
			case gobreaker.StateClosed:
				state = 0
			case gobreaker.StateOpen:
				state = 1
				// 熔断器打开时记录触发次数
				metrics.RecordCircuitBreakerTrip(name)
			case gobreaker.StateHalfOpen:
				state = 2
			}
			metrics.SetCircuitBreakerState(name, state)
		},
	}

	return gobreaker.NewCircuitBreaker(settings)
}

// NewKBBreaker KB 检索熔断器（较宽容：向量库慢但不应轻易熔断）
func NewKBBreaker() *gobreaker.CircuitBreaker {
	return New(Config{
		Name:             "kb-search",
		MaxRequests:      3,
		Interval:         10 * time.Second,
		Timeout:          30 * time.Second,
		FailureThreshold: 8, // 更高的阈值
	})
}

// NewMemoryBreaker 记忆检索熔断器
func NewMemoryBreaker() *gobreaker.CircuitBreaker {
	return New(Config{
		Name:             "memory-search",
		MaxRequests:      3,
		Interval:         10 * time.Second,
		Timeout:          30 * time.Second,
		FailureThreshold: 5,
	})
}

// NewToolGatewayBreaker 动态工具网关熔断器
func NewToolGatewayBreaker() *gobreaker.CircuitBreaker {
	return New(Config{
		Name:             "tool-gateway",
		MaxRequests:      5,
		Interval:         10 * time.Second,
		Timeout:          20 * time.Second,
		FailureThreshold: 5,
	})
}
