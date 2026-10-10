package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

type claims struct {
	UserID   string `json:"UserId"`
	Subject  string `json:"sub"`
	TenantID string `json:"tenant_id"`
	jwt.RegisteredClaims
}

type gateway struct {
	internalToken string
	issuer        string
	verifier      *jwksVerifier
	tenantClient  *tenantMembershipClient
	testAdminRole string
	agent         *httputil.ReverseProxy
	config        *httputil.ReverseProxy
	rag           *httputil.ReverseProxy
	parser        *httputil.ReverseProxy
}

func main() {
	issuer := env("OIDC_ISSUER", defaultOIDCIssuer)
	jwksURL := env("OIDC_JWKS_URL", issuer+"/.well-known/openid-configuration/jwks")
	testAdminRole, err := parseTestAdminRole(os.Getenv("LEA_ENV"), os.Getenv("LEA_TEST_ADMIN_ROLE"))
	if err != nil {
		log.Fatal(err)
	}
	tenantRoleCache, tenantRoleCacheTTL := newTenantRoleCache()
	redisKeyPrefix := env("REDIS_KEY_PREFIX", "lea:skill-market:")

	agent := newProxy(env("LEA_AGENT_URL", "http://lea-agent:8100"))
	config := newProxy(env("LEA_CONFIG_URL", "http://lea-config:8500"), "/api/user", "/api")
	rag := newProxy(env("LEA_RAG_URL", "http://lea-rag:8300"), "/api/user", "/api")
	parser := newProxy(env("LEA_PARSER_URL", "http://lea-parser:8600"), "/api")
	g := &gateway{
		internalToken: strings.TrimSpace(os.Getenv("LEA_INTERNAL_TOKEN")),
		issuer:        issuer,
		verifier:      newJWKSVerifier(jwksURL),
		testAdminRole: testAdminRole,
		tenantClient: &tenantMembershipClient{
			url:         env("TENANT_MEMBERSHIP_URL", "https://aibase-preview.ouc-online.com.cn/api/TenantUser/CurrUserTenants"),
			client:      &http.Client{Timeout: 10 * time.Second},
			roleCache:   tenantRoleCache,
			cacheTTL:    tenantRoleCacheTTL,
			cachePrefix: redisKeyPrefix,
		},
		agent:  agent,
		config: config,
		rag:    rag,
		parser: parser,
	}

	server := &http.Server{
		Addr:              ":" + env("LEA_GATEWAY_PORT", "8080"),
		Handler:           g.cors(g.auth(g.routes())),
		ReadHeaderTimeout: 10 * time.Second,
	}
	log.Printf("lea-gateway listening on %s", server.Addr)
	log.Fatal(server.ListenAndServe())
}

// routes 建路由表。
//
// 单独一个方法而不是写在 main 里，是为了**让它能被测**：改之前所有测试都直接
// 拿 g.agentRoute / g.configRoute 当 handler 用，于是 mux 本身一行都没覆盖 ——
// 而出问题的恰好是它（/api/projects 和 /api/user/files 漏注册，生产 404）。
// 测 handler 测不出漏注册，只有测这张表才行。
func (g *gateway) routes() *http.ServeMux {
	// 这张表是**白名单，没有兜底 pattern** —— 没列在这里的 /api/* 一律 404。
	//
	// 那是故意的（兜底一加，将来 lea-config 新增的路径会被静默转给 lea-agent），
	// 代价是**每条新的后端路由都必须回来加一行**。而漏加的症状极其难认：
	// 本地 verify 栈走的是 nginx → lea-agent 直连（infra/docker-compose.verify.yml），
	// lea-agent 自己的路由表是全的，所以漏加的那条在开发环境**完全正常**，
	// 只在过网关的生产环境 404。项目和用户文件库都这么漏过一轮。
	//
	// 加路由时先问一句"这条的实现在哪个服务"，别看 /api/user 前缀 —— 见下面
	// /api/user/storage 那段。
	mux := http.NewServeMux()
	mux.HandleFunc("/health", g.health)
	mux.HandleFunc("/api/tenants", g.tenants)
	// 中性资源路径是新契约。角色、租户和对象归属由下游服务决定，不把调用者角色
	// 编进 URL。/api/user/* 暂留作兼容别名。
	mux.HandleFunc("/api/agents", g.configRoute)
	mux.HandleFunc("/api/agents/", g.configRoute)
	mux.HandleFunc("/api/teams", g.configRoute)
	mux.HandleFunc("/api/teams/", g.configRoute)
	mux.HandleFunc("/api/skills", g.configRoute)
	mux.HandleFunc("/api/skills/", g.configRoute)
	mux.HandleFunc("/api/tools", g.configRoute)
	mux.HandleFunc("/api/tools/", g.configRoute)
	mux.HandleFunc("/api/mcp-servers", g.configRoute)
	mux.HandleFunc("/api/mcp-servers/", g.configRoute)
	mux.HandleFunc("/api/publication-requests", g.configRoute)
	mux.HandleFunc("/api/publication-requests/", g.configRoute)
	mux.HandleFunc("/api/audit-events", g.configRoute)
	mux.HandleFunc("/api/audit-events/", g.configRoute)
	mux.HandleFunc("/api/publications", g.configRoute)
	mux.HandleFunc("/api/publications/", g.configRoute)
	mux.HandleFunc("/api/resources/", g.configRoute)
	mux.HandleFunc("/api/providers", g.configRoute)
	mux.HandleFunc("/api/providers/", g.configRoute)
	mux.HandleFunc("/api/retrieval/models", g.configRoute)
	mux.HandleFunc("/api/retrieval/models/", g.configRoute)
	mux.HandleFunc("/api/console-context", g.configRoute)
	mux.HandleFunc("/api/operations/runs", g.agentRoute)
	mux.HandleFunc("/api/operations/runs/", g.agentRoute)
	mux.HandleFunc("/api/operations/document-jobs", g.ragRoute)
	mux.HandleFunc("/api/operations/document-jobs/", g.ragRoute)
	mux.HandleFunc("/api/usage", g.parserRoute)
	mux.HandleFunc("/api/quotas", g.parserRoute)
	mux.HandleFunc("/api/quotas/", g.parserRoute)
	mux.HandleFunc("/api/knowledge-bases", g.ragRoute)
	mux.HandleFunc("/api/knowledge-bases/", g.ragRoute)

	mux.HandleFunc("/api/user/agents", g.configRoute)
	mux.HandleFunc("/api/user/agents/", g.configRoute)
	mux.HandleFunc("/api/user/teams", g.configRoute)
	mux.HandleFunc("/api/user/teams/", g.configRoute)
	mux.HandleFunc("/api/user/skills", g.configRoute)
	mux.HandleFunc("/api/user/skills/", g.configRoute)
	mux.HandleFunc("/api/user/tools", g.configRoute)
	mux.HandleFunc("/api/user/tools/", g.configRoute)
	mux.HandleFunc("/api/user/mcp-servers", g.configRoute)
	mux.HandleFunc("/api/user/mcp-servers/", g.configRoute)
	// 可选聊天模型列表（只投影模型名，不含 provider 的 base_url/api_key）。
	mux.HandleFunc("/api/user/llm-models", g.configRoute)
	// 可选检索模型列表（embedding/reranker/vlm，建库表单用），同样只投影安全字段。
	mux.HandleFunc("/api/user/retrieval-models", g.configRoute)
	mux.HandleFunc("/api/user/knowledge-bases", g.ragRoute)
	mux.HandleFunc("/api/user/knowledge-bases/", g.ragRoute)
	mux.HandleFunc("/api/user/memories", g.ragRoute)
	mux.HandleFunc("/api/user/memories/", g.ragRoute)
	mux.HandleFunc("/api/chat", g.agentRoute)
	mux.HandleFunc("/api/chat/", g.agentRoute)
	mux.HandleFunc("/api/sessions", g.agentRoute)
	mux.HandleFunc("/api/sessions/", g.agentRoute)
	mux.HandleFunc("/api/files/", g.agentRoute)
	// 项目（会话的分组）。lea-agent 侧是 handleListProjects 那一组。
	mux.HandleFunc("/api/projects", g.agentRoute)
	mux.HandleFunc("/api/projects/", g.agentRoute)
	// 用户文件库（跨会话那一层）。
	//
	// ⚠️ 走 agentRoute 而**不是** configRoute —— 别被 /api/user 前缀骗了。这两条的
	// 实现在 lea-agent（router/user_files.go），而 config/rag 那两个 proxy 建出来时
	// 带了 stripPrefix="/api/user"，转过去会变成 lea-config 的 /storage、/files，
	// 那边根本没有这两条路由。
	mux.HandleFunc("/api/user/storage", g.agentRoute)
	mux.HandleFunc("/api/user/files", g.agentRoute)
	mux.HandleFunc("/api/user/files/", g.agentRoute)
	// MCP 的 test/sync 动作，由 lea-agent 反代到 lea-tools 的 /admin/mcp-servers/*。
	mux.HandleFunc("/api/user/mcp-actions/", g.agentRoute)
	return mux
}

func (g *gateway) health(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"status":"ok","service":"lea-gateway"}`))
}

func (g *gateway) tenants(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	items, err := g.tenantClient.list(r.Context(), r.Header.Get("Authorization"))
	if err != nil {
		http.Error(w, `{"error":"could not list tenants"}`, http.StatusBadGateway)
		return
	}
	result := make([]map[string]string, 0, len(items))
	for _, item := range items {
		if item.Disabled || item.TenantID == "" {
			continue
		}
		result = append(result, map[string]string{
			"id": item.TenantID, "type": item.TenantType, "role": g.effectiveRole(mapTenantRole(item.TenantType, item.UserTenantRoleName, item.Role)),
		})
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{"items": result})
}

func (g *gateway) auth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/health" {
			next.ServeHTTP(w, r)
			return
		}
		token := strings.TrimSpace(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))
		if token == "" {
			http.Error(w, `{"error":"missing token"}`, http.StatusUnauthorized)
			return
		}
		c, err := g.parseClaims(token)
		if err != nil {
			log.Printf("gateway_auth_rejected reason=%v", err)
			http.Error(w, `{"error":"invalid token"}`, http.StatusUnauthorized)
			return
		}
		ctx := withClaims(r.Context(), c)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func (g *gateway) parseClaims(token string) (*claims, error) {
	c := &claims{}
	parsed, err := jwt.ParseWithClaims(token, c, func(t *jwt.Token) (any, error) {
		if t.Method != jwt.SigningMethodRS256 {
			return nil, fmt.Errorf("unexpected signing method")
		}
		kid, _ := t.Header["kid"].(string)
		if kid == "" {
			return nil, fmt.Errorf("missing key id")
		}
		return g.verifier.key(kid)
	}, jwt.WithValidMethods([]string{jwt.SigningMethodRS256.Alg()}), jwt.WithIssuer(g.issuer))
	if err != nil || !parsed.Valid {
		if err != nil {
			return nil, fmt.Errorf("OIDC token validation: %w", err)
		}
		return nil, fmt.Errorf("OIDC token validation: token is not valid")
	}
	if c.UserID == "" {
		c.UserID = c.Subject
	}
	if c.UserID == "" {
		return nil, fmt.Errorf("missing user id")
	}
	return c, nil
}

func (g *gateway) configRoute(w http.ResponseWriter, r *http.Request) {
	g.trustedRoute(w, r, g.config)
}

func (g *gateway) ragRoute(w http.ResponseWriter, r *http.Request) {
	g.trustedRoute(w, r, g.rag)
}

func (g *gateway) parserRoute(w http.ResponseWriter, r *http.Request) {
	g.trustedRoute(w, r, g.parser)
}

func (g *gateway) trustedRoute(w http.ResponseWriter, r *http.Request, proxy *httputil.ReverseProxy) {
	if g.internalToken == "" {
		http.Error(w, `{"error":"gateway internal token is not configured"}`, http.StatusServiceUnavailable)
		return
	}
	c := claimsFromContext(r.Context())
	if g.tenantClient == nil {
		http.Error(w, `{"error":"tenant membership is not configured"}`, http.StatusServiceUnavailable)
		return
	}
	selectedTenant := strings.TrimSpace(r.Header.Get("X-Selected-Tenant-ID"))
	role, err := g.tenantClient.resolve(r.Context(), r.Header.Get("Authorization"), c.UserID, selectedTenant)
	if err != nil {
		http.Error(w, `{"error":"selected tenant is invalid"}`, http.StatusForbidden)
		return
	}
	role = g.effectiveRole(role)
	// The config service consumes these only after the gateway has verified JWT.
	r.Header.Del("X-User-ID")
	r.Header.Del("X-Tenant-ID")
	r.Header.Del("X-User-Role")
	r.Header.Del("X-Selected-Tenant-ID")
	r.Header.Set("X-User-ID", c.UserID)
	r.Header.Set("X-Tenant-ID", selectedTenant)
	r.Header.Set("X-User-Role", role)
	r.Header.Set("X-Internal-Token", g.internalToken)
	proxy.ServeHTTP(w, r)
}

func (g *gateway) agentRoute(w http.ResponseWriter, r *http.Request) {
	if g.internalToken == "" {
		http.Error(w, `{"error":"gateway internal token is not configured"}`, http.StatusServiceUnavailable)
		return
	}
	c := claimsFromContext(r.Context())
	selectedTenant := strings.TrimSpace(r.Header.Get("X-Selected-Tenant-ID"))
	role, err := g.tenantClient.resolve(r.Context(), r.Header.Get("Authorization"), c.UserID, selectedTenant)
	if err != nil {
		http.Error(w, `{"error":"selected tenant is invalid"}`, http.StatusForbidden)
		return
	}
	role = g.effectiveRole(role)
	r.Header.Del("Authorization")
	r.Header.Del("X-User-ID")
	r.Header.Del("X-Tenant-ID")
	r.Header.Del("X-User-Role")
	r.Header.Del("X-Selected-Tenant-ID")
	r.Header.Set("X-User-ID", c.UserID)
	r.Header.Set("X-Tenant-ID", selectedTenant)
	r.Header.Set("X-User-Role", role)
	r.Header.Set("X-Internal-Token", g.internalToken)
	g.agent.ServeHTTP(w, r)
}

func parseTestAdminRole(environment, configuredRole string) (string, error) {
	role := strings.ToLower(strings.TrimSpace(configuredRole))
	if role == "" || role == "disabled" {
		return "", nil
	}
	if !strings.EqualFold(strings.TrimSpace(environment), "test") {
		return "", fmt.Errorf("LEA_TEST_ADMIN_ROLE is only allowed when LEA_ENV=test")
	}
	if role != "admin" && role != "platform_admin" {
		return "", fmt.Errorf("invalid LEA_TEST_ADMIN_ROLE %q: expected disabled, admin, or platform_admin", configuredRole)
	}
	return role, nil
}

func (g *gateway) effectiveRole(membershipRole string) string {
	if g.testAdminRole != "" {
		return g.testAdminRole
	}
	return membershipRole
}

func trustedRole(roles []string) string {
	for _, wanted := range []string{"platform_admin", "admin"} {
		for _, role := range roles {
			if strings.EqualFold(strings.TrimSpace(role), wanted) {
				return wanted
			}
		}
	}
	return ""
}

func newProxy(raw string, stripPrefixes ...string) *httputil.ReverseProxy {
	target, err := url.Parse(raw)
	if err != nil {
		log.Fatalf("invalid upstream URL %q: %v", raw, err)
	}
	p := httputil.NewSingleHostReverseProxy(target)
	original := p.Director
	p.Director = func(r *http.Request) {
		original(r)
		for _, stripPrefix := range stripPrefixes {
			if stripPrefix == "" || (r.URL.Path != stripPrefix && !strings.HasPrefix(r.URL.Path, stripPrefix+"/")) {
				continue
			}
			r.URL.Path = strings.TrimPrefix(r.URL.Path, stripPrefix)
			if r.URL.Path == "" {
				r.URL.Path = "/"
			}
			break
		}
		r.Host = target.Host
	}
	return p
}

func (g *gateway) cors(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Selected-Tenant-ID")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func env(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}
