package persistence

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
)

// Attachment 覆盖读（LoadAttachments 只用前三个）和写（InsertAttachment 用全部）两条路径。
// file_key 空串表示"没上 OBS"（H5 版本用户上传附件不落 OBS，见 plan）。
type Attachment struct {
	ID            int64
	TenantID      string
	UserID        string
	SessionID     string
	Filename      string
	FileKey       string
	FileType      string
	FileSize      int64
	MediaType     string
	ExtractedText string
}

// InsertAttachment 落一行 chat_attachments，返回自增 id。
// created_at 由 MySQL default now() 填。
func InsertAttachment(ctx context.Context, db *sql.DB, a Attachment) (int64, error) {
	if db == nil {
		return 0, nil
	}
	if a.MediaType == "" {
		a.MediaType = "text"
	}
	res, err := db.ExecContext(ctx,
		`INSERT INTO chat_attachments
		    (tenant_id, user_id, session_id, filename, file_key,
		     file_type, file_size, media_type, extracted_text)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		a.TenantID, a.UserID, a.SessionID, a.Filename, a.FileKey,
		a.FileType, a.FileSize, a.MediaType, a.ExtractedText,
	)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

// LoadAttachments 按 id 列表读 chat_attachments，用 tenant 做越权护栏。
// Python 侧 chat.py 没做 tenant 过滤（隐患），Go 侧顺手补上。
// 空 id 列表 → 返回 nil。ids 里查不到的行静默忽略（跟 Python 语义一致）。
func LoadAttachments(ctx context.Context, db *sql.DB, tenantID string, ids []int64) ([]Attachment, error) {
	if db == nil || len(ids) == 0 {
		return nil, nil
	}
	placeholders := make([]string, len(ids))
	args := make([]any, 0, len(ids)+1)
	for i, id := range ids {
		placeholders[i] = "?"
		args = append(args, id)
	}
	args = append(args, tenantID)

	q := fmt.Sprintf(
		`SELECT id, filename, COALESCE(extracted_text, '')
		   FROM chat_attachments
		  WHERE id IN (%s) AND tenant_id = ?`,
		strings.Join(placeholders, ","),
	)
	rows, err := db.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Attachment
	for rows.Next() {
		var a Attachment
		if err := rows.Scan(&a.ID, &a.Filename, &a.ExtractedText); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

// BuildContentWithAttachments 拼装 "[文件: xxx]\n--- 内容 ---\n...\n--- 内容结束 ---\n\n<消息>"，
// 与 lea-server chat.py::_build_message_content 语义对齐（H5 前端切过来后
// deriveTitle 的 stripAttachmentPreamble 才能吃到同一个 marker）。
//
// 无附件 → 原样返回 message。
// 有附件但都没提取到文本 → 拼 "[文件: xxx（无法提取文本内容）]"。
func BuildContentWithAttachments(message string, atts []Attachment) string {
	if len(atts) == 0 {
		return message
	}
	parts := make([]string, 0, len(atts))
	for _, a := range atts {
		if a.ExtractedText != "" {
			parts = append(parts, fmt.Sprintf(
				"[文件: %s]\n--- 内容 ---\n%s\n--- 内容结束 ---",
				a.Filename, a.ExtractedText,
			))
		} else {
			parts = append(parts, fmt.Sprintf("[文件: %s（无法提取文本内容）]", a.Filename))
		}
	}
	return strings.Join(parts, "\n\n") + "\n\n" + message
}
