package router

import (
	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/runtime"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
)

// 会话的"激活 agent"——专家/专家团的第 1 层（切底座）。
//
// ## 跟会话级挂载（session_attachments.go）的分工
//
//	active_agent_id   第 1 层：这个会话此刻以哪个 agent 在跑（人格 + 绑定 + 可能的团拓扑）。
//	                  切专家 = 换它；取消 = 置 0 回默认 super-agent。天然单值。
//	session_attachments 第 2 层：在当前专家之上再临时加的 tool/skill/kb/mcp_server。可多个。
//
// 两层正交，各走各的表。合流点在 session.Manager.GetOrCreate：它先按 active_agent_id 选底座
// LoadAgentConfig，再把挂载并进 cfg。
//
// ## 生效时机 —— 复用挂载那条重建链
//
// 引擎的系统提示词和工具集在 engine.Build() 那一刻冻死，切 agent 只能靠重建生效。这套
// （Drop / 生成中置 MarkPendingRebuild / 下一条消息重建 / 多副本指纹自愈）挂载已经全写好，
// 这里直接复用 applyAndReport。active_agent_id 折进了同一个重建指纹（rebuildFingerprint），
// 所以"在副本 A 上切、到副本 B 上发消息"跟挂载一样自愈。
type activeAgentBody struct {
	AgentID int `json:"agent_id"`
}

// handleSetActiveAgent 切换会话的激活 agent。
func handleSetActiveAgent(mgr *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		var body activeAgentBody
		if err := c.ShouldBindJSON(&body); err != nil {
			c.JSON(400, gin.H{"error": "invalid body"})
			return
		}
		if body.AgentID < 0 {
			c.JSON(400, gin.H{"error": "agent_id must be >= 0"})
			return
		}

		db, ok := sessionOwnerGuard(c)
		if !ok {
			return
		}

		// 写列之前先探一次目标 agent 能不能加载。理由：底座 agent **不能** fail-soft ——
		// 一旦写进列，下一条消息重建时 LoadAgentConfig 失败会让会话回退默认（manager.go 的
		// 失效兜底），用户以为切成功了、实际还在默认。先探一次，加载不出来就拒绝、不写列，
		// 把错误即时还给用户。agent_id=0（取消回默认）跳过——默认 super-agent 一定在。
		//
		// ⚠️ 只判 err 不够：lea-config 对不存在的 agent_id **静默兜底到租户 super-agent**
		// （internal.py），于是 LoadAgentConfig(不存在的 id) 不报错、返回的是 super-agent
		// 的配置。所以还要比 cfg.ID：兜底回来的 id 跟请求的对不上，就是"这个专家不存在"，
		// 照样拒绝——否则用户挂一个打错的 id 会静默变成默认，还以为挂上了。
		if body.AgentID != 0 {
			cfg, err := runtime.LoadAgentConfig(
				c.Request.Context(), body.AgentID, c.GetString("tenant_id"))
			if err != nil {
				c.JSON(400, gin.H{"error": "agent not available: " + err.Error()})
				return
			}
			if cfg.ID != body.AgentID {
				c.JSON(404, gin.H{"error": "agent not found"})
				return
			}
		}

		sessionID := c.Param("id")
		changed, err := persistence.SetSessionActiveAgent(
			c.Request.Context(), db,
			sessionID, c.GetString("tenant_id"), c.GetString("user_id"), body.AgentID)
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		if !changed {
			// guard 已确认归属；这里还没命中行，只可能是会话尚未落库（首条消息之前）。
			// "召唤"应走"新建会话 + 首条消息带 agent_id"那条路，不靠这个端点。
			c.JSON(404, gin.H{"error": "session not found"})
			return
		}

		applyAndReport(c, mgr, sessionID, gin.H{
			"session_id": sessionID,
			"agent_id":   body.AgentID,
			"active":     body.AgentID != 0,
		})
	}
}
