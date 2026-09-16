package persistence

import (
	"context"
	"database/sql"
)

// ProjectRow 直接被 handler 序列化成 HTTP 响应，所以 json tag 是**契约的一部分**，
// 不是可选的装饰。少了它们，Go 按字段名导出 ID/Name/SessionCount/IsArchived，
// 而前端读的是 id/name/session_count/is_archived（api.ts 的 ProjectItem）——
// 结果是 200、结构看着对、每个字段都是 undefined，界面上项目列表全是空白名字。
type ProjectRow struct {
	ID           int    `json:"id"`
	Name         string `json:"name"`
	Description  string `json:"description"`
	SessionCount int    `json:"session_count"`
	IsArchived   bool   `json:"is_archived"`
}

func CreateProject(ctx context.Context, db *sql.DB, tenantID, userID, name, description string) (ProjectRow, error) {
	result, err := db.ExecContext(ctx, `INSERT INTO projects (tenant_id, user_id, name, description) VALUES (?, ?, ?, ?)`, tenantID, userID, name, description)
	if err != nil {
		return ProjectRow{}, err
	}
	id, err := result.LastInsertId()
	if err != nil {
		return ProjectRow{}, err
	}
	return ProjectRow{ID: int(id), Name: name, Description: description}, nil
}

func GetProject(ctx context.Context, db *sql.DB, id int, tenantID, userID string) (ProjectRow, error) {
	var project ProjectRow
	err := db.QueryRowContext(ctx, `
		SELECT p.id, p.name, p.description, COUNT(s.id), COALESCE(p.is_archived, 0)
		  FROM projects p
		  LEFT JOIN chat_sessions s ON s.project_id = p.id AND s.tenant_id = p.tenant_id AND s.user_id = p.user_id AND COALESCE(s.is_archived, 0) = 0
		 WHERE p.id = ? AND p.tenant_id = ? AND p.user_id = ?
		 GROUP BY p.id, p.name, p.description, p.is_archived`, id, tenantID, userID).
		Scan(&project.ID, &project.Name, &project.Description, &project.SessionCount, &project.IsArchived)
	return project, err
}

func UpdateProject(ctx context.Context, db *sql.DB, id int, tenantID, userID, name, description string) (ProjectRow, error) {
	result, err := db.ExecContext(ctx, `
		UPDATE projects SET name = ?, description = ?
		 WHERE id = ? AND tenant_id = ? AND user_id = ? AND COALESCE(is_archived, 0) = 0`,
		name, description, id, tenantID, userID)
	if err != nil {
		return ProjectRow{}, err
	}
	if affected, _ := result.RowsAffected(); affected == 0 {
		return ProjectRow{}, sql.ErrNoRows
	}
	return GetProject(ctx, db, id, tenantID, userID)
}

func ArchiveProject(ctx context.Context, db *sql.DB, id int, tenantID, userID string) error {
	result, err := db.ExecContext(ctx, `
		UPDATE projects SET is_archived = 1
		 WHERE id = ? AND tenant_id = ? AND user_id = ? AND COALESCE(is_archived, 0) = 0`,
		id, tenantID, userID)
	if err != nil {
		return err
	}
	if affected, _ := result.RowsAffected(); affected == 0 {
		return sql.ErrNoRows
	}
	return nil
}

func DeleteProject(ctx context.Context, db *sql.DB, id int, tenantID, userID string) error {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()
	if _, err = tx.ExecContext(ctx, `
		UPDATE chat_sessions SET project_id = NULL
		 WHERE project_id = ? AND tenant_id = ? AND user_id = ?`, id, tenantID, userID); err != nil {
		return err
	}
	result, err := tx.ExecContext(ctx, `
		DELETE FROM projects WHERE id = ? AND tenant_id = ? AND user_id = ?`, id, tenantID, userID)
	if err != nil {
		return err
	}
	if affected, _ := result.RowsAffected(); affected == 0 {
		return sql.ErrNoRows
	}
	return tx.Commit()
}

func ListProjects(ctx context.Context, db *sql.DB, tenantID, userID string) ([]ProjectRow, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT p.id, p.name, p.description, COUNT(s.id), COALESCE(p.is_archived, 0)
		FROM projects p
		LEFT JOIN chat_sessions s ON s.project_id = p.id AND s.tenant_id = p.tenant_id AND s.user_id = p.user_id AND COALESCE(s.is_archived, 0) = 0
		WHERE p.tenant_id = ? AND p.user_id = ? AND COALESCE(p.is_archived, 0) = 0
		GROUP BY p.id, p.name, p.description, p.is_archived, p.updated_at
		ORDER BY p.updated_at DESC, p.id DESC`, tenantID, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var projects []ProjectRow
	for rows.Next() {
		var project ProjectRow
		if err := rows.Scan(&project.ID, &project.Name, &project.Description, &project.SessionCount, &project.IsArchived); err != nil {
			return nil, err
		}
		projects = append(projects, project)
	}
	return projects, rows.Err()
}

func ProjectOwnedBy(ctx context.Context, db *sql.DB, id int, tenantID, userID string) (bool, error) {
	var exists int
	err := db.QueryRowContext(ctx, `SELECT 1 FROM projects WHERE id = ? AND tenant_id = ? AND user_id = ? AND COALESCE(is_archived, 0) = 0`, id, tenantID, userID).Scan(&exists)
	if err == sql.ErrNoRows {
		return false, nil
	}
	return err == nil && exists == 1, err
}
