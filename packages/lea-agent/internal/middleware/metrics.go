package middleware

import (
	"time"

	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/metrics"
)

// Metrics 记录 HTTP 请求指标
func Metrics() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()

		// 执行请求
		c.Next()

		// 记录指标
		duration := time.Since(start)

		// endpoint 用 gin 的路由模板（/api/sessions/:id），**不能回落到真实路径**。
		//
		// c.FullPath() 在没匹配到路由时返回空串（404）。而 404 的路径完全由调用方
		// 决定，回落到 c.Request.URL.Path 的话，外面打一轮 /api/<随机> 就能让
		// 时间序列无限增长 —— 这是可以远程触发的 Prometheus 内存耗尽，不只是
		// "指标不好看"。所以统一记成固定值。
		//
		// lea-config / lea-rag 的 metrics.py 和 lea-gateway 的 metrics.go 里是
		// 同一套处理，三处的理由一致。
		endpoint := c.FullPath()
		if endpoint == "" {
			endpoint = "unmatched"
		}
		method := c.Request.Method
		statusCode := c.Writer.Status()

		metrics.RecordHTTPRequest(endpoint, method, statusCode, duration)
	}
}
