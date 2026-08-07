package main

import (
	"log"
	"os"

	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/middleware"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/router"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
)

func main() {
	port := os.Getenv("LEA_AGENT_PORT")
	if port == "" {
		port = "8100"
	}

	mgr := session.NewManager()
	defer mgr.Close()

	r := gin.Default()
	r.Use(middleware.CORS())

	api := r.Group("/api")
	api.Use(middleware.JWTAuth())

	// /api/user/agents/* → lea-config:8500/agents/* （用户视角 Agent CRUD）
	api.Any("/user/agents", router.UserAgentsProxy())
	api.Any("/user/agents/*path", router.UserAgentsProxy())

	router.RegisterRoutes(api, mgr)
	router.RegisterDebugRoutes(api)

	// 带上驻留会话数：CCE 的探针和监控靠它看内存压力，也能确认淘汰真的在工作。
	r.GET("/health", func(c *gin.Context) {
		c.JSON(200, gin.H{
			"status":   "ok",
			"service":  "lea-agent",
			"sessions": mgr.Count(),
		})
	})

	log.Printf("lea-agent starting on :%s", port)
	if err := r.Run(":" + port); err != nil {
		log.Fatal(err)
	}
}
