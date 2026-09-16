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
		// 取回用户上传的原件。走 attachment_id 而不是路径：原件的权威位置是 OBS 上的
		// blob，会话归档后 NFS 那份已经清理掉了、blob 还在。见 handleDownloadAttachment。
		chat.GET("/attachments/:id/download", handleDownloadAttachment())
	}

	sess := api.Group("/sessions")
	{
		api.GET("/projects", handleListProjects())
		api.POST("/projects", handleCreateProject())
		api.PUT("/projects/:id", handleUpdateProject())
		api.POST("/projects/:id/archive", handleArchiveProject())
		api.DELETE("/projects/:id", handleDeleteProject())
		sess.POST("", handleCreateSession(mgr))
		sess.GET("", handleListSessions(mgr))
		sess.GET("/:id/messages", handleGetMessages(mgr))
		sess.DELETE("/:id", handleDeleteSession(mgr))
		// 会话级挂载：在**这一个会话里**临时挂上自己的工具/知识库，不动 agent 的绑定。
		// 见 session_attachments.go 顶部。
		sess.GET("/:id/attachments", handleListSessionAttachments(mgr))
		sess.POST("/:id/attachments", handleAttachToSession(mgr))
		sess.DELETE("/:id/attachments/:kind/:ref_id", handleDetachFromSession(mgr))
		// 切换这个会话的"激活 agent"（专家/专家团）；agent_id=0 = 取消回默认。
		// 见 session_active_agent.go 顶部。
		sess.PUT("/:id/active-agent", handleSetActiveAgent(mgr))
		// 把用户文件库里的一个文件物化进这个会话，让模型能读能改。
		// 挂在会话下面（而不是 /user 下）是因为它改的是**这一个会话**的状态。
		// 见 user_files.go 的 handleImportToSession。
		sess.POST("/:id/files/import", handleImportToSession())
		sess.POST("/:id/files/publish", handlePublishWorkspaceFile())
	}

	files := api.Group("/files")
	{
		files.GET("/list", handleListArtifacts())
		files.GET("/download", handleDownloadArtifact())
	}

	// 用户的长期文件库：跨会话的那一层。上传件 + 模型交付的成品合并展示。
	// 见 user_files.go 顶部。
	user := api.Group("/user")
	{
		user.GET("/storage", handleUserStorage())
		user.GET("/files", handleListUserFiles())
		// kind ∈ {upload, artifact} —— 两张表，删除要知道去哪张。
		user.DELETE("/files/:kind/:id", handleDeleteUserFile())
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
