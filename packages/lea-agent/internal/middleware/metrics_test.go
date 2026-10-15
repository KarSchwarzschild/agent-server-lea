package middleware

import (
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/prometheus/client_golang/prometheus/testutil"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/metrics"
)

func TestMetricsUsesRouteTemplateForPathParameters(t *testing.T) {
	gin.SetMode(gin.TestMode)
	metrics.HTTPRequestsTotal.Reset()

	router := gin.New()
	router.Use(Metrics())
	router.GET("/api/sessions/:id", func(c *gin.Context) {
		c.Status(http.StatusOK)
	})

	for i := 0; i < 50; i++ {
		req := httptest.NewRequest(http.MethodGet, "/api/sessions/session-"+strconv.Itoa(i), nil)
		router.ServeHTTP(httptest.NewRecorder(), req)
	}

	if got := testutil.CollectAndCount(metrics.HTTPRequestsTotal); got != 1 {
		t.Fatalf("50 个不同 session id 产生了 %d 条时间序列，应该只有 1 条", got)
	}
	if got := testutil.ToFloat64(metrics.HTTPRequestsTotal.WithLabelValues("/api/sessions/:id", http.MethodGet, "2xx")); got != 50 {
		t.Fatalf("路由模板对应的请求数=%v，应该是 50", got)
	}
}

func TestMetricsCollapsesUnmatchedPaths(t *testing.T) {
	gin.SetMode(gin.TestMode)
	metrics.HTTPRequestsTotal.Reset()

	router := gin.New()
	router.Use(Metrics())

	for _, path := range []string{"/api/unknown-1", "/api/unknown-2", "/.env", "/wp-login.php"} {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		router.ServeHTTP(httptest.NewRecorder(), req)
	}

	if got := testutil.CollectAndCount(metrics.HTTPRequestsTotal); got != 1 {
		t.Fatalf("4 个未匹配路径产生了 %d 条时间序列，应该合并成 1 条", got)
	}
	if got := testutil.ToFloat64(metrics.HTTPRequestsTotal.WithLabelValues("unmatched", http.MethodGet, "4xx")); got != 4 {
		t.Fatalf("unmatched 对应的请求数=%v，应该是 4", got)
	}
}
