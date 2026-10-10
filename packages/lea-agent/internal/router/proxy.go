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

// ResourceConfigProxy 把中性 /api/{agents,skills,tools,...} 和兼容的
// /api/user/{agents,skills,tools,...} 反代到 lea-config 对应路径。
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
// "/api" 或兼容路径的 "/api/user" 前缀切掉。
//
// LEA_CONFIG_URL 环境变量控制 upstream，默认 http://localhost:8500。
func ResourceConfigProxy() gin.HandlerFunc {
	target := os.Getenv("LEA_CONFIG_URL")
	if target == "" {
		target = "http://localhost:8500"
	}
	return userProxy(target, "bad LEA_CONFIG_URL: ", func(p string) string {
		// /api/agents/... 和 /api/user/agents/... 都重写为 /agents/...
		if p == "/api/user" || strings.HasPrefix(p, "/api/user/") {
			return strings.TrimPrefix(p, "/api/user")
		}
		return strings.TrimPrefix(p, "/api")
	})
}

func UserConfigProxy() gin.HandlerFunc {
	return ResourceConfigProxy()
}

// userProxy 是这一组反代的公共实现：注入身份、注入服务间密钥、重写路径。
//
// 抽出来是因为 lea-tools 也需要一条（MCP 的 /test /sync 在那一侧），而这三件事一件都
// 不能漏 —— 尤其身份注入的三个头必须 Set-或-Del，"不管"等于把客户端伪造的值转给下游。
// 复制一份的话下一个人只会照抄看得见的部分。
func userProxy(target, panicPrefix string, rewrite func(string) string) gin.HandlerFunc {
	upstream, err := url.Parse(target)
	if err != nil {
		panic(panicPrefix + err.Error())
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
		req.URL.Path = rewrite(req.URL.Path)
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
	return ResourceConfigProxy()
}

func UserTeamsProxy() gin.HandlerFunc {
	return ResourceConfigProxy()
}

func UserSkillsProxy() gin.HandlerFunc {
	return ResourceConfigProxy()
}

func UserToolsProxy() gin.HandlerFunc {
	return ResourceConfigProxy()
}

// UserMCPServersProxy 反代 /api/user/mcp-servers/* → lea-config 的 /mcp-servers/*。
//
// 放开用户私有 MCP server 之后这条路是必需的：用户要建、列、改、删自己的 server，而
// lea-config 不对外暴露（验证栈里只有 nginx 的 8080 通向 lea-agent，线上同理）。少了
// 它的话「用户能建自己的 server」在接口上是**够不着的** —— 库和 CRUD 都对了，但前端
// 没有路。
//
// 授权仍然在 lea-config 那一侧（require_actor + visible_to + _require_can_edit）；
// 这里只做身份注入，跟另外三条完全一样。
func UserMCPServersProxy() gin.HandlerFunc {
	return ResourceConfigProxy()
}

// UserMCPActionsProxy 反代 /api/user/mcp-actions/{id}/test → lea-tools 的
// /admin/mcp-servers/{id}/test。
//
// ## 只剩 /test 了
//
// sync 已经搬到 lea-config（`POST /mcp-servers/{id}/sync`），所以它走上面那条
// UserMCPServersProxy 就通 —— 用户同步自己的 server 不再需要管理员。搬的理由是授权需要
// actor，而 lea-tools 没有；出网那一跳仍然留在 lea-tools（内部调
// /internal/mcp/discover），因为不能让 lea-config 开始打用户填的任意 URL。
//
// `/test` 还在这边：它只连一下、写三列连通状态，**不碰 tools 表**。搬它的收益只有
// "授权更细"，而它今天要 admin 这件事不挡任何人的路 —— 用户建完 server 直接 sync 就行，
// sync 本身就会写连通状态。等有人真的需要"普通用户点一下测试"再搬。
//
// ## 为什么路径叫 mcp-actions 而不是直接映射 /admin/*
//
// 下游那个前缀就叫 /admin/mcp-servers，但**它不是"管理员专用"的意思** —— 那是历史命名
// （见 lea_tools/api/admin_mcp.py 的文件头：那里以前有四组裸奔的 /admin/*，三组已删）。
// 直接把 /api/user/admin/... 暴露出去会让人以为这是管理接口。
//
// 更要紧的是：只映射这一个动作，不是整个 /admin/ 前缀。开一个通向下游 /admin/* 的
// 通道等于把那一侧将来新增的任何 admin 端点也一起暴露了。
func UserMCPActionsProxy() gin.HandlerFunc {
	target := os.Getenv("LEA_TOOLS_URL")
	if target == "" {
		target = "http://localhost:8400"
	}
	return userProxy(target, "bad LEA_TOOLS_URL: ", func(p string) string {
		// /api/user/mcp-actions/7/test → /admin/mcp-servers/7/test
		return strings.Replace(p, "/api/user/mcp-actions", "/admin/mcp-servers", 1)
	})
}
