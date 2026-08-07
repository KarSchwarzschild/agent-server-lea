package router

import (
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"

	"github.com/gin-gonic/gin"
)

// UserAgentsProxy 把 /api/user/agents/* 反代到 lea-config 的 /agents/*。
// 从 gin context 里拿 JWT 已验过的 user_id / tenant_id → 注入 X-User-ID /
// X-Tenant-ID header，覆盖前端可能伪造的同名 header（防越权）。
//
// lea-config 的路由前缀是 /agents（不是 /api/agents），所以 rewrite 时把
// "/api/user" 前缀切掉。
//
// LEA_CONFIG_URL 环境变量控制 upstream，默认 http://localhost:8500。
func UserAgentsProxy() gin.HandlerFunc {
	target := os.Getenv("LEA_CONFIG_URL")
	if target == "" {
		target = "http://localhost:8500"
	}
	upstream, err := url.Parse(target)
	if err != nil {
		panic("bad LEA_CONFIG_URL: " + err.Error())
	}
	proxy := httputil.NewSingleHostReverseProxy(upstream)

	origDirector := proxy.Director
	proxy.Director = func(req *http.Request) {
		origDirector(req)
		// /api/user/agents/... → /agents/...
		req.URL.Path = strings.TrimPrefix(req.URL.Path, "/api/user")
		req.Host = upstream.Host
	}

	return func(c *gin.Context) {
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")
		if tenantID != "" {
			c.Request.Header.Set("X-Tenant-ID", tenantID)
		} else {
			c.Request.Header.Del("X-Tenant-ID")
		}
		if userID != "" {
			c.Request.Header.Set("X-User-ID", userID)
		} else {
			c.Request.Header.Del("X-User-ID")
		}
		proxy.ServeHTTP(c.Writer, c.Request)
	}
}
