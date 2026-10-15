package metrics

// Temporal SDK 的指标接到 Prometheus 上。
//
// ── 为什么自己写而不用 contrib/tally ──────────────────────────────────────
//
// 官方常规做法是 go.temporal.io/sdk/contrib/tally.NewMetricsHandler，但那要引
// tally v4 + contrib 两个新模块。而 SDK 的 Handler 接口只有 4 个方法
// （WithTags/Counter/Gauge/Timer，各自 1 个方法），client_golang 这边已经在
// go.mod 里了，所以自己接等于**零新依赖**。
//
// 这不是洁癖：给网关加指标时 `go get client_golang@v1.24.1` 顺手把鉴权用的
// golang-jwt 从 v5.2.1 顶到了 v5.3.1。依赖越少，这种"为了 A 被动升了 B"的事越少。
//
// ── 性能 ──────────────────────────────────────────────────────────────────
//
// 单次记录是一次 map 读 + 一次 Prometheus 原子加，纳秒级；而这个 worker 真正
// 在做的是 LLM 调用和工具执行（秒级）。开销差了六七个数量级。
//
// 唯一需要留意的是 WithTags：SDK 会频繁调它派生带标签的 handler，如果每次都去
// prometheus.NewCounterVec + Register，那就是注册风暴。所以这里把**向量**按
// (指标名 + 排序后的标签键) 缓存，只有标签值那一维交给 Prometheus 自己管
// （CounterVec 本来就是干这个的）。缓存的 key 不含标签值，所以 map 不会随
// workflow 数量增长。
//
// ── 基数 ──────────────────────────────────────────────────────────────────
//
// 已核对 SDK v1.49.0 的标签键全集（internal/common/metrics/constants.go）：
// namespace / client_name / poller_type / worker_type / workflow_type /
// activity_type / nexus_* / failure_reason / task_queue / operation / cause /
// status_code。**没有 workflow_id 或 run_id**，所以不存在"每个工作流一条序列"
// 的问题。failure_reason 是 6 个常量里取值，cause 是 gRPC 的枚举 String()，
// 也都是有界的。
//
// 本项目的实际取值很窄：1 个 workflow 类型、2 个 activity 类型、1 个 task queue。

import (
	"sort"

	"github.com/prometheus/client_golang/prometheus"
	"go.temporal.io/sdk/client"
)

// TemporalHandler 实现 client.MetricsHandler。
//
// 接口定义在 SDK 的 internal 包里，引不到，但 client 包把它和三个子接口都用
// 类型别名重新导出了（client.MetricsHandler / MetricsCounter / MetricsGauge /
// MetricsTimer），所以方法签名直接写别名就行。
type TemporalHandler struct {
	tags  map[string]string
	store *vectorStore
}

// 编译期断言。少了这一行，签名写错只会在 Dial() 那个调用点报错，错误信息是
// 一长串 "does not implement ... (wrong type for method Counter)"，离真正改坏
// 的地方有几个文件远。
var _ client.MetricsHandler = (*TemporalHandler)(nil)

// NewTemporalHandler 建一个根 handler。所有派生 handler 共用同一份向量缓存。
func NewTemporalHandler() *TemporalHandler {
	return &TemporalHandler{
		tags: map[string]string{},
		store: &vectorStore{
			counters:   map[string]*prometheus.CounterVec{},
			gauges:     map[string]*prometheus.GaugeVec{},
			histograms: map[string]*prometheus.HistogramVec{},
		},
	}
}

func (h *TemporalHandler) WithTags(tags map[string]string) client.MetricsHandler {
	merged := make(map[string]string, len(h.tags)+len(tags))
	for k, v := range h.tags {
		merged[k] = v
	}
	// 新标签覆盖同名旧标签，这是接口约定的语义。
	for k, v := range tags {
		merged[k] = v
	}
	return &TemporalHandler{tags: merged, store: h.store}
}

func (h *TemporalHandler) Counter(name string) client.MetricsCounter {
	keys, values := h.sortedTags()
	return &temporalCounter{vec: h.store.counter(name, keys), values: values}
}

func (h *TemporalHandler) Gauge(name string) client.MetricsGauge {
	keys, values := h.sortedTags()
	return &temporalGauge{vec: h.store.gauge(name, keys), values: values}
}

func (h *TemporalHandler) Timer(name string) client.MetricsTimer {
	keys, values := h.sortedTags()
	return &temporalTimer{vec: h.store.histogram(name, keys), values: values}
}

// sortedTags 返回按键排序的标签键和值。
//
// 必须排序：Prometheus 的向量按**位置**取标签值，而 Go 的 map 遍历顺序是随机的。
// 不排序的话同一个指标会时不时把 activity_type 的值填到 task_queue 的位置上，
// 查出来的数据像是"有个叫 Execute 的 task queue"，而且每次重启表现都不一样。
func (h *TemporalHandler) sortedTags() ([]string, []string) {
	keys := make([]string, 0, len(h.tags))
	for k := range h.tags {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	values := make([]string, len(keys))
	for i, k := range keys {
		values[i] = h.tags[k]
	}
	return keys, values
}
