package metrics

// Temporal SDK 的 Counter / Gauge / Timer 三个接口的实现，各自一个方法。

import (
	"time"

	"github.com/prometheus/client_golang/prometheus"
)

type temporalCounter struct {
	vec    *prometheus.CounterVec
	values []string
}

func (c *temporalCounter) Inc(delta int64) {
	c.vec.WithLabelValues(c.values...).Add(float64(delta))
}

type temporalGauge struct {
	vec    *prometheus.GaugeVec
	values []string
}

func (g *temporalGauge) Update(value float64) {
	g.vec.WithLabelValues(g.values...).Set(value)
}

type temporalTimer struct {
	vec    *prometheus.HistogramVec
	values []string
}

// Record 以**秒**为单位记录。
//
// Prometheus 的约定是基础单位（秒），指标名也该以 _seconds 结尾。Temporal 的
// 指标名是 temporal_*_latency 不带单位后缀，这里不改名（改了跟官方文档和社区
// 的查询语句对不上），但值统一用秒 —— 跟 lea_* 那批指标一致，
// histogram_quantile 的结果可以直接按秒读。
func (t *temporalTimer) Record(d time.Duration) {
	t.vec.WithLabelValues(t.values...).Observe(d.Seconds())
}
