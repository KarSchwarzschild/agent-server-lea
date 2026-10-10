package main

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"math/big"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

type memoryTenantRoleCache struct {
	values map[string]string
}

func (c *memoryTenantRoleCache) Get(_ context.Context, key string) (string, error) {
	return c.values[key], nil
}

func (c *memoryTenantRoleCache) Set(_ context.Context, key, value string, _ time.Duration) error {
	c.values[key] = value
	return nil
}

func token(t *testing.T, key *rsa.PrivateKey, extra jwt.MapClaims) string {
	t.Helper()
	claims := jwt.MapClaims{"sub": "alice", "UserId": "user-1", "iss": defaultOIDCIssuer, "exp": time.Now().Add(time.Hour).Unix()}
	for key, value := range extra {
		claims[key] = value
	}
	jwtToken := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	jwtToken.Header["kid"] = "test-key"
	signed, err := jwtToken.SignedString(key)
	if err != nil {
		t.Fatal(err)
	}
	return signed
}

func testTenantClient(t *testing.T, tenantType, roleName, role string) *tenantMembershipClient {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") == "" {
			t.Error("membership request is missing Authorization")
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"data": []map[string]any{{
			"tenantId": "t1", "tenantType": tenantType, "userTenantRoleName": roleName,
			"role": role, "disabled": false,
		}}})
	}))
	t.Cleanup(server.Close)
	return &tenantMembershipClient{url: server.URL, client: server.Client()}
}

func testGateway(t *testing.T) (*gateway, *rsa.PrivateKey) {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"keys": []map[string]string{{
			"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "test-key",
			"n": base64.RawURLEncoding.EncodeToString(key.N.Bytes()),
			"e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(key.E)).Bytes()),
		}}})
	}))
	t.Cleanup(server.Close)
	return &gateway{issuer: defaultOIDCIssuer, verifier: newJWKSVerifier(server.URL)}, key
}

func TestAuthRejectsMissingToken(t *testing.T) {
	g, _ := testGateway(t)
	h := g.auth(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) }))
	req := httptest.NewRequest(http.MethodGet, "/api/user/skills", nil)
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)
	if res.Code != http.StatusUnauthorized {
		t.Fatalf("status=%d body=%s", res.Code, res.Body.String())
	}
}

func TestConfigRouteReplacesClientIdentityHeaders(t *testing.T) {
	var got http.Header
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Clone()
		if r.URL.Path != "/skills" {
			t.Errorf("path=%q", r.URL.Path)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()

	g, key := testGateway(t)
	g = &gateway{
		issuer:        g.issuer,
		verifier:      g.verifier,
		internalToken: "internal-token",
		tenantClient:  testTenantClient(t, "Super", "管理者", "Master"),
		config:        newProxy(upstream.URL, "/api/user", "/api"),
	}
	h := g.auth(http.HandlerFunc(g.configRoute))
	req := httptest.NewRequest(http.MethodGet, "/api/user/skills", nil)
	req.Header.Set("Authorization", "Bearer "+token(t, key, nil))
	req.Header.Set("X-Selected-Tenant-ID", "t1")
	req.Header.Set("X-User-ID", "attacker")
	req.Header.Set("X-Tenant-ID", "victim")
	req.Header.Set("X-User-Role", "admin")
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", res.Code, res.Body.String())
	}
	if got.Get("X-User-ID") != "user-1" || got.Get("X-Tenant-ID") != "t1" {
		t.Fatalf("identity headers: %v", got)
	}
	if got.Get("X-User-Role") != "platform_admin" {
		t.Fatalf("role=%q", got.Get("X-User-Role"))
	}
	if got.Get("X-Internal-Token") != "internal-token" {
		t.Fatalf("internal token missing: %v", got)
	}
}

func TestConfigRouteRequiresInternalToken(t *testing.T) {
	g := &gateway{internalToken: ""}
	res := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/api/user/tools", nil)
	g.configRoute(res, req.WithContext(withClaims(req.Context(), &claims{UserID: "alice"})))
	if res.Code != http.StatusServiceUnavailable {
		t.Fatalf("status=%d", res.Code)
	}
}

func TestConfigRouteAppliesTestAdminRoleAfterMembershipValidation(t *testing.T) {
	var got http.Header
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Clone()
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()

	g, key := testGateway(t)
	g = &gateway{
		issuer:        g.issuer,
		verifier:      g.verifier,
		internalToken: "internal-token",
		tenantClient:  testTenantClient(t, "Regular", "编辑", "Editor"),
		testAdminRole: "admin",
		config:        newProxy(upstream.URL, "/api"),
	}
	h := g.auth(http.HandlerFunc(g.configRoute))
	req := httptest.NewRequest(http.MethodGet, "/api/publication-requests", nil)
	req.Header.Set("Authorization", "Bearer "+token(t, key, nil))
	req.Header.Set("X-Selected-Tenant-ID", "t1")
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", res.Code, res.Body.String())
	}
	if got.Get("X-User-Role") != "admin" {
		t.Fatalf("role=%q", got.Get("X-User-Role"))
	}
}

func TestTestAdminRoleDoesNotBypassTenantMembership(t *testing.T) {
	upstreamCalled := false
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		upstreamCalled = true
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()

	g, key := testGateway(t)
	g = &gateway{
		issuer:        g.issuer,
		verifier:      g.verifier,
		internalToken: "internal-token",
		tenantClient:  testTenantClient(t, "Regular", "编辑", "Editor"),
		testAdminRole: "platform_admin",
		config:        newProxy(upstream.URL, "/api"),
	}
	h := g.auth(http.HandlerFunc(g.configRoute))
	req := httptest.NewRequest(http.MethodGet, "/api/publication-requests", nil)
	req.Header.Set("Authorization", "Bearer "+token(t, key, nil))
	req.Header.Set("X-Selected-Tenant-ID", "tenant-not-owned-by-user")
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)

	if res.Code != http.StatusForbidden {
		t.Fatalf("status=%d body=%s", res.Code, res.Body.String())
	}
	if upstreamCalled {
		t.Fatal("request reached upstream without valid tenant membership")
	}
}

func TestParseTestAdminRole(t *testing.T) {
	tests := []struct {
		name        string
		environment string
		configured  string
		want        string
		wantErr     bool
	}{
		{name: "disabled by default", environment: "test"},
		{name: "explicitly disabled", environment: "test", configured: "disabled"},
		{name: "tenant admin", environment: "test", configured: "admin", want: "admin"},
		{name: "platform admin", environment: "TEST", configured: "platform_admin", want: "platform_admin"},
		{name: "rejected outside test", environment: "prod", configured: "admin", wantErr: true},
		{name: "invalid role", environment: "test", configured: "root", wantErr: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, err := parseTestAdminRole(test.environment, test.configured)
			if (err != nil) != test.wantErr {
				t.Fatalf("err=%v wantErr=%v", err, test.wantErr)
			}
			if got != test.want {
				t.Fatalf("role=%q want=%q", got, test.want)
			}
		})
	}
}

func TestRagRouteForwardsTrustedIdentity(t *testing.T) {
	var got http.Header
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Clone()
		if r.URL.Path != "/knowledge-bases" {
			t.Errorf("path=%q", r.URL.Path)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()

	g, key := testGateway(t)
	g = &gateway{
		issuer:        g.issuer,
		verifier:      g.verifier,
		internalToken: "internal-token",
		tenantClient:  testTenantClient(t, "Regular", "编辑", "Editor"),
		rag:           newProxy(upstream.URL, "/api/user", "/api"),
	}
	h := g.auth(http.HandlerFunc(g.ragRoute))
	req := httptest.NewRequest(http.MethodGet, "/api/user/knowledge-bases", nil)
	req.Header.Set("Authorization", "Bearer "+token(t, key, nil))
	req.Header.Set("X-Selected-Tenant-ID", "t1")
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", res.Code, res.Body.String())
	}
	if got.Get("X-User-ID") != "user-1" || got.Get("X-Tenant-ID") != "t1" || got.Get("X-Internal-Token") != "internal-token" {
		t.Fatalf("headers: %v", got)
	}
	if got.Get("X-User-Role") != "user" {
		t.Fatalf("role=%q", got.Get("X-User-Role"))
	}
}

func TestAgentRouteForwardsTrustedIdentity(t *testing.T) {
	var got http.Header
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Clone()
		if r.URL.Path != "/api/sessions" {
			t.Errorf("path=%q", r.URL.Path)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()

	g, key := testGateway(t)
	g = &gateway{
		issuer:        g.issuer,
		verifier:      g.verifier,
		internalToken: "internal-token",
		tenantClient:  testTenantClient(t, "Regular", "编辑", "Editor"),
		agent:         newProxy(upstream.URL),
	}
	h := g.auth(http.HandlerFunc(g.agentRoute))
	req := httptest.NewRequest(http.MethodGet, "/api/sessions", nil)
	req.Header.Set("Authorization", "Bearer "+token(t, key, nil))
	req.Header.Set("X-Selected-Tenant-ID", "t1")
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)
	if res.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", res.Code, res.Body.String())
	}
	if got.Get("Authorization") != "" {
		t.Fatalf("external authorization leaked to agent: %q", got.Get("Authorization"))
	}
	if got.Get("X-User-ID") != "user-1" || got.Get("X-Tenant-ID") != "t1" || got.Get("X-User-Role") != "user" {
		t.Fatalf("identity headers: %v", got)
	}
	if got.Get("X-Internal-Token") != "internal-token" {
		t.Fatalf("internal token missing: %v", got)
	}
}

// TestRouteTableDispatch 钉的是**路由表本身**，不是任何一个 handler。
//
// 上面那些测试都直接拿 g.agentRoute 当 handler 用，绕开了 mux —— 于是"某条路径压根
// 没注册"这类错误它们一条都测不出来。而这恰好是实际发生过的故障：/api/projects 和
// /api/user/files 在 lea-agent 里都实现好了、本地 verify 栈（nginx 直连 lea-agent）
// 也全绿，只有过网关的生产环境 404。
//
// 断言两件事，缺一不可：
//  1. 落到**哪个上游** —— 漏注册会变成 404，注册错会落到别人家。
//  2. 到达上游时的**路径** —— config/rag 的 proxy 带 stripPrefix="/api/user"，
//     所以把一条 /api/user/* 错交给 configRoute 时，上游收到的是被剃过的
//     /storage 而不是 /api/user/storage。只看上游名字的话，这类错会漏过去。
func TestRouteTableDispatch(t *testing.T) {
	var hitUpstream, hitPath string
	newUpstream := func(name string) *httptest.Server {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			hitUpstream, hitPath = name, r.URL.Path
			w.WriteHeader(http.StatusOK)
		}))
		t.Cleanup(server.Close)
		return server
	}
	agent, config, rag, parser := newUpstream("agent"), newUpstream("config"), newUpstream("rag"), newUpstream("parser")

	base, key := testGateway(t)
	g := &gateway{
		issuer:        base.issuer,
		verifier:      base.verifier,
		internalToken: "internal-token",
		tenantClient:  testTenantClient(t, "Regular", "编辑", "Editor"),
		// 前缀跟 main() 里建 proxy 时保持一致，否则测的就不是生产的那张表了。
		agent:  newProxy(agent.URL),
		config: newProxy(config.URL, "/api/user", "/api"),
		rag:    newProxy(rag.URL, "/api/user", "/api"),
		parser: newProxy(parser.URL, "/api"),
	}
	h := g.auth(g.routes())

	cases := []struct {
		path     string
		upstream string
		// upstreamPath 空 = 期望 404（没有任何上游被打到）。
		upstreamPath string
	}{
		// 聊天主链路。
		{"/api/chat/stream", "agent", "/api/chat/stream"},
		{"/api/chat/stop/s1", "agent", "/api/chat/stop/s1"},
		{"/api/chat/status/s1", "agent", "/api/chat/status/s1"},
		{"/api/sessions", "agent", "/api/sessions"},
		{"/api/sessions/s1/messages", "agent", "/api/sessions/s1/messages"},
		{"/api/sessions/s1/active-agent", "agent", "/api/sessions/s1/active-agent"},
		{"/api/files/f1/content", "agent", "/api/files/f1/content"},
		// 项目：整块功能曾经漏注册。
		{"/api/projects", "agent", "/api/projects"},
		{"/api/projects/1/archive", "agent", "/api/projects/1/archive"},
		// 用户文件库：实现在 lea-agent，但路径顶着 /api/user 前缀 —— 交给 config
		// 就会被剃成 /storage、/files 送错门。这几条是防那个的。
		{"/api/user/storage", "agent", "/api/user/storage"},
		{"/api/user/files", "agent", "/api/user/files"},
		{"/api/user/files/upload/7", "agent", "/api/user/files/upload/7"},
		{"/api/user/mcp-actions/3/sync", "agent", "/api/user/mcp-actions/3/sync"},
		// 配置面：这些才是真该剃掉前缀的。
		{"/api/user/agents", "config", "/agents"},
		{"/api/user/skills", "config", "/skills"},
		{"/api/user/teams/9", "config", "/teams/9"},
		{"/api/user/tools", "config", "/tools"},
		{"/api/user/mcp-servers/3", "config", "/mcp-servers/3"},
		{"/api/user/llm-models", "config", "/llm-models"},
		// 知识面。
		{"/api/user/knowledge-bases", "rag", "/knowledge-bases"},
		{"/api/user/memories", "rag", "/memories"},
		// 中性资源路径：跟 /api/user/* 兼容别名落到同一上游、同一路径。
		{"/api/agents", "config", "/agents"},
		{"/api/agents/7/bindings", "config", "/agents/7/bindings"},
		{"/api/teams/9", "config", "/teams/9"},
		{"/api/skills/publish-requests", "config", "/skills/publish-requests"},
		{"/api/tools/3", "config", "/tools/3"},
		{"/api/mcp-servers/3/sync", "config", "/mcp-servers/3/sync"},
		{"/api/publication-requests/8/review", "config", "/publication-requests/8/review"},
		{"/api/audit-events", "config", "/audit-events"},
		{"/api/knowledge-bases", "rag", "/knowledge-bases"},
		{"/api/knowledge-bases/4/documents", "rag", "/knowledge-bases/4/documents"},
		{"/api/publications/tool/3/suspend", "config", "/publications/tool/3/suspend"},
		{"/api/resources/tool/3/checks", "config", "/resources/tool/3/checks"},
		{"/api/providers", "config", "/providers"},
		{"/api/retrieval/models", "config", "/retrieval/models"},
		{"/api/console-context", "config", "/console-context"},
		{"/api/operations/runs", "agent", "/api/operations/runs"},
		{"/api/operations/document-jobs/9/retry", "rag", "/operations/document-jobs/9/retry"},
		{"/api/usage", "parser", "/usage"},
		{"/api/quotas/users/alice", "parser", "/quotas/users/alice"},
		// 白名单没有兜底 pattern，未列出的一律 404 —— 这是故意的，一并钉住。
		{"/api/user/nope", "", ""},
		{"/api/nope", "", ""},
	}

	for _, tc := range cases {
		hitUpstream, hitPath = "", ""
		req := httptest.NewRequest(http.MethodGet, tc.path, nil)
		req.Header.Set("Authorization", "Bearer "+token(t, key, nil))
		req.Header.Set("X-Selected-Tenant-ID", "t1")
		res := httptest.NewRecorder()
		h.ServeHTTP(res, req)

		if tc.upstream == "" {
			if res.Code != http.StatusNotFound {
				t.Errorf("%s: status=%d, want 404 (hit %s%s)", tc.path, res.Code, hitUpstream, hitPath)
			}
			continue
		}
		if res.Code != http.StatusOK {
			t.Errorf("%s: status=%d body=%s", tc.path, res.Code, res.Body.String())
			continue
		}
		if hitUpstream != tc.upstream || hitPath != tc.upstreamPath {
			t.Errorf("%s -> %s%s, want %s%s", tc.path, hitUpstream, hitPath, tc.upstream, tc.upstreamPath)
		}
	}
}

func TestTenantRoleMapping(t *testing.T) {
	cases := []struct {
		tenantType string
		roleName   string
		role       string
		want       string
	}{
		{"Super", "管理者", "Master", "platform_admin"},
		{"Regular", "管理者", "Master", "admin"},
		{"Regular", "编辑", "Editor", "user"},
	}
	for _, tc := range cases {
		if got := mapTenantRole(tc.tenantType, tc.roleName, tc.role); got != tc.want {
			t.Errorf("mapTenantRole(%q, %q, %q)=%q, want %q", tc.tenantType, tc.roleName, tc.role, got, tc.want)
		}
	}
}

func TestTenantMembershipUsesCachedRole(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls++
		_ = json.NewEncoder(w).Encode(map[string]any{"data": []map[string]any{{
			"tenantId": "t1", "tenantType": "Regular", "userTenantRoleName": "编辑",
			"role": "Editor", "disabled": false,
		}}})
	}))
	defer server.Close()
	cache := &memoryTenantRoleCache{values: make(map[string]string)}
	client := &tenantMembershipClient{
		url: server.URL, client: server.Client(), roleCache: cache, cacheTTL: time.Minute,
	}
	for range 2 {
		role, err := client.resolve(context.Background(), "Bearer token", "user-1", "t1")
		if err != nil || role != "user" {
			t.Fatalf("role=%q err=%v", role, err)
		}
	}
	if calls != 1 {
		t.Fatalf("membership calls=%d, want 1", calls)
	}
}

func TestTenantRoleCacheKeyUsesConfiguredPrefix(t *testing.T) {
	key := tenantRoleCacheKey("lea:skill-market", "user-1", "t1")
	if !strings.HasPrefix(key, "lea:skill-market:gateway:tenant-role:v1:") {
		t.Fatalf("unexpected cache key %q", key)
	}
}
