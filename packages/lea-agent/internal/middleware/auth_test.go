package middleware

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
)

// signToken 在测试内签发 token，claim 结构与 Python 侧 core/auth.py 的
// jose.jwt.encode 一致（sub / tenant_id / roles []string，HS256）。
//
// 之前这里是硬编码的 token 串，exp 写死成固定时间戳 —— 到期后测试全红，
// 且失败信息只说 "invalid token"，看不出是过期。改成运行时签发。
func signToken(t *testing.T, secret string, claims map[string]any) string {
	t.Helper()
	mc := jwt.MapClaims{"exp": time.Now().Add(time.Hour).Unix()}
	for k, v := range claims {
		mc[k] = v
	}
	s, err := jwt.NewWithClaims(jwt.SigningMethodHS256, mc).SignedString([]byte(secret))
	if err != nil {
		t.Fatalf("sign token: %v", err)
	}
	return s
}

// TestJWTAuth_PythonInterop 验证 Python 侧 core/auth.py 出的 token
// （payload: sub / tenant_id / roles []string，HS256 + SECRET_KEY）
// 能被 Go middleware 正确解析并注入 gin.Context。
//
// 下面的 token 由 packages/lea-server core/auth.py 的 jose 用同一密钥生成：
//
//	from jose import jwt
//	jwt.encode({'sub':'u1','tenant_id':'t1','roles':['admin','user'],'exp':...},
//	           'test-secret-h3', algorithm='HS256')
func TestJWTAuth_PythonInterop(t *testing.T) {
	t.Setenv("SECRET_KEY", "test-secret-h3")

	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(JWTAuth())
	var gotUserID, gotTenant, gotRole string
	var gotRoles []string
	r.GET("/ping", func(c *gin.Context) {
		gotUserID = c.GetString("user_id")
		gotTenant = c.GetString("tenant_id")
		gotRole = c.GetString("role")
		if v, ok := c.Get("roles"); ok {
			gotRoles, _ = v.([]string)
		}
		c.Status(http.StatusOK)
	})

	validTok := signToken(t, "test-secret-h3", map[string]any{
		"sub": "u1", "tenant_id": "t1", "roles": []string{"admin", "user"},
	})

	req := httptest.NewRequest("GET", "/ping", nil)
	req.Header.Set("Authorization", "Bearer "+validTok)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d body=%s", w.Code, w.Body.String())
	}
	if gotUserID != "u1" {
		t.Errorf("user_id: got %q want u1", gotUserID)
	}
	if gotTenant != "t1" {
		t.Errorf("tenant_id: got %q want t1", gotTenant)
	}
	if gotRole != "admin" {
		t.Errorf("role: got %q want admin (first from roles[])", gotRole)
	}
	if len(gotRoles) != 2 || gotRoles[0] != "admin" || gotRoles[1] != "user" {
		t.Errorf("roles: got %v want [admin user]", gotRoles)
	}
}

func TestJWTAuth_MissingToken(t *testing.T) {
	t.Setenv("SECRET_KEY", "test-secret-h3")
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(JWTAuth())
	r.GET("/ping", func(c *gin.Context) { c.Status(200) })

	req := httptest.NewRequest("GET", "/ping", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "missing token") {
		t.Errorf("body: %s", w.Body.String())
	}
}

func TestJWTAuth_WrongSecret(t *testing.T) {
	t.Setenv("SECRET_KEY", "wrong-secret")
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(JWTAuth())
	r.GET("/ping", func(c *gin.Context) { c.Status(200) })

	// 用正确密钥签的 token，但 middleware 那边 SECRET_KEY=wrong-secret → 验签失败
	validTok := signToken(t, "test-secret-h3", map[string]any{
		"sub": "u1", "tenant_id": "t1", "roles": []string{"admin", "user"},
	})

	req := httptest.NewRequest("GET", "/ping", nil)
	req.Header.Set("Authorization", "Bearer "+validTok)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d body=%s", w.Code, w.Body.String())
	}
}

func TestJWTAuth_DefaultTenant(t *testing.T) {
	// token 无 tenant_id 时 middleware 应兜底 "default"
	t.Setenv("SECRET_KEY", "test-secret-h3")
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(JWTAuth())
	var gotTenant string
	r.GET("/ping", func(c *gin.Context) { gotTenant = c.GetString("tenant_id"); c.Status(200) })

	tok := signToken(t, "test-secret-h3", map[string]any{
		"sub": "u2", "roles": []string{"user"},
	})
	req := httptest.NewRequest("GET", "/ping", nil)
	req.Header.Set("Authorization", "Bearer "+tok)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d body=%s", w.Code, w.Body.String())
	}
	if gotTenant != "default" {
		t.Errorf("tenant_id fallback: got %q want default", gotTenant)
	}
}

func TestJWTAuth_TrustedGatewayIdentity(t *testing.T) {
	t.Setenv("SECRET_KEY", "test-secret-h3")
	t.Setenv("LEA_INTERNAL_TOKEN", "gateway-secret")
	gin.SetMode(gin.TestMode)
	r := gin.New()
	r.Use(JWTAuth())
	var userID, tenantID, role string
	r.GET("/ping", func(c *gin.Context) {
		userID, tenantID, role = c.GetString("user_id"), c.GetString("tenant_id"), c.GetString("role")
		c.Status(http.StatusOK)
	})

	req := httptest.NewRequest("GET", "/ping", nil)
	req.Header.Set("X-Internal-Token", "gateway-secret")
	req.Header.Set("X-User-ID", "gateway-user")
	req.Header.Set("X-Tenant-ID", "tenant-1")
	req.Header.Set("X-User-Role", "user")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d body=%s", w.Code, w.Body.String())
	}
	if userID != "gateway-user" || tenantID != "tenant-1" || role != "user" {
		t.Fatalf("identity user=%q tenant=%q role=%q", userID, tenantID, role)
	}
}
