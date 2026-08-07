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
	MessageCount int
	LastActiveAt time.Time
	CreatedAt    time.Time
	IsArchived   bool
}

// TouchSession 首次建行 / 后续 message_count++。
// created_at / last_active_at 交给 MySQL 的 DEFAULT / ON UPDATE。
//
// tenantID 目前 chat_sessions 表里对应列名是 tenant_id（TenantMixin）；
// 若列不存在会 fallback（见 exec 的 error 分支）。
func TouchSession(ctx context.Context, db *sql.DB, sessionID, userID, tenantID, firstMessage string) error {
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
		    (id, user_id, tenant_id, title, app_name, message_count, is_archived)
		 VALUES (?, ?, ?, ?, 'chat', 1, 0)`,
		sessionID, userID, tenantID, title,
	)
	return err
}

// ListSessions 按 tenant + user 返回未归档会话，最近活跃在前。
func ListSessions(ctx context.Context, db *sql.DB, tenantID, userID string, limit, offset int) ([]SessionRow, error) {
	if db == nil {
		return nil, nil
	}
	if limit <= 0 {
		limit = 50
	}
	// chat_sessions 目前没有 agent_id 列（H5 冒烟发现），先返 NULL；后续
	// 需要按 agent 分组时再加迁移。
	rows, err := db.QueryContext(ctx,
		`SELECT id, COALESCE(title, ''), NULL AS agent_id, COALESCE(message_count, 0),
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
		var agentID sql.NullInt64
		var archived int
		if err := rows.Scan(&s.ID, &s.Title, &agentID, &s.MessageCount,
			&s.LastActiveAt, &s.CreatedAt, &archived); err != nil {
			return nil, err
		}
		if agentID.Valid {
			v := int(agentID.Int64)
			s.AgentID = &v
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

// stripAttachmentPreamble 去掉 chat.py 拼进来的附件文本块。
// 附件块以 "--- 内容结束 ---" 结尾，其后跟 "\n\n<真实消息>"。
func stripAttachmentPreamble(msg string) string {
	const marker = "--- 内容结束 ---"
	idx := strings.LastIndex(msg, marker)
	if idx < 0 {
		return msg
	}
	return msg[idx+len(marker):]
}
