package router

import (
	"log"
	"os"

	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
)

func RegisterRoutes(api *gin.RouterGroup, mgr *session.Manager) {
	chat := api.Group("/chat")
	{
		chat.POST("/send", handleSend(mgr))
		chat.GET("/stream/:session_id", handleStream(mgr))
		chat.POST("/stop/:session_id", handleStop(mgr))
		chat.POST("/attachments/upload", handleUploadAttachment())
	}

	sess := api.Group("/sessions")
	{
		sess.POST("", handleCreateSession(mgr))
		sess.GET("", handleListSessions(mgr))
		sess.GET("/:id/messages", handleGetMessages(mgr))
		sess.DELETE("/:id", handleDeleteSession(mgr))
	}

	files := api.Group("/files")
	{
		files.GET("/list", handleListArtifacts())
		files.GET("/download", handleDownloadArtifact())
	}
}

// RegisterDebugRoutes 挂排查用端点。注意两件事：
//
//  1. 必须挂在**已过 JWT 的 group** 上（调用方传 api，不是 gin.Engine）。
//     之前挂在裸 engine 上，于是 /debug/trace 跟 /api/* 共用 8100 端口却
//     不过鉴权 —— nginx 只反代 /api/，所以公网碰不到，但任何能直连 8100
//     的东西都能调，包括同集群里被拿下的其他服务。
//  2. 默认不注册。它的响应里有记忆正文和完整 prompt，正常运行时不该存在。
//     排查时设 LEA_AGENT_DEBUG_TRACE=1 再重启。
func RegisterDebugRoutes(api *gin.RouterGroup) {
	if os.Getenv("LEA_AGENT_DEBUG_TRACE") != "1" {
		return
	}
	log.Printf("debug_trace_enabled — /api/debug/trace 已开启，仅供排查，勿在生产常开")
	api.POST("/debug/trace", handleDebugTrace)
}
