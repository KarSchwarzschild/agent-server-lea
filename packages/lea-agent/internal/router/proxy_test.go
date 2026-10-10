package router

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"

	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/runtime"
)

// /api/user/agents 这条反代是 lea-config 唯一一个**不经过**
// runtime/internal_token.go 那个 Transport 的调用方（它用 httputil.ReverseProxy，
// 不是 runtime 包里那五个 http.Client）。lea-config 开了鉴权之后漏掉这里的症状是
// 前端 Agent 列表整个空掉，而且只在配了密钥的环境出现 —— 本地永远是绿的。
//
// 下面三条分别盯：带上了、没配时不带、以及客户端伪造的同名头会被清掉。

// upstreamSpy 起一个假的 lea-config，把收到的头记下来。
func upstreamSpy(t *testing.T) (*httptest.Server, *http.Header) {
	t.Helper()
	var got http.Header
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Clone()
		w.WriteHeader(200)
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	t.Cleanup(srv.Close)
	return srv, &got
}

// callProxy 用给定的 LEA_INTERNAL_TOKEN 建一次反代并打一个请求。
//
// 注意必须在 UserAgentsProxy() **之前**设环境变量 —— 它在构造时读一次
// （跟 runtime 那边的 init 一样，都是进程启动时的一次性读取）。
//
// 这里必须起真 server 而不是用 httptest.NewRecorder：ReverseProxy 会调
// ResponseWriter 的 CloseNotify，而 Recorder 不实现 http.CloseNotifier，
// gin 的 responseWriter 包着它一调就 panic。真 server 的 writer 有这个方法。
func callProxy(t *testing.T, token string, clientHeaders map[string]string) *http.Header {
	return callProxyAs(t, token, nil, clientHeaders)
}

// callProxyAs 额外模拟 JWTAuth 中间件的产物：把已验过的身份放进 gin context。
//
// 反代读的是 context 而不是请求头（那正是它的意义 —— 头可能是伪造的），所以测
// 身份重写必须从这一层灌进去。identity 传 nil 表示"中间件没跑/token 里没这些"。
func callProxyAs(
	t *testing.T,
	token string,
	identity map[string]any,
	clientHeaders map[string]string,
) *http.Header {
	t.Helper()
	upstream, got := upstreamSpy(t)
	t.Setenv("LEA_CONFIG_URL", upstream.URL)
	t.Setenv("LEA_INTERNAL_TOKEN", token)

	r := gin.New()
	if identity != nil {
		r.Use(func(c *gin.Context) {
			for k, v := range identity {
				c.Set(k, v)
			}
			c.Next()
		})
	}
	r.Any("/api/user/agents", UserAgentsProxy())
	r.Any("/api/user/tools", UserToolsProxy())
	front := httptest.NewServer(r)
	t.Cleanup(front.Close)

	req, err := http.NewRequest("GET", front.URL+"/api/user/agents", nil)
	if err != nil {
		t.Fatal(err)
	}
	for k, v := range clientHeaders {
		req.Header.Set(k, v)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("请求失败: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("proxy 没打通: HTTP %d", resp.StatusCode)
	}
	return got
}

func TestUserToolsProxy_RewritesToConfigTools(t *testing.T) {
	upstream, got := upstreamSpy(t)
	t.Setenv("LEA_CONFIG_URL", upstream.URL)
	t.Setenv("LEA_INTERNAL_TOKEN", "tok")

	r := gin.New()
	r.Use(func(c *gin.Context) {
		c.Set("user_id", "alice")
		c.Set("tenant_id", "t1")
		c.Set("roles", []string{"user"})
		c.Next()
	})
	r.Any("/api/user/tools", UserToolsProxy())
	front := httptest.NewServer(r)
	t.Cleanup(front.Close)

	resp, err := http.Get(front.URL + "/api/user/tools")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("tools proxy: HTTP %d", resp.StatusCode)
	}
	if got.Get("X-User-ID") != "alice" || got.Get("X-Tenant-ID") != "t1" {
		t.Fatalf("tools proxy did not inject JWT identity: %v", *got)
	}
}

func TestResourceConfigProxy_RewritesCanonicalAndLegacyPaths(t *testing.T) {
	var gotPath string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(upstream.Close)
	t.Setenv("LEA_CONFIG_URL", upstream.URL)
	t.Setenv("LEA_INTERNAL_TOKEN", "tok")

	r := gin.New()
	proxy := ResourceConfigProxy()
	r.Any("/api/agents/*path", proxy)
	r.Any("/api/user/agents/*path", proxy)
	front := httptest.NewServer(r)
	t.Cleanup(front.Close)

	for _, path := range []string{"/api/agents/7/bindings", "/api/user/agents/7/bindings"} {
		gotPath = ""
		resp, err := http.Get(front.URL + path)
		if err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		_ = resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("%s: status=%d", path, resp.StatusCode)
		}
		if gotPath != "/agents/7/bindings" {
			t.Errorf("%s -> %s, want /agents/7/bindings", path, gotPath)
		}
	}
}

func TestResourceConfigProxy_RewritesPublicationRequestPath(t *testing.T) {
	var gotPath string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(upstream.Close)
	t.Setenv("LEA_CONFIG_URL", upstream.URL)

	r := gin.New()
	r.Any("/api/publication-requests/*path", ResourceConfigProxy())
	front := httptest.NewServer(r)
	t.Cleanup(front.Close)

	resp, err := http.Post(front.URL+"/api/publication-requests/8/review", "application/json", nil)
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status=%d", resp.StatusCode)
	}
	if gotPath != "/publication-requests/8/review" {
		t.Fatalf("got path %s", gotPath)
	}
}

func TestResourceConfigProxy_RewritesAuditEventsPath(t *testing.T) {
	var gotPath string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(upstream.Close)
	t.Setenv("LEA_CONFIG_URL", upstream.URL)

	r := gin.New()
	r.Any("/api/audit-events", ResourceConfigProxy())
	front := httptest.NewServer(r)
	t.Cleanup(front.Close)

	resp, err := http.Get(front.URL + "/api/audit-events")
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK || gotPath != "/audit-events" {
		t.Fatalf("status=%d path=%s", resp.StatusCode, gotPath)
	}
}

func TestUserAgentsProxy_SendsInternalToken(t *testing.T) {
	got := callProxy(t, "s3cret", nil)
	if v := got.Get(runtime.InternalTokenHeader); v != "s3cret" {
		t.Fatalf("上游没收到服务间鉴权头: %q —— lea-config 会返 401", v)
	}
}

// 没配密钥 = 这套机制没启用，不该凭空造一个头出来。
func TestUserAgentsProxy_NoTokenNoHeader(t *testing.T) {
	got := callProxy(t, "", nil)
	if _, ok := (*got)[http.CanonicalHeaderKey(runtime.InternalTokenHeader)]; ok {
		t.Fatal("没配 token 却带了头 —— 会变成一个空值/垃圾值的鉴权尝试")
	}
}

// 最要紧的一条：客户端自己塞的 X-Internal-Token 必须被清掉，不能原样转给 lea-config。
//
// 反代默认会把入站请求的头**全部**带给上游。所以在没配密钥的部署里，浏览器只要
// 自己加一个这个头，就能替我们完成 lea-config 那边的鉴权 —— 一个完全绕过。
// 跟旁边 X-Tenant-ID / X-User-ID 用 Del 兜底是同一个道理。
func TestUserAgentsProxy_StripsClientSuppliedToken(t *testing.T) {
	got := callProxy(t, "", map[string]string{
		runtime.InternalTokenHeader: "guessed-by-attacker",
	})
	if v := got.Get(runtime.InternalTokenHeader); v != "" {
		t.Fatalf("客户端伪造的鉴权头被转给了上游: %q —— 这是一个绕过", v)
	}
}

// 配了密钥时，客户端伪造的值必须被我们的真值覆盖（Set 而不是 Add）。
// 用 Add 的话上游会看到两个值，httpx/starlette 取第一个 —— 可能就是攻击者那个。
func TestUserAgentsProxy_OverridesClientSuppliedToken(t *testing.T) {
	got := callProxy(t, "real-token", map[string]string{
		runtime.InternalTokenHeader: "guessed-by-attacker",
	})
	vals := (*got)[http.CanonicalHeaderKey(runtime.InternalTokenHeader)]
	if len(vals) != 1 || vals[0] != "real-token" {
		t.Fatalf("鉴权头没被干净覆盖: %v", vals)
	}
}

// ── X-User-Role ───────────────────────────────────────────────────────────
//
// 下游的 require_admin()（lea_authkit/authz.py）判的就是这个头，所以它跟
// X-User-ID / X-Tenant-ID 是同一级别的东西。这组测试盯三件事：管理员下发得到、
// 普通用户拿不到、以及**伪造的一律被清掉**。
//
// 这里曾经是个真洞：反代只重写 user/tenant 两个头，X-User-Role 原样转发 ——
// 浏览器加一个 X-User-Role: admin 就通过了下游的管理员门。

const roleHeader = "X-User-Role"

func TestUserAgentsProxy_ForwardsAdminRole(t *testing.T) {
	got := callProxyAs(t, "tok", map[string]any{
		"user_id": "root", "tenant_id": "t1", "roles": []string{"admin"},
	}, nil)
	if v := got.Get(roleHeader); v != "admin" {
		t.Fatalf("管理员角色没下发: %q —— 下游会把管理员当普通用户拒掉", v)
	}
}

func TestUserAgentsProxy_PlainUserGetsNoRole(t *testing.T) {
	got := callProxyAs(t, "tok", map[string]any{
		"user_id": "alice", "tenant_id": "t1", "roles": []string{"user"},
	}, nil)
	if v := got.Get(roleHeader); v != "" {
		t.Fatalf("普通用户拿到了角色头: %q", v)
	}
}

// 最要紧的一条：客户端自己塞的 X-User-Role 必须被清掉。
func TestUserAgentsProxy_StripsForgedRole(t *testing.T) {
	got := callProxyAs(t, "tok", map[string]any{
		"user_id": "alice", "tenant_id": "t1", "roles": []string{"user"},
	}, map[string]string{roleHeader: "admin"})
	if v := got.Get(roleHeader); v != "" {
		t.Fatalf("伪造的管理员角色被转给了下游: %q —— 这是一个提权", v)
	}
}

// 没有 roles（token 里没这个 claim）时，伪造的头同样要被删掉。
func TestUserAgentsProxy_StripsForgedRoleWhenNoRoles(t *testing.T) {
	got := callProxyAs(t, "tok", map[string]any{
		"user_id": "alice", "tenant_id": "t1",
	}, map[string]string{roleHeader: "admin"})
	if v := got.Get(roleHeader); v != "" {
		t.Fatalf("无 roles claim 时伪造的头没被删: %q", v)
	}
}

func TestUserAgentsProxy_ForwardsPlatformAdminFromJWT(t *testing.T) {
	got := callProxyAs(t, "tok", map[string]any{
		"user_id": "god", "tenant_id": "t1",
		"roles": []string{"platform_admin"},
	}, nil)
	if v := got.Get(roleHeader); v != "platform_admin" {
		t.Fatalf("平台管理员角色没下发: %q", v)
	}
}

// 多角色时挑得出白名单里那个，不受顺序影响。
func TestUserAgentsProxy_PicksAdminFromMultipleRoles(t *testing.T) {
	got := callProxyAs(t, "tok", map[string]any{
		"user_id": "root", "tenant_id": "t1",
		"roles": []string{"teacher", "admin", "user"},
	}, nil)
	if v := got.Get(roleHeader); v != "admin" {
		t.Fatalf("多角色里没挑出 admin: %q", v)
	}
}

func TestUserAgentsProxy_PrefersPlatformAdminFromMultipleRoles(t *testing.T) {
	got := callProxyAs(t, "tok", map[string]any{
		"user_id": "root", "tenant_id": "t1",
		"roles": []string{"admin", "platform_admin", "user"},
	}, nil)
	if v := got.Get(roleHeader); v != "platform_admin" {
		t.Fatalf("多角色里没优先下发 platform_admin: %q", v)
	}
}

// 大小写和空格不该让一个真管理员被降级。
func TestUserAgentsProxy_NormalizesRoleCasing(t *testing.T) {
	got := callProxyAs(t, "tok", map[string]any{
		"user_id": "root", "tenant_id": "t1", "roles": []string{" Admin "},
	}, nil)
	if v := got.Get(roleHeader); v != "admin" {
		t.Fatalf("大小写/空格导致管理员被降级: %q", v)
	}
}

// 身份三件套一起验：都从 context 来，都覆盖客户端的伪造值。
func TestUserAgentsProxy_OverridesAllThreeIdentityHeaders(t *testing.T) {
	got := callProxyAs(t, "tok", map[string]any{
		"user_id": "real-user", "tenant_id": "real-tenant",
		"roles": []string{"admin"},
	}, map[string]string{
		"X-User-ID":   "attacker",
		"X-Tenant-ID": "victim-tenant",
		roleHeader:    "platform_admin",
	})
	for h, want := range map[string]string{
		"X-User-ID":   "real-user",
		"X-Tenant-ID": "real-tenant",
		roleHeader:    "admin",
	} {
		if v := got.Get(h); v != want {
			t.Errorf("%s = %q, 期望 %q", h, v, want)
		}
	}
}
