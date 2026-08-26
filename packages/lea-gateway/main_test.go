package main

import (
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"math/big"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

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
		config:        newProxy(upstream.URL, "/api/user"),
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
		rag:           newProxy(upstream.URL, "/api/user"),
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
