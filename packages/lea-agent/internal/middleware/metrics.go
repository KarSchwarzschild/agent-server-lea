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
		endpoint := c.FullPath()
		if endpoint == "" {
			endpoint = c.Request.URL.Path
		}
		method := c.Request.Method
		statusCode := c.Writer.Status()

		metrics.RecordHTTPRequest(endpoint, method, statusCode, duration)
	}
}
