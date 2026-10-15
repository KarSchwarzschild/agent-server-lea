package metrics

// Prometheus 向量的缓存 —— 见 temporal.go 顶部关于 WithTags 注册风暴那段。

import (
	"errors"
	"log"
	"strings"
	"sync"

	"github.com/prometheus/client_golang/prometheus"
)

// vectorStore 按 (指标名 + 标签键集合) 缓存向量。
//
// 为什么要这层：Prometheus 的向量在创建时就把标签**名**定死了，而 Temporal 传进
// 来的是任意 tag map。同一个指标名在不同调用点可能带不同的标签键集合（比如
// poll_no_task 有时只带 poller_type，有时还带 task_queue），所以 key 必须把标签
// 键算进去，否则第二次创建会因为标签数不符而 panic。
//
// key 里**不含标签值**，所以这个 map 的大小只取决于代码里的调用点数量（几十个），
// 不随 workflow / activity 实例数增长。
type vectorStore struct {
	mu         sync.RWMutex
	counters   map[string]*prometheus.CounterVec
	gauges     map[string]*prometheus.GaugeVec
	histograms map[string]*prometheus.HistogramVec
}

func cacheKey(name string, labelKeys []string) string {
	return name + "\x00" + strings.Join(labelKeys, "\x1f")
}

func (s *vectorStore) counter(name string, labelKeys []string) *prometheus.CounterVec {
	key := cacheKey(name, labelKeys)
	s.mu.RLock()
	if v, ok := s.counters[key]; ok {
		s.mu.RUnlock()
		return v
	}
	s.mu.RUnlock()

	s.mu.Lock()
	defer s.mu.Unlock()
	// 双检：拿写锁期间可能已经有别的 goroutine 建好了。
	if v, ok := s.counters[key]; ok {
		return v
	}
	v := prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: name, Help: "Temporal SDK counter " + name},
		labelKeys,
	)
	s.counters[key] = register(v).(*prometheus.CounterVec)
	return s.counters[key]
}

func (s *vectorStore) gauge(name string, labelKeys []string) *prometheus.GaugeVec {
	key := cacheKey(name, labelKeys)
	s.mu.RLock()
	if v, ok := s.gauges[key]; ok {
		s.mu.RUnlock()
		return v
	}
	s.mu.RUnlock()

	s.mu.Lock()
	defer s.mu.Unlock()
	if v, ok := s.gauges[key]; ok {
		return v
	}
	v := prometheus.NewGaugeVec(
		prometheus.GaugeOpts{Name: name, Help: "Temporal SDK gauge " + name},
		labelKeys,
	)
	s.gauges[key] = register(v).(*prometheus.GaugeVec)
	return s.gauges[key]
}

func (s *vectorStore) histogram(name string, labelKeys []string) *prometheus.HistogramVec {
	key := cacheKey(name, labelKeys)
	s.mu.RLock()
	if v, ok := s.histograms[key]; ok {
		s.mu.RUnlock()
		return v
	}
	s.mu.RUnlock()

	s.mu.Lock()
	defer s.mu.Unlock()
	if v, ok := s.histograms[key]; ok {
		return v
	}
	v := prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Name: name,
			Help: "Temporal SDK timer " + name,
			// 这些是**排队和执行延迟**，不是 HTTP 请求延迟，所以桶要铺到分钟级。
			// schedule_to_start 正常在毫秒级，但 worker 不够用时会涨到几十秒；
			// activity 执行时间这边动辄几十秒（LLM 调用）。按 HTTP 那套
			// （上限 10s）会让所有慢的情况全挤在 +Inf 里，分位数就没意义了。
			Buckets: []float64{
				.005, .01, .05, .1, .25, .5, 1, 2.5, 5, 10, 30, 60, 120, 300,
			},
		},
		labelKeys,
	)
	s.histograms[key] = register(v).(*prometheus.HistogramVec)
	return s.histograms[key]
}

// register 注册到默认 registry；已经注册过就复用已有的那个。
//
// 为什么要容错而不是直接 MustRegister：这个 handler 和 server 共用同一个二进制
// （cmd/server 和 cmd/worker），测试里也会反复建 handler。撞名就 panic 的话，
// 一个指标名冲突会让整个 worker 起不来 —— 而指标不该有这种杀伤力。
func register(c prometheus.Collector) prometheus.Collector {
	if err := prometheus.Register(c); err != nil {
		var already prometheus.AlreadyRegisteredError
		if errors.As(err, &already) {
			return already.ExistingCollector
		}
		// 走到这里说明注册被拒但又不是"已注册过"。最常见的是**同名但 Help 或标签
		// 不一致** —— Prometheus 只在名字和 Help 都一致时才返回
		// AlreadyRegisteredError，否则是另一种错。
		//
		// 这时返回的是一个**没挂到 registry 上的游离对象**：代码照常往里记数，
		// /metrics 里永远看不到它。不打日志的话这是纯静默的 —— 指标就是没了，
		// 而且查不出为什么。所以这里必须吼一声。
		//
		// 不 panic 是刻意的：指标注册冲突不该让 worker 起不来。
		log.Printf("metrics_register_failed name=%s err=%v "+
			"(指标不会出现在 /metrics 里，业务不受影响)", describe(c), err)
		return c
	}
	return c
}

// describe 取 collector 的指标名，只用于日志。
func describe(c prometheus.Collector) string {
	ch := make(chan *prometheus.Desc, 1)
	go func() {
		c.Describe(ch)
		close(ch)
	}()
	if d, ok := <-ch; ok {
		return d.String()
	}
	return "unknown"
}
