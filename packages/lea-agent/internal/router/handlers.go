package router

import (
	"database/sql"
	"errors"
	"log"

	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
)

type SendRequest struct {
	SessionID     string  `json:"session_id"`
	AgentID       int     `json:"agent_id"`
	Message       string  `json:"message"`
	AttachmentIDs []int64 `json:"attachment_ids"`
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

		if err := sess.Send(req.Message, req.AttachmentIDs); err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}

		c.JSON(202, gin.H{"session_id": sess.ID(), "status": "processing"})
	}
}

func handleStream(mgr *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		sessionID := c.Param("session_id")
		sess := mgr.Get(sessionID)
		if sess == nil {
			c.JSON(404, gin.H{"error": "session not found"})
			return
		}
		// 归属校验跟 handleStop 一样不能省 —— 这条以前漏了，于是拿到别人的
		// session_id 就能挂上他的事件流，实时读走对方的 delta 和工具输出。
		// 读侧（messages/files）都有这道，唯独 stream 没有。
		if !sess.OwnedBy(c.GetString("tenant_id"), c.GetString("user_id")) {
			c.JSON(403, gin.H{"error": "forbidden"})
			return
		}

		c.Writer.Header().Set("Content-Type", "text/event-stream")
		c.Writer.Header().Set("Cache-Control", "no-cache")
		c.Writer.Header().Set("Connection", "keep-alive")
		c.Writer.Flush()

		events := sess.Subscribe()
		defer sess.Unsubscribe(events)

		for {
			select {
			case ev, ok := <-events:
				if !ok {
					return
				}
				c.SSEvent(ev.Type, ev.Data)
				c.Writer.Flush()
			case <-c.Request.Context().Done():
				return
			}
		}
	}
}

// handleStop 掐断该会话正在跑的 turn。
//
// 只作用于进程内存里活着的 session —— 会话对象是 Send 的宿主，重启后本来就
// 没有 turn 在跑，所以 404 等价于"没什么可停"。stopped=false 表示这一轮已经
// 自己结束了，属于正常竞态（用户点停的同一瞬间 turn 收尾），返 200 不报错。
func handleStop(mgr *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		sessionID := c.Param("session_id")
		sess := mgr.Get(sessionID)
		if sess == nil {
			c.JSON(404, gin.H{"error": "session not found"})
			return
		}
		if !sess.OwnedBy(c.GetString("tenant_id"), c.GetString("user_id")) {
			c.JSON(403, gin.H{"error": "forbidden"})
			return
		}
		stopped := sess.Stop()
		c.JSON(200, gin.H{"session_id": sessionID, "stopped": stopped})
	}
}

func handleCreateSession(mgr *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req struct {
			AgentID int `json:"agent_id"`
		}
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(400, gin.H{"error": err.Error()})
			return
		}
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		sess, err := mgr.GetOrCreate(c.Request.Context(), session.Params{
			AgentID:  req.AgentID,
			TenantID: tenantID,
			UserID:   userID,
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
				"message_count":  r.MessageCount,
				"last_active_at": r.LastActiveAt,
				"created_at":     r.CreatedAt,
				"is_archived":    r.IsArchived,
			})
		}
		c.JSON(200, gin.H{"sessions": out})
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

		msgs, err := persistence.GetMessages(c.Request.Context(), db, sessionID)
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
