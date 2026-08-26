package main

import (
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
	UserID  string `json:"UserId"`
	Subject string `json:"sub"`
	jwt.RegisteredClaims
}

type gateway struct {
	internalToken string
	issuer        string
	verifier      *jwksVerifier
	tenantClient  *tenantMembershipClient
	agent         *httputil.ReverseProxy
	config        *httputil.ReverseProxy
	rag           *httputil.ReverseProxy
}

func main() {
	issuer := env("OIDC_ISSUER", defaultOIDCIssuer)
	jwksURL := env("OIDC_JWKS_URL", issuer+"/.well-known/openid-configuration/jwks")

	agent := newProxy(env("LEA_AGENT_URL", "http://lea-agent:8100"), "")
	config := newProxy(env("LEA_CONFIG_URL", "http://lea-config:8500"), "/api/user")
	rag := newProxy(env("LEA_RAG_URL", "http://lea-rag:8300"), "/api/user")
	g := &gateway{
		internalToken: strings.TrimSpace(os.Getenv("LEA_INTERNAL_TOKEN")),
		issuer:        issuer,
		verifier:      newJWKSVerifier(jwksURL),
		tenantClient: &tenantMembershipClient{
			url:    env("TENANT_MEMBERSHIP_URL", "https://aibase-preview.ouc-online.com.cn/api/TenantUser/CurrUserTenants"),
			client: &http.Client{Timeout: 10 * time.Second},
		},
		agent:  agent,
		config: config,
		rag:    rag,
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", g.health)
	mux.HandleFunc("/api/user/agents", g.configRoute)
	mux.HandleFunc("/api/user/agents/", g.configRoute)
	mux.HandleFunc("/api/user/skills", g.configRoute)
	mux.HandleFunc("/api/user/skills/", g.configRoute)
	mux.HandleFunc("/api/user/tools", g.configRoute)
	mux.HandleFunc("/api/user/tools/", g.configRoute)
	mux.HandleFunc("/api/user/knowledge-bases", g.ragRoute)
	mux.HandleFunc("/api/user/knowledge-bases/", g.ragRoute)
	mux.HandleFunc("/api/user/memories", g.ragRoute)
	mux.HandleFunc("/api/user/memories/", g.ragRoute)
	mux.HandleFunc("/api/chat", g.agentRoute)
	mux.HandleFunc("/api/chat/", g.agentRoute)
	mux.HandleFunc("/api/sessions", g.agentRoute)
	mux.HandleFunc("/api/sessions/", g.agentRoute)
	mux.HandleFunc("/api/files/", g.agentRoute)

	server := &http.Server{
		Addr:              ":" + env("LEA_GATEWAY_PORT", "8080"),
		Handler:           g.cors(g.auth(mux)),
		ReadHeaderTimeout: 10 * time.Second,
	}
	log.Printf("lea-gateway listening on %s", server.Addr)
	log.Fatal(server.ListenAndServe())
}

func (g *gateway) health(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"status":"ok","service":"lea-gateway"}`))
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
		parsed, err := jwt.ParseWithClaims(token, &claims{}, func(t *jwt.Token) (any, error) {
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
			http.Error(w, `{"error":"invalid token"}`, http.StatusUnauthorized)
			return
		}
		c, ok := parsed.Claims.(*claims)
		if !ok {
			http.Error(w, `{"error":"invalid token claims"}`, http.StatusUnauthorized)
			return
		}
		if c.UserID == "" {
			c.UserID = c.Subject
		}
		if c.UserID == "" {
			http.Error(w, `{"error":"missing UserId claim"}`, http.StatusUnauthorized)
			return
		}
		ctx := withClaims(r.Context(), c)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func (g *gateway) configRoute(w http.ResponseWriter, r *http.Request) {
	g.trustedRoute(w, r, g.config)
}

func (g *gateway) ragRoute(w http.ResponseWriter, r *http.Request) {
	g.trustedRoute(w, r, g.rag)
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
	role, err := g.tenantClient.resolve(r.Header.Get("Authorization"), selectedTenant)
	if err != nil {
		http.Error(w, `{"error":"selected tenant is invalid"}`, http.StatusForbidden)
		return
	}
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
	// lea-agent verifies the same user JWT itself; do not replace Authorization.
	g.agent.ServeHTTP(w, r)
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

func newProxy(raw, stripPrefix string) *httputil.ReverseProxy {
	target, err := url.Parse(raw)
	if err != nil {
		log.Fatalf("invalid upstream URL %q: %v", raw, err)
	}
	p := httputil.NewSingleHostReverseProxy(target)
	original := p.Director
	p.Director = func(r *http.Request) {
		original(r)
		if stripPrefix != "" {
			r.URL.Path = strings.TrimPrefix(r.URL.Path, stripPrefix)
			if r.URL.Path == "" {
				r.URL.Path = "/"
			}
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
