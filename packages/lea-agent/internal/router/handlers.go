package router

import (
	"database/sql"
	"errors"
	"io"
	"log"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/redis/go-redis/v9"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/cache"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
)

// 全局消息缓存实例
var globalMessageCache *persistence.MessageCache

// InitMessageCache 初始化全局消息缓存
func InitMessageCache(cacheLayer *cache.Cache, db *sql.DB, redisClient *redis.Client) {
	globalMessageCache = persistence.NewMessageCache(cacheLayer, db, redisClient)
}

type SendRequest struct {
	SessionID     string  `json:"session_id"`
	AgentID       int     `json:"agent_id"`
	ProjectID     *int    `json:"project_id,omitempty"`
	Message       string  `json:"message"`
	AttachmentIDs []int64 `json:"attachment_ids"`
	// OnDisconnect 决定客户端在 turn 跑完之前消失（关标签页、断网、LB 空闲超时）
	// 时怎么处理："continue"（默认，让它跑完并落库）或 "cancel"（立刻停）。
	// 见 router.onClientGone。
	OnDisconnect string `json:"on_disconnect"`
	// PlanMode 为 true 时进入计划模式：prompt 引导模型只做调研和计划。为 false
	// （含零值）时进入执行阶段，若有已批准的计划则每轮注入到 message 前缀。
	// 不填等价于 false，旧客户端无需改动。
	PlanMode bool `json:"plan_mode"`
	// PlanAction 是用户对**已提交的那份计划**的表态："approve" / "reject"，
	// 或空串（没表态，绝大多数消息都是空）。
	//
	// 它跟 PlanMode 是两个维度，不能互相推断 —— 这个字段存在的全部理由就是：
	// 老实现拿 plan_mode=false 当批准信号，于是用户回一句"算了不用了"也会激活
	// 那份计划。批准必须是用户显式做的动作，前端从 ask_user 的选项按钮发出来。
	PlanAction string `json:"plan_action"`
}

// planControl 把请求里那两个字段收成 session 层的入参。
func (r SendRequest) planControl() session.PlanControl {
	return session.PlanControl{Mode: r.PlanMode, Action: r.PlanAction}
}

func handleSend(mgr *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req SendRequest
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(400, gin.H{"error": err.Error()})
			return
		}

		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		sess, err := mgr.GetOrCreate(c.Request.Context(), session.Params{
			SessionID: req.SessionID,
			AgentID:   req.AgentID,
			TenantID:  tenantID,
			UserID:    userID,
		})
		if err != nil {
			if errors.Is(err, session.ErrForbidden) {
				c.JSON(403, gin.H{"error": "forbidden"})
				return
			}
			// 见 stream.go 同处：验不了 ≠ 无权，必须是 503。
			if errors.Is(err, session.ErrUnverifiable) {
				c.Header("Retry-After", "5")
				c.JSON(503, gin.H{"error": "cannot verify session ownership, retry shortly"})
				return
			}
			// 会话数打满且没有可回收的空闲会话 —— 让客户端稍后重试，
			// 别去挤掉别人正在用的会话。
			if errors.Is(err, session.ErrCapacity) {
				c.Header("Retry-After", "10")
				c.JSON(503, gin.H{"error": "server busy, retry shortly"})
				return
			}
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}

		if err := sess.Send(req.Message, req.AttachmentIDs, req.planControl()); err != nil {
			// plan_mode 被拒（handoff 团、或 agent 的 plan_mode=off）。400 而不是 500：
			// 请求本身不成立，重试不会变好，客户端该做的是别发 plan_mode。
			if errors.Is(err, session.ErrPlanModeUnsupported) {
				c.JSON(400, gin.H{"error": err.Error()})
				return
			}
			// 这个会话已经有一轮在别的 pod 上跑着。409 而不是 500 —— 这是可预期的
			// 并发，客户端该做的是等当前这轮结束（它的 SSE 还在流），而不是立刻重试。
			if errors.Is(err, session.ErrBusy) {
				c.JSON(409, gin.H{"error": "a turn is already running for this session"})
				return
			}
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}

		c.JSON(202, gin.H{"session_id": sess.ID(), "status": "processing"})
	}
}

// handleStop 掐断该会话正在跑的 turn。
//
// 两条路：会话在本 pod 内存里就直接停；不在就转给持有执行权的那个 pod。
//
// 为什么需要第二条：这是一条**独立的 HTTP 请求**，多副本下会落到任意副本，而
// cancel 函数只存在于跑这一轮的那个进程里。没有转发的话"停止"是抛硬币 —— 命中
// 就停，没命中返 404 而回答继续生成。而且这不是配置粘性路由能解决的：session_id
// 在本请求的 URL 里、在 POST /chat/stream 的 body 里，nginx 没法让两者哈希到
// 同一个后端（它读不了请求体）。
//
// stopped=false 表示这一轮已经自己结束了，属于正常竞态（用户点停的同一瞬间 turn
// 收尾），返 200 不报错。
func handleStop(mgr *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		sessionID := c.Param("session_id")
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		if sess := mgr.Get(sessionID); sess != nil {
			if !sess.OwnedBy(tenantID, userID) {
				c.JSON(403, gin.H{"error": "forbidden"})
				return
			}
			c.JSON(200, gin.H{"session_id": sessionID, "stopped": sess.Stop()})
			return
		}

		// 本 pod 没有这个会话。它可能在别的副本上跑着，也可能压根没在跑。
		if !authorizeAbsentSession(c, sessionID, tenantID, userID) {
			return
		}

		forwarded, err := mgr.ForwardStop(c.Request.Context(), sessionID)
		if err != nil {
			// 控制面不通（没配 Redis，或连不上）。返 503 而不是 404 —— 404 会让
			// 前端以为"没什么在跑"，而真相是"我们不知道，也送不出去信号"。
			log.Printf("stop_forward_failed session=%s err=%v", sessionID, err)
			c.JSON(503, gin.H{"error": "cannot reach the runner for this session"})
			return
		}
		if !forwarded {
			// 全集群都没有在跑的 turn（锁不存在）。这个 404 是有依据的，不是猜的。
			c.JSON(404, gin.H{"error": "session not found"})
			return
		}
		// stopped=true 只表示信号已经发出去了。权威的完成信号是 SSE 流上的
		// result + done —— 那条流就挂在跑这一轮的 pod 上，所以用户一定收得到。
		c.JSON(200, gin.H{"session_id": sessionID, "stopped": true, "forwarded": true})
	}
}

// authorizeAbsentSession 校验一个**不在本进程内存里**的会话是否归调用方所有。
// 不通过时它自己写好响应并返回 false，调用方直接 return 即可。
//
// 为什么必须单独有这么一道：AgentSession.OwnedBy 要内存里的会话对象，而多副本下
// 控制类请求（stop、status）经常落在没有这个会话的副本上。少了这道就等于：任何
// 登录用户拿一个 session_id 打到"不持有该会话"的副本，就能掐断别人正在生成的回答、
// 或者探到别人的会话状态。本 pod 那条路有校验、跨 pod 那条没有 —— 这是最容易漏的
// 一类权限缺口，因为功能测试全都会过。
func authorizeAbsentSession(c *gin.Context, sessionID, tenantID, userID string) bool {
	db, err := persistence.DB()
	if err != nil || db == nil {
		// 校验不了就不放行。不能"反正是内网先过去吧"—— 那正是缺口本身。
		c.JSON(503, gin.H{"error": "persistence unavailable"})
		return false
	}
	ownerUser, ownerTenant, err := persistence.GetSessionOwner(c.Request.Context(), db, sessionID)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			c.JSON(404, gin.H{"error": "session not found"})
			return false
		}
		c.JSON(500, gin.H{"error": err.Error()})
		return false
	}
	if ownerUser != userID || ownerTenant != tenantID {
		c.JSON(403, gin.H{"error": "forbidden"})
		return false
	}
	return true
}

// handleTurnStatus 回答"这个会话此刻还在生成吗"，覆盖全集群。
//
// 前端断流之后需要它来决定下一步：running=true 就接着等（或重连流），false 就去
// 拉 /sessions/:id/messages 取最终结果。没有它的话这两种情况在 messages 接口上
// 长得一模一样 —— assistant 消息要等整轮结束才落库，所以"还在写"和"写完了但我
// 没收到"都表现为"列表里没有新消息"。
//
// 这也是 OpenAI 的形状：GET /v1/responses/{id} 返回 status: in_progress / completed。
func handleTurnStatus(mgr *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		sessionID := c.Param("session_id")
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		// 本 pod 有会话对象就用它校验；没有就查库（多副本下这是常态）。
		if sess := mgr.Get(sessionID); sess != nil {
			if !sess.OwnedBy(tenantID, userID) {
				c.JSON(403, gin.H{"error": "forbidden"})
				return
			}
		} else if !authorizeAbsentSession(c, sessionID, tenantID, userID) {
			return
		}

		running, err := mgr.IsTurnRunning(c.Request.Context(), sessionID)
		if err != nil {
			// 查不到就如实说查不到。猜 false 会让前端过早放弃、丢掉正在生成的答案；
			// 猜 true 会让它对着一个不存在的 turn 死等。
			log.Printf("turn_status_failed session=%s err=%v", sessionID, err)
			c.JSON(503, gin.H{"error": "cannot determine turn status"})
			return
		}
		c.JSON(200, gin.H{"session_id": sessionID, "running": running})
	}
}

// handleCreateSession 建会话。
//
// ## agent_id 是可选的
//
// 不传 = 用租户的 super-agent（lea-config 的 _resolve_super_agent 在 agentID=0 时兜底）。
// 这条路是"用户进来直接开聊"的前提：先有一个能对话的会话，再靠 @ 往里挂 skill/tool/
// mcp_server/kb —— 而不是逼用户先选一个 agent 才看得到输入框。
//
// 指针而不是 int：0 和"没传"必须分得开。将来若要让 0 表示别的意思（比如显式拒绝兜底），
// 有了这个区分才改得动；用零值的话那个语义位已经被占掉了。
//
// 空 body 也接受 —— POST 一个没有 body 的请求是这条路最自然的调用形状，而
// ShouldBindJSON 对空 body 返回 EOF。不放过它的话"直接开聊"仍然要求前端记得发 `{}`。
func handleCreateSession(mgr *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req struct {
			AgentID   *int `json:"agent_id,omitempty"`
			ProjectID *int `json:"project_id,omitempty"`
		}
		// EOF = 没有 body，等价于 {}。其余解析错误仍然是 400：那是**格式坏了**，
		// 静默当成缺省会把一个拼错的字段名变成"悄悄用了 super-agent"。
		if err := c.ShouldBindJSON(&req); err != nil && !errors.Is(err, io.EOF) {
			c.JSON(400, gin.H{"error": err.Error()})
			return
		}
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		agentID := 0
		if req.AgentID != nil {
			agentID = *req.AgentID
		}
		if req.ProjectID != nil {
			db, dbErr := persistence.DB()
			if dbErr != nil || db == nil {
				c.JSON(503, gin.H{"error": "persistence unavailable"})
				return
			}
			owned, err := persistence.ProjectOwnedBy(c.Request.Context(), db, *req.ProjectID, tenantID, userID)
			if err != nil {
				c.JSON(500, gin.H{"error": err.Error()})
				return
			}
			if !owned {
				c.JSON(404, gin.H{"error": "project not found"})
				return
			}
		}

		sess, err := mgr.GetOrCreate(c.Request.Context(), session.Params{
			AgentID:   agentID,
			TenantID:  tenantID,
			UserID:    userID,
			ProjectID: req.ProjectID,
		})
		if err != nil {
			// 这条不传 SessionID，走不到复用分支，理论上碰不到 ErrForbidden；
			// 仍然映射一下，免得将来改了 GetOrCreate 这里静默变成 500。
			if errors.Is(err, session.ErrForbidden) {
				c.JSON(403, gin.H{"error": "forbidden"})
				return
			}
			if errors.Is(err, session.ErrCapacity) {
				c.Header("Retry-After", "10")
				c.JSON(503, gin.H{"error": "server busy, retry shortly"})
				return
			}
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		c.JSON(201, gin.H{"session_id": sess.ID()})
	}
}

// handleListSessions 读 DB 返回归属该 tenant + user 的未归档会话。
// 老的 mgr.ListByUser 只看进程内存，重启就丢，H5 改成读库为准。
func handleListSessions(_ *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(200, gin.H{"sessions": []any{}})
			return
		}
		rows, err := persistence.ListSessions(c.Request.Context(), db, tenantID, userID, 50, 0)
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		out := make([]gin.H, 0, len(rows))
		for _, r := range rows {
			out = append(out, gin.H{
				"id":             r.ID,
				"title":          r.Title,
				"agent_id":       r.AgentID,
				"project_id":     r.ProjectID,
				"message_count":  r.MessageCount,
				"last_active_at": r.LastActiveAt,
				"created_at":     r.CreatedAt,
				"is_archived":    r.IsArchived,
			})
		}
		c.JSON(200, gin.H{"sessions": out})
	}
}

func handleListProjects() gin.HandlerFunc {
	return func(c *gin.Context) {
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		items, err := persistence.ListProjects(c.Request.Context(), db, c.GetString("tenant_id"), c.GetString("user_id"))
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		c.JSON(200, gin.H{"projects": items})
	}
}

func handleCreateProject() gin.HandlerFunc {
	return func(c *gin.Context) {
		var req struct {
			Name        string `json:"name" binding:"required"`
			Description string `json:"description"`
		}
		if err := c.ShouldBindJSON(&req); err != nil || strings.TrimSpace(req.Name) == "" {
			c.JSON(400, gin.H{"error": "project name required"})
			return
		}
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		project, err := persistence.CreateProject(c.Request.Context(), db, c.GetString("tenant_id"), c.GetString("user_id"), strings.TrimSpace(req.Name), strings.TrimSpace(req.Description))
		if err != nil {
			c.JSON(409, gin.H{"error": "project name already exists"})
			return
		}
		c.JSON(201, project)
	}
}

func handleUpdateProject() gin.HandlerFunc {
	return func(c *gin.Context) {
		id, err := strconv.Atoi(c.Param("id"))
		if err != nil {
			c.JSON(400, gin.H{"error": "invalid project id"})
			return
		}
		var req struct {
			Name        string `json:"name" binding:"required"`
			Description string `json:"description"`
		}
		if err := c.ShouldBindJSON(&req); err != nil || strings.TrimSpace(req.Name) == "" {
			c.JSON(400, gin.H{"error": "project name required"})
			return
		}
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		project, err := persistence.UpdateProject(c.Request.Context(), db, id, c.GetString("tenant_id"), c.GetString("user_id"), strings.TrimSpace(req.Name), strings.TrimSpace(req.Description))
		if errors.Is(err, sql.ErrNoRows) {
			c.JSON(404, gin.H{"error": "project not found"})
			return
		}
		if err != nil {
			c.JSON(409, gin.H{"error": "project name already exists"})
			return
		}
		c.JSON(200, project)
	}
}

func handleArchiveProject() gin.HandlerFunc {
	return func(c *gin.Context) {
		id, err := strconv.Atoi(c.Param("id"))
		if err != nil {
			c.JSON(400, gin.H{"error": "invalid project id"})
			return
		}
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		err = persistence.ArchiveProject(c.Request.Context(), db, id, c.GetString("tenant_id"), c.GetString("user_id"))
		if errors.Is(err, sql.ErrNoRows) {
			c.JSON(404, gin.H{"error": "project not found"})
			return
		}
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		c.JSON(200, gin.H{"id": id, "is_archived": true})
	}
}

func handleDeleteProject() gin.HandlerFunc {
	return func(c *gin.Context) {
		id, err := strconv.Atoi(c.Param("id"))
		if err != nil {
			c.JSON(400, gin.H{"error": "invalid project id"})
			return
		}
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		err = persistence.DeleteProject(c.Request.Context(), db, id, c.GetString("tenant_id"), c.GetString("user_id"))
		if errors.Is(err, sql.ErrNoRows) {
			c.JSON(404, gin.H{"error": "project not found"})
			return
		}
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		c.JSON(200, gin.H{"ok": true})
	}
}

// handleGetMessages 拉某会话的历史消息，先校验归属再读。
func handleGetMessages(_ *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		sessionID := c.Param("id")
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}

		ownerUser, ownerTenant, err := persistence.GetSessionOwner(c.Request.Context(), db, sessionID)
		if err != nil {
			if errors.Is(err, sql.ErrNoRows) {
				c.JSON(404, gin.H{"error": "session not found"})
				return
			}
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		if ownerUser != userID || ownerTenant != tenantID {
			c.JSON(403, gin.H{"error": "forbidden"})
			return
		}

		// 使用消息缓存（如果可用）
		var msgs []persistence.MessageRow
		if globalMessageCache != nil {
			msgs, err = globalMessageCache.GetSessionMessages(c.Request.Context(), sessionID)
		} else {
			msgs, err = persistence.GetMessages(c.Request.Context(), db, sessionID)
		}
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		out := make([]gin.H, 0, len(msgs))
		for _, m := range msgs {
			row := gin.H{
				"id":         m.ID,
				"role":       m.Role,
				"content":    m.Content,
				"created_at": m.CreatedAt,
			}
			if len(m.Extra) > 0 {
				row["extra"] = m.Extra
			}
			out = append(out, row)
		}
		c.JSON(200, gin.H{"session_id": sessionID, "messages": out})
	}
}

// handleDeleteSession 走软删（ArchiveSession）。找不到或不属于当前用户 → 404。
func handleDeleteSession(mgr *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		id := c.Param("id")
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		db, err := persistence.DB()
		if err == nil && db != nil {
			if err := persistence.ArchiveSession(c.Request.Context(), db, id, userID, tenantID); err != nil {
				if errors.Is(err, sql.ErrNoRows) {
					c.JSON(404, gin.H{"error": "session not found"})
					return
				}
				log.Printf("archive_session_failed session=%s err=%v", id, err)
				c.JSON(500, gin.H{"error": err.Error()})
				return
			}
		}
		// 内存里的 session 也顺手清一下（若有）
		mgr.Delete(id)
		c.JSON(200, gin.H{"ok": true})
	}
}
