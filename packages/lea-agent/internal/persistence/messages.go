package persistence

import (
	"context"
	"database/sql"
	"encoding/json"
	"time"
)

type Message struct {
	SessionID  string
	Role       string // user | assistant
	Content    string
	TokenCount *int
	ArtifactID *string
	Extra      map[string]any // → chat_messages.extra JSON
}

// MessageRow 前端消息历史用的字段。extra 保持 raw JSON，让前端自己解。
type MessageRow struct {
	ID        int64
	SessionID string
	Role      string
	Content   string
	CreatedAt time.Time
	Extra     json.RawMessage // 空时为 nil
}

// GetMessages 按 session_id 拉全部消息，主键升序（时间顺序）。
// 归属校验在 handler 层（GetSessionOwner），本函数只查数据。
func GetMessages(ctx context.Context, db *sql.DB, sessionID string) ([]MessageRow, error) {
	if db == nil {
		return nil, nil
	}
	rows, err := db.QueryContext(ctx,
		`SELECT id, session_id, role, content, created_at, extra
		   FROM chat_messages
		  WHERE session_id = ?
		  ORDER BY id ASC`,
		sessionID,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []MessageRow
	for rows.Next() {
		var m MessageRow
		var extra sql.NullString
		if err := rows.Scan(&m.ID, &m.SessionID, &m.Role, &m.Content, &m.CreatedAt, &extra); err != nil {
			return nil, err
		}
		if extra.Valid && extra.String != "" {
			m.Extra = json.RawMessage(extra.String)
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// SaveMessages 落一批消息。extra 为空时写 NULL（不写空对象，跟 Python 侧对齐）。
func SaveMessages(ctx context.Context, db *sql.DB, msgs ...Message) error {
	if db == nil || len(msgs) == 0 {
		return nil
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()

	stmt, err := tx.PrepareContext(ctx,
		`INSERT INTO chat_messages
		    (session_id, role, content, token_count, artifact_id, extra)
		 VALUES (?, ?, ?, ?, ?, ?)`,
	)
	if err != nil {
		return err
	}
	defer stmt.Close()

	for _, m := range msgs {
		var extraJSON any
		if len(m.Extra) > 0 {
			b, err := json.Marshal(m.Extra)
			if err != nil {
				return err
			}
			extraJSON = string(b)
		}
		var tokenCount any
		if m.TokenCount != nil {
			tokenCount = *m.TokenCount
		}
		var artifactID any
		if m.ArtifactID != nil {
			artifactID = *m.ArtifactID
		}
		if _, err := stmt.ExecContext(ctx,
			m.SessionID, m.Role, m.Content, tokenCount, artifactID, extraJSON,
		); err != nil {
			return err
		}
	}
	return tx.Commit()
}
