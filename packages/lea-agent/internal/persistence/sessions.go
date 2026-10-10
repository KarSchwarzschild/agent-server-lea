package persistence

import (
	"context"
	"database/sql"
	"strings"
	"time"
	"unicode/utf8"
)

// SessionRow 前端会话列表用的字段。
// agent_id 允许为空（旧 super-agent 没绑），用指针区分 NULL 和 0。
type SessionRow struct {
	ID           string
	Title        string
	AgentID      *int
	ProjectID    *int
	MessageCount int
	LastActiveAt time.Time
	CreatedAt    time.Time
	IsArchived   bool
}

// EnsureSession creates the ownership row without counting a message. Queued execution needs the
// row before a worker on another pod can rebuild the session and verify its owner.
func EnsureSession(ctx context.Context, db *sql.DB, sessionID, userID, tenantID string, projectID *int, activeAgentID int, firstMessage string) error {
	if db == nil {
		return nil
	}
	_, err := db.ExecContext(ctx,
		`INSERT INTO chat_sessions
		    (id, user_id, tenant_id, project_id, active_agent_id, title, app_name, message_count, is_archived)
		 VALUES (?, ?, ?, ?, ?, ?, 'chat', 0, 0)
		 ON DUPLICATE KEY UPDATE id = id`,
		sessionID, userID, tenantID, projectID, activeAgentID, deriveTitle(firstMessage),
	)
	return err
}

// TouchSession 首次建行 / 后续 message_count++。
// created_at / last_active_at 交给 MySQL 的 DEFAULT / ON UPDATE。
//
// tenantID 目前 chat_sessions 表里对应列名是 tenant_id（TenantMixin）；
// 若列不存在会 fallback（见 exec 的 error 分支）。
//
// activeAgentID 只在**首次建行**时写进 active_agent_id —— 它是这个会话的"激活 agent"
// 初值（"召唤"时首条消息带的那个 agent，或 0=默认）。之后切专家走
// SetSessionActiveAgent，UPDATE 分支不碰它，免得每轮把用户中途切的专家又覆盖回请求带的值。
func TouchSession(ctx context.Context, db *sql.DB, sessionID, userID, tenantID string, projectID *int, activeAgentID int, firstMessage string) error {
	if db == nil {
		return nil
	}

	// 先尝试 UPDATE
	res, err := db.ExecContext(ctx,
		`UPDATE chat_sessions
		    SET message_count = COALESCE(message_count, 0) + 1,
		        last_active_at = NOW()
		  WHERE id = ?`,
		sessionID,
	)
	if err != nil {
		return err
	}
	n, _ := res.RowsAffected()
	if n > 0 {
		return nil
	}

	// 不存在 → INSERT。message_count 从 1 起（本轮）。
	title := deriveTitle(firstMessage)
	_, err = db.ExecContext(ctx,
		`INSERT INTO chat_sessions
		    (id, user_id, tenant_id, project_id, active_agent_id, title, app_name, message_count, is_archived)
		 VALUES (?, ?, ?, ?, ?, ?, 'chat', 1, 0)`,
		sessionID, userID, tenantID, projectID, activeAgentID, title,
	)
	return err
}

// GetSessionActiveAgent 读这个会话的"激活 agent"。0 = 默认 super-agent。
//
// 行不存在（新会话首条消息还没落库）/ db 为 nil / 查询出错，一律返回 0 —— 都退回默认，
// 让调用方去用请求带的 p.AgentID（GetOrCreate 的 effectiveAgent 逻辑）。不把出错单独往上抛：
// 这一步在每条消息的热路径上，拿不到就当默认，跟 attachmentsChanged 查库失败当"没变"是同一种
// 保守姿态。
func GetSessionActiveAgent(ctx context.Context, db *sql.DB, sessionID string) int {
	if db == nil || sessionID == "" {
		return 0
	}
	var agentID int
	err := db.QueryRowContext(ctx,
		`SELECT active_agent_id FROM chat_sessions WHERE id = ?`,
		sessionID,
	).Scan(&agentID)
	if err != nil {
		return 0
	}
	return agentID
}

// SetSessionActiveAgent 切换这个会话的激活 agent。agentID=0 表示回到默认 super-agent。
//
// WHERE 带 tenant + user 过滤（跟 ArchiveSession 同形）防越权：路由层的 sessionOwnerGuard
// 已经验过一次归属，这里再夹一道，免得日后有别的调用方绕过 guard 直接调。
// 返回是否命中行——没命中（会话还没落库，或不属于该用户）让 handler 返 404。
func SetSessionActiveAgent(ctx context.Context, db *sql.DB, sessionID, tenantID, userID string, agentID int) (bool, error) {
	if db == nil {
		return false, nil
	}
	res, err := db.ExecContext(ctx,
		`UPDATE chat_sessions
		    SET active_agent_id = ?
		  WHERE id = ? AND tenant_id = ? AND user_id = ?`,
		agentID, sessionID, tenantID, userID,
	)
	if err != nil {
		return false, err
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

// ListSessions 按 tenant + user 返回未归档会话，最近活跃在前。
func ListSessions(ctx context.Context, db *sql.DB, tenantID, userID string, limit, offset int) ([]SessionRow, error) {
	if db == nil {
		return nil, nil
	}
	if limit <= 0 {
		limit = 50
	}
	// active_agent_id 是会话当前的"激活 agent"（专家/专家团），迁移 023 加的列。
	// 会话列表/聊天头靠它显示"现在挂的是哪个专家"。0=默认 super-agent，用 NULLIF 归一成
	// NULL，让前端拿到的 agent_id 语义跟以前一致（NULL = 没挂特定 agent）。
	rows, err := db.QueryContext(ctx,
		`SELECT id, project_id, COALESCE(title, ''), NULLIF(COALESCE(active_agent_id, 0), 0) AS agent_id, COALESCE(message_count, 0),
		        COALESCE(last_active_at, created_at), created_at, COALESCE(is_archived, 0)
		   FROM chat_sessions
		  WHERE tenant_id = ? AND user_id = ? AND COALESCE(is_archived, 0) = 0
		  ORDER BY last_active_at DESC, id DESC
		  LIMIT ? OFFSET ?`,
		tenantID, userID, limit, offset,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []SessionRow
	for rows.Next() {
		var s SessionRow
		var projectID, agentID sql.NullInt64
		var archived int
		if err := rows.Scan(&s.ID, &projectID, &s.Title, &agentID, &s.MessageCount,
			&s.LastActiveAt, &s.CreatedAt, &archived); err != nil {
			return nil, err
		}
		if agentID.Valid {
			v := int(agentID.Int64)
			s.AgentID = &v
		}
		if projectID.Valid {
			v := int(projectID.Int64)
			s.ProjectID = &v
		}
		s.IsArchived = archived != 0
		out = append(out, s)
	}
	return out, rows.Err()
}

// GetSessionOwner 拉一行的 user_id / tenant_id，用于 handler 层做归属校验。
// 找不到返回 sql.ErrNoRows。
func GetSessionOwner(ctx context.Context, db *sql.DB, sessionID string) (userID, tenantID string, err error) {
	if db == nil {
		return "", "", sql.ErrNoRows
	}
	err = db.QueryRowContext(ctx,
		`SELECT user_id, tenant_id FROM chat_sessions WHERE id = ?`,
		sessionID,
	).Scan(&userID, &tenantID)
	return
}

// ArchiveSession 软删：UPDATE is_archived=1。tenant + user 一起做过滤防越权。
// 返回 sql.ErrNoRows 表示无匹配行（可能已归档、不属于该用户、或不存在）。
func ArchiveSession(ctx context.Context, db *sql.DB, sessionID, userID, tenantID string) error {
	if db == nil {
		return nil
	}
	res, err := db.ExecContext(ctx,
		`UPDATE chat_sessions
		    SET is_archived = 1
		  WHERE id = ? AND user_id = ? AND tenant_id = ?`,
		sessionID, userID, tenantID,
	)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return sql.ErrNoRows
	}
	return nil
}

// deriveTitle 从首条消息生成 session 标题。
//
//  1. 空消息 → "新对话"
//  2. 跳过附件占位符块（H4 里 chat.py 用 "[文件: xxx]\n--- 内容 ---\n...\n--- 内容结束 ---\n\n<真实消息>"），
//     用真实用户文字做 title；找不到分隔符则用原文
//  3. trim 首尾空白/换行
//  4. rune-safe 截取前 24 个字符（不切坏中文/emoji）
func deriveTitle(msg string) string {
	msg = stripAttachmentPreamble(msg)
	msg = strings.TrimSpace(msg)
	if msg == "" {
		return "新对话"
	}
	const maxRunes = 24
	if utf8.RuneCountInString(msg) <= maxRunes {
		return msg
	}
	count := 0
	for i := range msg {
		if count == maxRunes {
			return msg[:i]
		}
		count++
	}
	return msg
}

// stripAttachmentPreamble 去掉 BuildContentWithAttachments 拼进来的附件段，留下用户原话。
//
// ## 为什么不能只找 `--- 内容结束 ---`
//
// 老实现是 `LastIndex("--- 内容结束 ---")`，它在**混合附件**下会切错：
//
//	[文件: a.pptx] … --- 内容结束 ---      ← 最后一个 marker 在这里
//	[文件: b.png]  说明：这是图片…          ← 但它后面还有一个没有正文的块
//	对比这两个
//
// 于是标题变成 "[文件: b.png] 位置：/mnt/uplo…"。抽不到文本的附件（图片、扫描件、
// 未知格式）没有正文块，所以只要它排在最后就会漏 —— 而"文档 + 图片一起传"是很常见
// 的用法。这两种情况都被测试抓到过。
//
// ## 现在的做法：锚在**最后一个**附件块上
//
// 先定位最后一个 `[文件: `，再看那个块怎么结束：
//
//	块内有 `--- 内容结束 ---`   → 用户消息在它之后
//	块内没有（无正文）          → 用户消息在该块最后一个空行之后
//
// 两种形状都覆盖，且不依赖附件的个数和顺序。
func stripAttachmentPreamble(msg string) string {
	const header = "[文件: "
	const endMarker = "--- 内容结束 ---"

	// 不是附件消息就原样返回。这道判断很要紧：下面按空行切，而用户自己分段写的长消息
	// 里的空行会被误当成边界，把前半截掉。
	if !strings.HasPrefix(msg, header) {
		return msg
	}
	// 最后一个附件块的起点 —— 用户消息一定在这个块之后。
	tail := msg
	if last := strings.LastIndex(msg, "\n\n"+header); last >= 0 {
		tail = msg[last+2:]
	}

	if idx := strings.Index(tail, endMarker); idx >= 0 {
		return tail[idx+len(endMarker):]
	}
	// 无正文块：注入段内部只用单换行，段与用户消息之间是 "\n\n"，所以最后一个空行
	// 就是边界。
	if idx := strings.LastIndex(tail, "\n\n"); idx >= 0 {
		return tail[idx+2:]
	}
	return msg
}
