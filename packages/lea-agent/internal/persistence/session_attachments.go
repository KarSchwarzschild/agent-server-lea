package persistence

import (
	"context"
	"database/sql"
)

// 会话级挂载 —— 用户在**这一个会话里**临时挂上的工具/知识库（migrations/017）。
//
// ⚠️ 不要跟同包的 attachments.go 搞混：那个是**聊天附件**（用户上传的文件，走
// chat_attachments 表），跟这里毫无关系。两者英文都叫 attachment，所以文件名和类型名
// 都带 SessionAttachment 前缀区分。
//
// 跟 agent 的绑定也是两回事：
//
//	agent_tool_bindings   agent 定义的一部分，所有使用者相同，改它 = 改这个 agent
//	session_attachments   只属于一个会话，别人用同一个 agent 完全不受影响
//
// ## 为什么要落库而不是只留在内存
//
// lea-agent 会回收最久未活跃的闲置会话（session/manager.go 的 ensureCapacity）。回收
// 不丢对话（历史在 agent_messages），但只存在于内存对象里的挂载会没：
//
//	10:00 挂上 → 12:30 会话闲置被回收 → 13:00 回来发消息 → 「我没有查库存的工具」
//
// 用户什么都没做错，上午还在用，而且没有任何提示。

// SessionAttachment 是 session_attachments 的一行。
type SessionAttachment struct {
	Kind  string // tool | kb（mcp_server / skill 是下一批）
	RefID int
}

// SessionAttachmentKinds 是这一版认得的 kind。
//
// 表上那列是 VARCHAR 而不是 ENUM（见 migrations/017），所以库里可能出现这一版还不认识
// 的值 —— 那是故意留的：加一种新资源不该要求先改所有读端再排序部署。读到不认识的照常
// 传给 lea-config，由它决定丢弃，**这里不过滤**：过滤了就等于把"未知 kind"变成一个
// 静默的黑洞，而 lea-config 那边至少会在 dropped 里报出来。
var SessionAttachmentKinds = map[string]bool{"tool": true, "kb": true}

// LoadSessionAttachments 读一个会话挂了什么。按 (tenant, session) 查 —— 索引就是这个形状。
//
// db 为 nil 时返回 nil（单元测试和没配库的本地开发都会走到），不是错误。
func LoadSessionAttachments(
	ctx context.Context, db *sql.DB, tenantID, sessionID string,
) ([]SessionAttachment, error) {
	if db == nil || sessionID == "" {
		return nil, nil
	}
	rows, err := db.QueryContext(ctx,
		`SELECT kind, ref_id FROM session_attachments
		  WHERE tenant_id = ? AND session_id = ?
		  ORDER BY id`,
		tenantID, sessionID,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []SessionAttachment
	for rows.Next() {
		var a SessionAttachment
		if err := rows.Scan(&a.Kind, &a.RefID); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

// AddSessionAttachment 挂上一个。重复挂载是**幂等**的，不是错误。
//
// 用 INSERT ... ON DUPLICATE KEY UPDATE 而不是先查再插：并发两个请求挂同一个工具时，
// 先查再插会有一个撞唯一索引 500。而"重复点了一下挂载按钮"是个必然会发生的操作，
// 不该让用户看到错误。
//
// UPDATE 里只碰 user_id，因为主键三元组是 (session_id, kind, ref_id) —— 同一个会话里
// 同一个工具被"重新挂"时唯一可能变的就是操作者。created_at 保留首次的时间。
func AddSessionAttachment(
	ctx context.Context, db *sql.DB, tenantID, sessionID, userID, kind string, refID int,
) error {
	if db == nil {
		return nil
	}
	_, err := db.ExecContext(ctx,
		`INSERT INTO session_attachments (session_id, tenant_id, user_id, kind, ref_id)
		 VALUES (?, ?, ?, ?, ?)
		 ON DUPLICATE KEY UPDATE user_id = VALUES(user_id)`,
		sessionID, tenantID, userID, kind, refID,
	)
	return err
}

// RemoveSessionAttachment 摘掉一个。返回是否真的删了行。
//
// 删不存在的行返回 (false, nil) 而不是错误 —— 调用方据此返 404 或者干脆当成功。
// 摘一个已经不在的东西，结果状态跟用户想要的完全一致。
func RemoveSessionAttachment(
	ctx context.Context, db *sql.DB, tenantID, sessionID, kind string, refID int,
) (bool, error) {
	if db == nil {
		return false, nil
	}
	res, err := db.ExecContext(ctx,
		`DELETE FROM session_attachments
		  WHERE tenant_id = ? AND session_id = ? AND kind = ? AND ref_id = ?`,
		tenantID, sessionID, kind, refID,
	)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	return n > 0, err
}
