package router

import (
	"database/sql"
	"errors"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
)

// 会话级挂载的三个端点 —— 用户在**这一个会话里**临时挂上自己的工具/知识库。
//
// ## 解决的问题
//
// 用户建了自己的 api 工具，今天唯一的用法是绑到某个 agent 上。而绑定是 agent 定义的
// 一部分、所有使用者共享：想在别人做的共享 agent 里用自己的工具就做不到，硬绑上去又会
// 把名字/描述/schema 泄给所有人（共享发布门拦的就是这个）。结果是建了工具没地方用。
//
// 挂载走的是另一条路：**只影响这一个会话**。在别人共享的 agent 上挂自己的工具是安全的，
// 那个 agent 一行都没动，作者和其他使用者看到的还是原样。
//
// ## 生效时机
//
// 引擎的工具数组在 engine.Build() 那一刻就冻住了（agentloop 里 Toolset.Tools() 全仓
// 只调一次），所以挂载靠**丢掉内存里那份引擎对象**生效：下一条消息重建时，新工具在
// 初始集合里。对话不受影响 —— 历史在 agent_messages，重建会回放。
//
// 正在生成时不能丢（对象正被占用），也**不该打断那一轮**：用户挂工具是想*增补*，还等着
// 这轮结果。所以置个标志，本轮收尾自己丢。对调用方来说挂载**永远成功**，只是 applies
// 字段告诉它什么时候生效，不需要处理任何冲突错误。
//
// （对比：用户在生成中发新消息走 acquireTurnLock，那里直接取消上一轮 —— 那是*改主意*，
// 旧产出已作废。同一个"生成中的用户操作"，两种语义，两种处理。）

type attachBody struct {
	Kind  string `json:"kind"`
	RefID int    `json:"ref_id"`
}

// sessionOwnerGuard 校验这个会话归当前调用方所有，并返回可用的 db。
//
// 三个挂载端点都要这一段，抽出来是因为它是**安全边界**：漏掉任何一个，就能靠猜
// session_id 去改别人会话的工具集。跟 handleGetMessages 里那段是同一条判据。
func sessionOwnerGuard(c *gin.Context) (*sql.DB, bool) {
	db, err := persistence.DB()
	if err != nil || db == nil {
		c.JSON(503, gin.H{"error": "persistence unavailable"})
		return nil, false
	}
	ownerUser, ownerTenant, err := persistence.GetSessionOwner(
		c.Request.Context(), db, c.Param("id"))
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			c.JSON(404, gin.H{"error": "session not found"})
			return nil, false
		}
		c.JSON(500, gin.H{"error": err.Error()})
		return nil, false
	}
	// 404 而不是 403：跟 bind_tool 那边一致 —— 说"没有这个会话"才不泄露它存在。
	if ownerUser != c.GetString("user_id") || ownerTenant != c.GetString("tenant_id") {
		c.JSON(404, gin.H{"error": "session not found"})
		return nil, false
	}
	return db, true
}

// applyAndReport 让挂载变更生效，并告诉调用方什么时候生效。
func applyAndReport(c *gin.Context, mgr *session.Manager, sessionID string, payload gin.H) {
	if mgr.Drop(sessionID) {
		payload["applies"] = "next_message"
	} else {
		// 正在生成或有 SSE 订阅者 —— 记标志，本轮收尾自己丢。
		if s := mgr.Get(sessionID); s != nil {
			s.MarkPendingRebuild()
		}
		payload["applies"] = "after_current_turn"
	}
	c.JSON(200, payload)
}

// handleListSessionAttachments 列出这个会话挂了什么。
//
// 返回的是**库里的原始记录**，不做归属解析 —— 界面要显示"你挂了这几样"，包括其中某样
// 已经失效的情况。解析在建会话时做（那时失效项会被静默丢弃），两边目的不同：这里要
// 忠实反映用户挂过什么，那里要保证能用。
func handleListSessionAttachments(_ *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		db, ok := sessionOwnerGuard(c)
		if !ok {
			return
		}
		rows, err := persistence.LoadSessionAttachments(
			c.Request.Context(), db, c.GetString("tenant_id"), c.Param("id"))
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		out := make([]gin.H, 0, len(rows))
		for _, r := range rows {
			out = append(out, gin.H{"kind": r.Kind, "ref_id": r.RefID})
		}
		c.JSON(200, gin.H{"session_id": c.Param("id"), "attachments": out})
	}
}

// handleAttachToSession 挂上一个资源。
//
// **不在这里校验资源归属。** 那件事在建会话时由 lea-config 的 /internal/resolve-attachments
// 做，而且必须在那里做：挂载记录会存很久，资源的可见性随时会变（工具被删、被取消共享、
// 人被移出租户），所以每次用之前都要重新校验一遍。在这里校验一次是**不够**的，那会给人
// "已经验过了"的错觉。
//
// 代价是用户可以挂一个自己够不着的 id，然后发现它不生效。可以接受：那不是越权，因为
// 解析那一跳会丢掉它；而多一次校验换来的只是一条早一点的错误提示。
func handleAttachToSession(mgr *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		var body attachBody
		if err := c.ShouldBindJSON(&body); err != nil {
			c.JSON(400, gin.H{"error": "invalid body"})
			return
		}
		if body.RefID <= 0 {
			c.JSON(400, gin.H{"error": "ref_id required"})
			return
		}
		if !persistence.SessionAttachmentKinds[body.Kind] {
			// 这里**要**拦未知 kind，跟解析那边的"静默丢弃"不矛盾：写入是新数据，拦住
			// 才不会攒下一堆谁也不认的行；读取是既有数据，宽容才不会让老会话打不开。
			//
			// 列表从那个 map 生成，不手写。手写的那版在加 skill 时就过时了 —— 提示里
			// 只有 tool/kb，而实际已经收 skill，用户照着提示做反而是错的。
			c.JSON(400, gin.H{
				"error": "kind must be one of: " + strings.Join(persistence.KnownAttachmentKinds(), ", "),
			})
			return
		}

		db, ok := sessionOwnerGuard(c)
		if !ok {
			return
		}
		sessionID := c.Param("id")
		if err := persistence.AddSessionAttachment(
			c.Request.Context(), db,
			c.GetString("tenant_id"), sessionID, c.GetString("user_id"),
			body.Kind, body.RefID,
		); err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		applyAndReport(c, mgr, sessionID, gin.H{
			"session_id": sessionID, "kind": body.Kind, "ref_id": body.RefID,
			"attached": true,
		})
	}
}

// handleDetachFromSession 摘掉一个。摘不存在的也返 200 —— 结果状态跟用户想要的一致。
func handleDetachFromSession(mgr *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		kind := c.Param("kind")
		refID, err := strconv.Atoi(c.Param("ref_id"))
		if err != nil || refID <= 0 {
			c.JSON(400, gin.H{"error": "invalid ref_id"})
			return
		}

		db, ok := sessionOwnerGuard(c)
		if !ok {
			return
		}
		sessionID := c.Param("id")
		removed, rmErr := persistence.RemoveSessionAttachment(
			c.Request.Context(), db, c.GetString("tenant_id"), sessionID, kind, refID)
		if rmErr != nil {
			c.JSON(500, gin.H{"error": rmErr.Error()})
			return
		}
		if !removed {
			// 本来就没挂 —— 不重建（省一次没必要的重建），直接说"已经不在了"。
			c.JSON(200, gin.H{
				"session_id": sessionID, "kind": kind, "ref_id": refID,
				"attached": false, "applies": "already_absent",
			})
			return
		}
		applyAndReport(c, mgr, sessionID, gin.H{
			"session_id": sessionID, "kind": kind, "ref_id": refID,
			"attached": false,
		})
	}
}
