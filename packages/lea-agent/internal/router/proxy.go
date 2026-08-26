package router

import (
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"

	"github.com/gin-gonic/gin"

	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/runtime"
)

// UserConfigProxy 把 /api/user/{agents,skills,tools}/* 反代到 lea-config 对应路径。
//
// 从 gin context 里拿 JWT 已验过的身份 → 注入 X-User-ID / X-Tenant-ID /
// X-User-Role，**覆盖或删掉**客户端可能伪造的同名头。
//
// 这三个头是下游整套授权的唯一身份来源（lea_authkit 的 _TrustedHeaderActor），
// 所以"覆盖"这件事本身就是安全边界：
//
//	X-User-ID / X-Tenant-ID   决定能看到谁的数据
//	X-User-Role               决定 require_admin() 那道门开不开
//
// 三个都必须走 Set-或-Del，不能有"不管"的分支 —— 不管等于把客户端的值原样转给
// 下游。X-User-Role 一度就是不管的，于是浏览器加一个 X-User-Role: admin 就是管理员。
//
// lea-config 的路由前缀是 /agents（不是 /api/agents），所以 rewrite 时把
// "/api/user" 前缀切掉。
//
// LEA_CONFIG_URL 环境变量控制 upstream，默认 http://localhost:8500。
//
// lea-config 的路由前缀是 /agents（不是 /api/agents），所以 rewrite 时把
// "/api/user" 前缀切掉。
//
// LEA_CONFIG_URL 环境变量控制 upstream，默认 http://localhost:8500。
func UserConfigProxy() gin.HandlerFunc {
	target := os.Getenv("LEA_CONFIG_URL")
	if target == "" {
		target = "http://localhost:8500"
	}
	upstream, err := url.Parse(target)
	if err != nil {
		panic("bad LEA_CONFIG_URL: " + err.Error())
	}
	proxy := httputil.NewSingleHostReverseProxy(upstream)

	// 服务间鉴权的头。runtime/internal_token.go 那个 Transport 覆盖不到这里 ——
	// 它换的是 runtime 包里五个 http.Client 的 Transport，而反代走的是
	// httputil.ReverseProxy 自己的。lea-config 开了鉴权之后不带这个头就是 401，
	// 表现为前端的 Agent 列表整个空掉。
	internalToken := os.Getenv("LEA_INTERNAL_TOKEN")

	origDirector := proxy.Director
	proxy.Director = func(req *http.Request) {
		origDirector(req)
		// /api/user/agents/... → /agents/...
		req.URL.Path = strings.TrimPrefix(req.URL.Path, "/api/user")
		req.Host = upstream.Host
		if internalToken != "" {
			// 设在 Director 里而不是下面的 handler 里：ReverseProxy 会先 Clone 一份
			// 出站请求再调 Director，所以改的是副本。在 handler 里改的是**进来的那个
			// Request**，等于把集群密钥写回客户端请求对象上 —— 功能上也能work，但
			// 之后任何读 c.Request.Header 的中间件/日志都会看到它。
			req.Header.Set(runtime.InternalTokenHeader, internalToken)
		} else {
			// 没配密钥 = 这套机制没启用。但客户端伪造的同名头必须删掉，
			// 不然它会被原样转给 lea-config —— 那边一旦开了鉴权就成了绕过口子。
			req.Header.Del(runtime.InternalTokenHeader)
		}
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
		// X-User-Role 跟上面两个同等重要：下游的 require_admin() 判的就是它
		// （lea_authkit/authz.py）。不重写的话那道门判的是一个客户端自己填的值 ——
		// 浏览器加一个 X-User-Role: admin 就成了管理员。
		setUserRoleHeader(c)
		proxy.ServeHTTP(c.Writer, c.Request)
	}
}

// proxiedRoles 是**允许下发给下游的角色白名单**。来源是 lea-agent 已验签 JWT，
// 不能直接信任客户端传来的 X-User-Role。
//
// 白名单的好处是新增角色必须有人显式加进来，而那正是该想清楚它意味着什么的时刻。
// 下游 lea_authkit 那边也**独立**拒绝 platform_admin 头 —— 两侧都挡，任一侧
// 被误改都不会立刻破防。
var proxiedRoles = map[string]bool{
    "admin":          true,
    "platform_admin": true,
}

// setUserRoleHeader 按 JWT 已验过的 roles 设置 X-User-Role，没有可下发的就删掉。
//
// 删而不是留空值：留一个空头下游会当成"有这个头但值是空"，多一种要处理的状态；
// 而删掉之后"客户端伪造的头"和"我们没下发"是同一件事 —— 都不存在。
func setUserRoleHeader(c *gin.Context) {
	const header = "X-User-Role"

	// c.Get 而不是 c.GetStringSlice：后者在类型不匹配时静默返回 nil，
	// 那会让"中间件没跑"和"这个用户没有角色"看起来一样。
	raw, ok := c.Get("roles")
	if !ok {
		c.Request.Header.Del(header)
		return
	}
	roles, ok := raw.([]string)
	if !ok {
		c.Request.Header.Del(header)
		return
	}
	for _, allowed := range []string{"platform_admin", "admin"} {
		for _, r := range roles {
			if strings.ToLower(strings.TrimSpace(r)) == allowed && proxiedRoles[allowed] {
				c.Request.Header.Set(header, allowed)
				return
			}
		}
	}
	// 有 roles 但没有一个在白名单里 —— 普通用户，明确删掉伪造的头。
	c.Request.Header.Del(header)
}

func UserAgentsProxy() gin.HandlerFunc {
	return UserConfigProxy()
}

func UserSkillsProxy() gin.HandlerFunc {
	return UserConfigProxy()
}

func UserToolsProxy() gin.HandlerFunc {
	return UserConfigProxy()
}
