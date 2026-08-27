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
		// 主链路：发消息 + 同连接回 SSE。多副本下唯一不需要会话亲和的形状。
		chat.POST("/stream", handleSendStream(mgr))
		// 断线重连入口。POST /stream 是单连接，断了没法重接（重发 POST 会真的再问
		// 一遍），所以重连必须走这条：带 ?from=<最后收到的 id> 从事件日志补齐再接直播。
		// 配了 REDIS_URL 时它不需要会话亲和；没配时退回内存订阅（单实例的正确行为）。
		chat.GET("/stream/:session_id", handleStreamResume(mgr))
		// 两段式链路的发送端，保留给尚未迁移的客户端。
		chat.POST("/send", handleSend(mgr))
		chat.POST("/stop/:session_id", handleStop(mgr))
		// 断流之后前端靠它判断"还在生成"还是"已经结束"。跨副本有效。
		chat.GET("/status/:session_id", handleTurnStatus(mgr))
		chat.POST("/attachments/upload", handleUploadAttachment())
	}

	sess := api.Group("/sessions")
	{
		sess.POST("", handleCreateSession(mgr))
		sess.GET("", handleListSessions(mgr))
		sess.GET("/:id/messages", handleGetMessages(mgr))
		sess.DELETE("/:id", handleDeleteSession(mgr))
		// 会话级挂载：在**这一个会话里**临时挂上自己的工具/知识库，不动 agent 的绑定。
		// 见 session_attachments.go 顶部。
		sess.GET("/:id/attachments", handleListSessionAttachments(mgr))
		sess.POST("/:id/attachments", handleAttachToSession(mgr))
		sess.DELETE("/:id/attachments/:kind/:ref_id", handleDetachFromSession(mgr))
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
