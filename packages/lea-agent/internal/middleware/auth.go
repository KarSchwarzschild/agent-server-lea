package middleware

import (
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
)

// Claims 对齐 lea-server core/auth.py：
//   - sub: user_id (string)
//   - tenant_id: string, 缺省 "default"
//   - roles: []string（Python 侧字段名，注意不是 "role"）
type Claims struct {
	UserID   string   `json:"sub"`
	TenantID string   `json:"tenant_id"`
	Roles    []string `json:"roles"`
	jwt.RegisteredClaims
}

// JWTAuth 校验 Authorization: Bearer <token>，与 Python 侧共用 SECRET_KEY + HS256。
// H3: role 从 roles[] 里挑第一个下发，兼容旧 handler 拿 c.GetString("role") 的场景。
//
// 没有任何 bypass。（这里曾有一行注释说 "X-Auth-Skip: dev" 能跳过校验，
// 但函数体里从来没实现过 —— 删掉，否则迟早有人照注释去文档化一个不存在的后门，
// 或者照它去实现一个真的。）
func JWTAuth() gin.HandlerFunc {
	secret := []byte(jwtSecret())
	return func(c *gin.Context) {
		auth := c.GetHeader("Authorization")
		if auth == "" {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "missing token"})
			return
		}
		token := strings.TrimPrefix(auth, "Bearer ")
		claims := &Claims{}
		t, err := jwt.ParseWithClaims(token, claims, func(t *jwt.Token) (any, error) {
			if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
				return nil, fmt.Errorf("unexpected signing method: %v", t.Header["alg"])
			}
			return secret, nil
		})
		if err != nil || !t.Valid {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "invalid token"})
			return
		}
		if claims.UserID == "" {
			c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "missing sub claim"})
			return
		}
		if claims.TenantID == "" {
			claims.TenantID = "default"
		}
		role := ""
		if len(claims.Roles) > 0 {
			role = claims.Roles[0]
		}
		c.Set("user_id", claims.UserID)
		c.Set("tenant_id", claims.TenantID)
		c.Set("roles", claims.Roles)
		c.Set("role", role)
		c.Next()
	}
}

func CORS() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Header("Access-Control-Allow-Origin", "*")
		c.Header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
		c.Header("Access-Control-Allow-Headers", "Authorization,Content-Type")
		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(204)
			return
		}
		c.Next()
	}
}

// devFallbackSecret 是本地开发用的固定密钥，跟 lea-server config.py 里的默认值一致。
// 它写在源码里、进了 git 历史，所以任何人都能用它签出合法 token —— 只在显式
// 开了 LEA_AGENT_ALLOW_DEV_SECRET=1 时才允许使用。
const devFallbackSecret = "dev-secret-key-change-me"

// jwtSecret 取 SECRET_KEY。部署时 lea-server / lea-config / lea-agent 必须共享同一个。
//
// 缺失时**直接退出**，不再静默回落到 devFallbackSecret。以前是回落的，后果是
// 少配一个环境变量就把整套鉴权变成摆设：密钥公开在仓库里，谁都能签 token，
// 而服务照常启动、日志一切正常，从外部完全看不出来。宁可起不来。
//
// 本地确实需要跑而懒得配密钥时，显式设 LEA_AGENT_ALLOW_DEV_SECRET=1。
func jwtSecret() string {
	if v := os.Getenv("SECRET_KEY"); v != "" {
		return v
	}
	if os.Getenv("LEA_AGENT_ALLOW_DEV_SECRET") == "1" {
		log.Printf("WARNING: SECRET_KEY 未设置，正在使用公开的开发密钥 —— " +
			"任何人都能伪造 token，切勿用于生产")
		return devFallbackSecret
	}
	log.Fatal("SECRET_KEY 未设置。JWT 校验需要它，且必须与 lea-server / lea-config 一致。" +
		"本地调试可设 LEA_AGENT_ALLOW_DEV_SECRET=1 使用开发密钥。")
	return ""
}
