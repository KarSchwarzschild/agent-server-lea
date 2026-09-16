package persistence

import (
	"context"
	"database/sql"
	"fmt"
	"path"
	"strings"
)

// Artifact —— 模型显式声明的交付物。见 migrations/025。
//
// ## 跟 Attachment 的方向相反
//
//	Attachment  用户 → 我们（上传件）。归属是上传者。
//	Artifact    我们 → 用户（成品）。归属是生成它的那个会话，但**展示给用户**时
//	            两者混在同一个文件库里，因为用户不关心这个区别：他要的是"我的文件"。
//
// 混在一起展示、分开存储，是因为它们的降级语义相反（见 025 文件头）：上传失败要
// 拒绝（用户还拿着原件，重传很便宜），交付失败要保留（背后是几分钟的模型劳动）。
//
// ## OBSKey == "" 的含义
//
// "登记了但没存住" —— OBS 不可达，或用户的库配额满了。这一行仍然有效：rel_path
// 指向的那份文件还在会话工作区里（30 天内），用户在会话里仍能下载。只是它不在
// 长期文件库里，跨会话用不了。
//
// SHA256 跟着一起为空 —— 它是发布那一步算出来的，没发布就没有。别从 OBSKey 里
// 反解（那是 025 给 Attachment 定过的同一条规矩）。
type Artifact struct {
	ID          int64
	TenantID    string
	UserID      string
	SessionID   string
	ProjectID   sql.NullInt64
	RelPath     string
	Filename    string
	Title       string
	Description string
	OBSKey      string
	SHA256      string
	Size        int64
	MimeType    string
	CreatedAt   string
}

// UpsertArtifact 登记一个交付物。同一会话同一路径反复交付 → 更新同一行。
//
// upsert 而不是 insert：模型改三轮才定稿是常态（写 → 用户说不对 → 改 → 再交付），
// 每轮都该覆盖同一行。堆三行的话用户在文件库里看到三个同名文件，而其中两个是
// 他明确要求改掉的版本 —— 分不清哪个是最终版，只能靠时间戳猜。
//
// ⚠️ 覆盖的是**登记**，不是 OBS 上的对象。旧版本那个 obs_key 指向的对象仍然在
// （内容寻址，key 不同）。那是有意的：确认删除是用户的动作，不是一次交付的副作用。
// 代价是被覆盖掉的中间版本成了没人引用的对象 —— 由用户显式删除或后续的对账收，
// 而不是在这里顺手删（顺手删的话，一次误覆盖就永久毁掉上一版）。
func UpsertArtifact(ctx context.Context, db *sql.DB, a Artifact) (int64, error) {
	if db == nil {
		return 0, fmt.Errorf("db unavailable")
	}
	if a.Filename == "" {
		a.Filename = path.Base(a.RelPath)
	}
	res, err := db.ExecContext(ctx,
		`INSERT INTO session_artifacts
		    (tenant_id, user_id, session_id, project_id, rel_path, filename,
		     title, description, obs_key, sha256, size, mime_type)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		 ON DUPLICATE KEY UPDATE
		    filename    = VALUES(filename),
		    title       = VALUES(title),
		    description = VALUES(description),
		    obs_key     = VALUES(obs_key),
		    sha256      = VALUES(sha256),
		    size        = VALUES(size),
		    mime_type   = VALUES(mime_type),
		    -- user_id 也更新：同一个会话理论上只有一个 user，但历史行可能是空的
		    -- （早期写入没带），更新时补上比留着空值好。
		    user_id     = VALUES(user_id)`,
		a.TenantID, a.UserID, a.SessionID, a.ProjectID, a.RelPath, a.Filename,
		a.Title, a.Description, a.OBSKey, a.SHA256, a.Size, a.MimeType,
	)
	if err != nil {
		return 0, err
	}
	id, err := res.LastInsertId()
	if err != nil {
		return 0, err
	}
	// ON DUPLICATE KEY UPDATE 命中更新时 LastInsertId 是 0（没有 AUTO_INCREMENT
	// 分配）。调用方拿它当"这一行的 id"用会拿到 0，所以命中更新时回查一次。
	if id == 0 {
		_ = db.QueryRowContext(ctx,
			`SELECT id FROM session_artifacts WHERE session_id = ? AND rel_path = ?`,
			a.SessionID, a.RelPath,
		).Scan(&id)
	}
	return id, nil
}

// ListSessionArtifacts 列出一个会话交付过的成品，最新的在前。
func ListSessionArtifacts(ctx context.Context, db *sql.DB, tenantID, sessionID string) ([]Artifact, error) {
	if db == nil {
		return nil, nil
	}
	rows, err := db.QueryContext(ctx,
		`SELECT id, tenant_id, user_id, session_id, project_id, rel_path, filename,
		        title, description, obs_key, sha256, size, mime_type, created_at
		   FROM session_artifacts
		  WHERE tenant_id = ? AND session_id = ?
		  ORDER BY created_at DESC, id DESC`,
		tenantID, sessionID,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanArtifacts(rows)
}

// ListUserArtifacts 用户文件库里的成品那一半，最新的在前。
//
// 按 (tenant, user) 过滤而不是只按 user —— 同一个 user_id 在两个租户下是两个人
// （宿主平台给的 id 只在租户内唯一）。少了 tenant 过滤就是跨租户串数据。
func ListUserArtifacts(ctx context.Context, db *sql.DB, tenantID, userID string, limit, offset int) ([]Artifact, error) {
	if db == nil {
		return nil, nil
	}
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	rows, err := db.QueryContext(ctx,
		`SELECT id, tenant_id, user_id, session_id, project_id, rel_path, filename,
		        title, description, obs_key, sha256, size, mime_type, created_at
		   FROM session_artifacts
		  WHERE tenant_id = ? AND user_id = ?
		  ORDER BY created_at DESC, id DESC
		  LIMIT ? OFFSET ?`,
		tenantID, userID, limit, offset,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanArtifacts(rows)
}

func scanArtifacts(rows *sql.Rows) ([]Artifact, error) {
	var out []Artifact
	for rows.Next() {
		var a Artifact
		if err := rows.Scan(&a.ID, &a.TenantID, &a.UserID, &a.SessionID, &a.ProjectID,
			&a.RelPath, &a.Filename, &a.Title, &a.Description, &a.OBSKey,
			&a.SHA256, &a.Size, &a.MimeType, &a.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

// GetArtifactForUser 按 id 取一行，同时校验归属。取不到（不存在**或**不是他的）
// 返回 sql.ErrNoRows。
//
// 两种情况不分开，跟 GetAttachmentForDownload 同一条理由：id 是自增的，分开报
// 等于给出一个"这个 id 存在但不属于你"的探测口 —— 顺着数就能数出别人有多少文件。
func GetArtifactForUser(ctx context.Context, db *sql.DB, tenantID, userID string, id int64) (Artifact, error) {
	var a Artifact
	if db == nil {
		return a, fmt.Errorf("db unavailable")
	}
	err := db.QueryRowContext(ctx,
		`SELECT id, tenant_id, user_id, session_id, project_id, rel_path, filename,
		        title, description, obs_key, sha256, size, mime_type, created_at
		   FROM session_artifacts
		  WHERE id = ? AND tenant_id = ? AND user_id = ?`,
		id, tenantID, userID,
	).Scan(&a.ID, &a.TenantID, &a.UserID, &a.SessionID, &a.ProjectID,
		&a.RelPath, &a.Filename, &a.Title, &a.Description, &a.OBSKey,
		&a.SHA256, &a.Size, &a.MimeType, &a.CreatedAt)
	return a, err
}

// DeleteArtifact 删掉一行登记。**不碰 OBS 对象** —— 那由调用方在确认没有别的行
// 引用同一个 sha 之后单独删（见 CountSHARefs）。
func DeleteArtifact(ctx context.Context, db *sql.DB, tenantID, userID string, id int64) (bool, error) {
	if db == nil {
		return false, fmt.Errorf("db unavailable")
	}
	res, err := db.ExecContext(ctx,
		`DELETE FROM session_artifacts WHERE id = ? AND tenant_id = ? AND user_id = ?`,
		id, tenantID, userID,
	)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	return n > 0, err
}

// CountSHARefs 数一数**整个租户内**还有多少行指向这个 sha256（两张表加起来）。
//
// ## 为什么删对象之前必须问这一句
//
// OBS 是内容寻址的：同一份课件被 40 个学生上传，库里 40 行，对象只有一个。
// 甲删掉自己那一行就把对象删了的话，另外 39 个人的文件**同时消失** —— 而他们
// 什么都没做。用户看到的是文件凭空不见了，日志里没有任何针对他们的操作。
//
// ## 为什么按租户数，不按用户数
//
// 去重的边界就是租户（upload_key 带 tenant 前缀，跨租户不共享对象）。按用户数的话
// 甲删完发现"我这边没有别的引用了"就删对象，而乙在同租户下引用着同一个 —— 正是
// 上面那个 bug。
//
// 调用方要在**删完自己那一行之后**再问，问出 0 才真删对象。顺序反了的话永远是 ≥1。
func CountSHARefs(ctx context.Context, db *sql.DB, tenantID, sha string) (int, error) {
	if db == nil || sha == "" {
		return 0, fmt.Errorf("db unavailable or empty sha")
	}
	var n int
	err := db.QueryRowContext(ctx,
		`SELECT
		   (SELECT COUNT(*) FROM session_artifacts WHERE tenant_id = ? AND sha256 = ?)
		 + (SELECT COUNT(*) FROM chat_attachments  WHERE tenant_id = ? AND sha256 = ?)`,
		tenantID, sha, tenantID, sha,
	).Scan(&n)
	return n, err
}

// StorageUsage 用户的存储占用。两张表分开报，因为用户能分别处理它们
// （上传件他自己传的，成品是模型生成的），一个合计数字看不出该删哪边。
type StorageUsage struct {
	UploadBytes   int64 `json:"upload_bytes"`
	ArtifactBytes int64 `json:"artifact_bytes"`
	TotalBytes    int64 `json:"total_bytes"`
	QuotaBytes    int64 `json:"quota_bytes"`
	UploadCount   int   `json:"upload_count"`
	ArtifactCount int   `json:"artifact_count"`
}

// UserStorageUsage 算一个用户占了多少 OBS 空间。
//
// ## 为什么是 SUM(DISTINCT ...) 而不是 SUM
//
// 见 migrations/026 的文件头：对象存储那层按 (tenant, sha) 去重，同一份内容传 40 次
// 只占一个对象。裸 SUM 会把它算 40 遍，用户看到的数字比真实占用大一个数量级，
// 然后在明明没满的时候被 507 挡住 —— 而他删不掉任何东西来缓解，因为那些字节本来
// 就不存在。
//
// 用子查询里的 DISTINCT (sha256, size) 而不是 SUM(DISTINCT size)：后者会把两个
// **不同内容但恰好同样大**的文件折成一个（比如两张都是 102400 字节的图），
// 少算一份。折叠的键必须是 sha，不是 size。
//
// ## file_key = '' 的行不算
//
// 那是"没存住"的降级行（见 Attachment 的注释）—— 它在 OBS 上没有对象，不占空间。
// 把它算进去的话，一次 OBS 故障之后用户的配额会平白紧一截，而且删不掉（他删了行，
// 空间也不会降，因为本来就没占）。artifacts 那边同理，判据是 obs_key。
func UserStorageUsage(ctx context.Context, db *sql.DB, tenantID, userID string, quotaBytes int64) (StorageUsage, error) {
	u := StorageUsage{QuotaBytes: quotaBytes}
	if db == nil {
		return u, fmt.Errorf("db unavailable")
	}

	err := db.QueryRowContext(ctx,
		`SELECT COALESCE(SUM(file_size), 0), COUNT(*) FROM (
		   SELECT DISTINCT sha256, file_size
		     FROM chat_attachments
		    WHERE tenant_id = ? AND user_id = ? AND file_key <> '' AND sha256 <> ''
		 ) t`,
		tenantID, userID,
	).Scan(&u.UploadBytes, &u.UploadCount)
	if err != nil {
		return u, err
	}

	err = db.QueryRowContext(ctx,
		`SELECT COALESCE(SUM(size), 0), COUNT(*) FROM (
		   SELECT DISTINCT sha256, size
		     FROM session_artifacts
		    WHERE tenant_id = ? AND user_id = ? AND obs_key <> '' AND sha256 <> ''
		 ) t`,
		tenantID, userID,
	).Scan(&u.ArtifactBytes, &u.ArtifactCount)
	if err != nil {
		return u, err
	}

	// 两张表之间也可能撞 sha（用户上传了一份文件，模型原样交付了同一份内容）——
	// 那在 OBS 上是**两个对象**，因为前缀不同（uploads/ vs library/）。所以这里
	// 直接相加是对的，不需要跨表去重。
	u.TotalBytes = u.UploadBytes + u.ArtifactBytes
	return u, nil
}

// TenantStorageUsage 租户级占用。同一套口径，少一层 user 过滤。
//
// 租户配额存在的理由跟用户配额不同：用户配额防的是一个人占满，租户配额防的是
// **一个租户里所有人加起来**占满整个 bucket。两者都要，因为前者拦不住 100 个用户
// 各占 90% 的额度。
func TenantStorageUsage(ctx context.Context, db *sql.DB, tenantID string) (int64, error) {
	if db == nil {
		return 0, fmt.Errorf("db unavailable")
	}
	var uploads, artifacts int64
	if err := db.QueryRowContext(ctx,
		`SELECT COALESCE(SUM(file_size), 0) FROM (
		   SELECT DISTINCT sha256, file_size FROM chat_attachments
		    WHERE tenant_id = ? AND file_key <> '' AND sha256 <> ''
		 ) t`, tenantID).Scan(&uploads); err != nil {
		return 0, err
	}
	if err := db.QueryRowContext(ctx,
		`SELECT COALESCE(SUM(size), 0) FROM (
		   SELECT DISTINCT sha256, size FROM session_artifacts
		    WHERE tenant_id = ? AND obs_key <> '' AND sha256 <> ''
		 ) t`, tenantID).Scan(&artifacts); err != nil {
		return 0, err
	}
	return uploads + artifacts, nil
}

// ListUserUploads 用户文件库里的上传件那一半。
//
// ⚠️ 按 (tenant, user) 过滤 —— 注意这跟同文件里的 LoadAttachments 不一样，
// 那个只按 tenant_id 过滤（它取的是"某个会话的附件"，会话本身已经限定了归属）。
// 这里是"某个用户的所有附件"，没有会话兜底，user_id 是唯一的边界。
func ListUserUploads(ctx context.Context, db *sql.DB, tenantID, userID string, limit, offset int) ([]Attachment, error) {
	if db == nil {
		return nil, nil
	}
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	rows, err := db.QueryContext(ctx,
		`SELECT id, tenant_id, COALESCE(user_id, ''), session_id, filename,
		        COALESCE(file_key, ''), COALESCE(file_type, ''), COALESCE(file_size, 0),
		        COALESCE(media_type, ''), COALESCE(sha256, ''), COALESCE(staged_path, ''),
		        created_at
		   FROM chat_attachments
		  WHERE tenant_id = ? AND user_id = ?
		  ORDER BY created_at DESC, id DESC
		  LIMIT ? OFFSET ?`,
		tenantID, userID, limit, offset,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Attachment
	for rows.Next() {
		var a Attachment
		var createdAt sql.NullString
		if err := rows.Scan(&a.ID, &a.TenantID, &a.UserID, &a.SessionID, &a.Filename,
			&a.FileKey, &a.FileType, &a.FileSize, &a.MediaType, &a.SHA256,
			&a.StagedPath, &createdAt); err != nil {
			return nil, err
		}
		a.CreatedAt = createdAt.String
		out = append(out, a)
	}
	return out, rows.Err()
}

// DeleteUpload 删掉一行上传登记。跟 DeleteArtifact 一样不碰 OBS 对象。
func DeleteUpload(ctx context.Context, db *sql.DB, tenantID, userID string, id int64) (bool, error) {
	if db == nil {
		return false, fmt.Errorf("db unavailable")
	}
	res, err := db.ExecContext(ctx,
		`DELETE FROM chat_attachments WHERE id = ? AND tenant_id = ? AND user_id = ?`,
		id, tenantID, userID,
	)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	return n > 0, err
}

// ArtifactSummaryLine 给模型看的一行说明。交付成功后回给它，让它在回复里能准确
// 转述"存到哪了、叫什么"，而不是自己编一个路径。
func ArtifactSummaryLine(a Artifact) string {
	var b strings.Builder
	b.WriteString(a.Filename)
	if a.Title != "" && a.Title != a.Filename {
		b.WriteString("（" + a.Title + "）")
	}
	if a.OBSKey == "" {
		b.WriteString("：已登记为本次会话的成品，但没能存进文件库")
	} else {
		b.WriteString("：已存入用户的文件库，其他对话里也能用")
	}
	return b.String()
}
