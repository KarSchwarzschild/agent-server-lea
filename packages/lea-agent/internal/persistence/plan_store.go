package persistence

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
)

// 计划有两份，不是一份两个状态（见 migrations/009）：
//
//	approved_content / approved_steps_json  已批准、正在执行的那份 + 进度
//	draft_content    / draft_steps_json     待批准的那份
//
// 拆开的理由是修订流程：执行中途模型提交修订版时，用户还没表态，那份**已经批准的**
// 计划必须原样留着 —— 否则用户点「否决」之后两份都没了，模型继续跑但一份计划都不
// 带，而且没有任何提示。008 的单列 + REPLACE INTO 就是那个形状。

// 步骤状态。取值刻意跟引擎 TodoWrite 的那套一致 —— 模型见过这三个词。
const (
	StepPending    = "pending"
	StepInProgress = "in_progress"
	StepCompleted  = "completed"
)

// PlanStep 是计划里的一步。
type PlanStep struct {
	Title  string `json:"title"`
	Status string `json:"status"`
}

// ValidStepStatus 判断状态词合不合法。模型填的值要过这道门，否则一个拼错的
// "complete" 会变成一个永远不等于 StepCompleted 的状态，进度卡在那里而没人报错。
func ValidStepStatus(s string) bool {
	switch s {
	case StepPending, StepInProgress, StepCompleted:
		return true
	}
	return false
}

// Plan 是一个会话的计划快照。四个字段一次查出来 —— 调用方要么全要（get_plan），
// 要么要正文加进度（每轮注入），分成多次查只会多几次往返。
type Plan struct {
	Approved      string
	ApprovedSteps []PlanStep
	Draft         string
	DraftSteps    []PlanStep
}

// Progress 返回已批准计划的完成数和总数。没有步骤时两个都是 0。
func (p Plan) Progress() (done, total int) {
	for _, s := range p.ApprovedSteps {
		if s.Status == StepCompleted {
			done++
		}
	}
	return done, len(p.ApprovedSteps)
}

// CurrentStep 返回正在做的那一步的标题；没有 in_progress 时返回第一个 pending，
// 都没有则返回空串。给每轮注入的那行指针用。
func (p Plan) CurrentStep() string {
	for _, s := range p.ApprovedSteps {
		if s.Status == StepInProgress {
			return s.Title
		}
	}
	for _, s := range p.ApprovedSteps {
		if s.Status == StepPending {
			return s.Title
		}
	}
	return ""
}

// marshalSteps 把步骤序列化成入库的字符串。空切片存 NULL 而不是 "[]" ——
// 让"没有步骤"在库里只有一种表示。
func marshalSteps(steps []PlanStep) any {
	if len(steps) == 0 {
		return nil
	}
	raw, err := json.Marshal(steps)
	if err != nil {
		return nil
	}
	return string(raw)
}

// unmarshalSteps 反解步骤。解不动时返回 nil 而不是报错：库里存着一份坏 JSON 不该
// 让整个会话不可用，退化成"这个计划没有步骤"是能继续跑的降级。
func unmarshalSteps(raw sql.NullString) []PlanStep {
	if !raw.Valid || strings.TrimSpace(raw.String) == "" {
		return nil
	}
	var steps []PlanStep
	if err := json.Unmarshal([]byte(raw.String), &steps); err != nil {
		return nil
	}
	return steps
}

// SaveDraftPlan 写入待批准的计划（首次提交或修订版），不碰已批准的那份。
//
// steps 可以为空 —— 模型没给出结构化步骤时计划仍然成立，只是没有进度可追踪。
//
// 用「先 UPDATE，没打中再 INSERT」而不是 REPLACE INTO / ON DUPLICATE KEY UPDATE：
// REPLACE 会删掉整行（approved 跟着没），而 ON DUPLICATE KEY UPDATE 是 MySQL 方言、
// SQLite 不认（测试跑在 SQLite 上，两边必须是同一条 SQL 才有意义）。
//
// 并发与幂等都靠 UNIQUE(tenant_id, session_id) 兜底，四种情况都收敛且不会打转：
//   - 行已存在且内容有变     UPDATE 打中 → 结束
//   - 行已存在且内容相同     MySQL 的 RowsAffected 数的是**改动**行数，所以这里是 0
//     → INSERT 撞唯一键失败 → 再 UPDATE 一次 → 返回 nil。内容本来就对。
//   - 行不存在               UPDATE 0 → INSERT 成功 → 结束
//   - 两个副本同时来         两边都 UPDATE 0，一个 INSERT 成功、另一个撞键失败 →
//     失败那个补一次 UPDATE → 结束
func SaveDraftPlan(ctx context.Context, db *sql.DB, tenantID, sessionID, content string, steps []PlanStep) error {
	stepsJSON := marshalSteps(steps)

	const updateSQL = `UPDATE session_plans SET draft_content = ?, draft_steps_json = ?
	                   WHERE tenant_id = ? AND session_id = ?`

	res, err := db.ExecContext(ctx, updateSQL, content, stepsJSON, tenantID, sessionID)
	if err != nil {
		return fmt.Errorf("save draft plan (update): %w", err)
	}
	if n, err := res.RowsAffected(); err == nil && n > 0 {
		return nil
	}

	_, err = db.ExecContext(ctx,
		`INSERT INTO session_plans (tenant_id, session_id, draft_content, draft_steps_json)
		 VALUES (?, ?, ?, ?)`,
		tenantID, sessionID, content, stepsJSON)
	if err == nil {
		return nil
	}

	// INSERT 没成功，几乎只可能是撞了唯一键（行在我们 UPDATE 之后被别人建了，或者
	// 内容本来就相同所以上面那次 UPDATE 报了 0 行）。补一次 UPDATE：成了就说明确实
	// 是这两种情况；再失败才是真错，把 INSERT 的原始错误一起带上。
	if _, uerr := db.ExecContext(ctx, updateSQL, content, stepsJSON, tenantID, sessionID); uerr != nil {
		return fmt.Errorf("save draft plan (insert: %v; retry update): %w", err, uerr)
	}
	return nil
}

// ApprovePlan 把待批准的那份转正：正文和步骤一起搬到 approved，draft 两列清空。
//
// `WHERE draft_content IS NOT NULL` 是关键：没有待批准的计划时这是个 no-op，而不是
// 拿 NULL 把已批准的那份**覆盖掉**。用户重复点「同意」、前端重发请求、多副本同时
// 处理同一条消息，走的都是这一支。
//
// 步骤跟着正文一起搬 —— 它们描述的是同一份计划，分开搬就会出现"正文是 v2、进度是
// v1"这种没人能解释的状态。
func ApprovePlan(ctx context.Context, db *sql.DB, tenantID, sessionID string) error {
	_, err := db.ExecContext(ctx,
		`UPDATE session_plans
		    SET approved_content    = draft_content,
		        approved_steps_json = draft_steps_json,
		        draft_content       = NULL,
		        draft_steps_json    = NULL
		  WHERE tenant_id = ? AND session_id = ? AND draft_content IS NOT NULL`,
		tenantID, sessionID)
	if err != nil {
		return fmt.Errorf("approve plan: %w", err)
	}
	return nil
}

// RejectPlan 丢掉待批准的那份（正文和步骤），已批准的原样留着。
//
// 语义是「这版修订我不要，按原计划继续」。整份计划都不想要的话是 ClearPlan。
func RejectPlan(ctx context.Context, db *sql.DB, tenantID, sessionID string) error {
	_, err := db.ExecContext(ctx,
		`UPDATE session_plans SET draft_content = NULL, draft_steps_json = NULL
		  WHERE tenant_id = ? AND session_id = ?`,
		tenantID, sessionID)
	if err != nil {
		return fmt.Errorf("reject plan: %w", err)
	}
	return nil
}

// LoadPlan 一次读出这个会话的全部计划状态。没有行时返回零值 Plan 和 nil error ——
// "还没有计划"是正常状态，不是错误。
func LoadPlan(ctx context.Context, db *sql.DB, tenantID, sessionID string) (Plan, error) {
	var approvedContent, draftContent, approvedSteps, draftSteps sql.NullString
	err := db.QueryRowContext(ctx,
		`SELECT approved_content, approved_steps_json, draft_content, draft_steps_json
		   FROM session_plans WHERE tenant_id = ? AND session_id = ?`,
		tenantID, sessionID).Scan(&approvedContent, &approvedSteps, &draftContent, &draftSteps)
	if err == sql.ErrNoRows {
		return Plan{}, nil
	}
	if err != nil {
		return Plan{}, fmt.Errorf("load plan: %w", err)
	}
	return Plan{
		Approved:      approvedContent.String,
		ApprovedSteps: unmarshalSteps(approvedSteps),
		Draft:         draftContent.String,
		DraftSteps:    unmarshalSteps(draftSteps),
	}, nil
}

// UpdateStepStatus 把已批准计划的第 index 步（**从 1 开始**）标成 status，返回改完
// 之后的整份计划。
//
// 从 1 开始是因为它对着用户和模型：注入的那行指针写"共 7 步，已完成 3 步"，模型说
// "第 3 步" 时指的就是这里的 3。让工具的编号跟对话里的说法一致，比省一次减法重要。
//
// 读-改-写而不是在 SQL 里改 JSON：MySQL 和 SQLite 的 json_set 路径语法有差别，而
// 测试跑在 SQLite 上、生产在 MySQL —— 两边不是同一条 SQL 的话测试就不算测过。
// 丢更新由调用方那侧防住：UpdatePlanStepTool.IsConcurrencySafe() 返回 false，所以
// 引擎不会在同一轮里并行调它；而跨轮次有会话级的执行锁。
func UpdateStepStatus(ctx context.Context, db *sql.DB, tenantID, sessionID string, index int, status string) (Plan, error) {
	if !ValidStepStatus(status) {
		return Plan{}, fmt.Errorf("unknown step status %q", status)
	}

	plan, err := LoadPlan(ctx, db, tenantID, sessionID)
	if err != nil {
		return Plan{}, err
	}
	if len(plan.ApprovedSteps) == 0 {
		return plan, fmt.Errorf("no approved plan with steps for this session")
	}
	if index < 1 || index > len(plan.ApprovedSteps) {
		return plan, fmt.Errorf("step %d out of range (1..%d)", index, len(plan.ApprovedSteps))
	}

	plan.ApprovedSteps[index-1].Status = status

	if _, err := db.ExecContext(ctx,
		`UPDATE session_plans SET approved_steps_json = ? WHERE tenant_id = ? AND session_id = ?`,
		marshalSteps(plan.ApprovedSteps), tenantID, sessionID); err != nil {
		return plan, fmt.Errorf("update step status: %w", err)
	}
	return plan, nil
}

// ClearPlan 删掉整行 —— 两份计划都不要了。会话删除时调用，或者用户明确要放弃
// 当前计划重新开始。
func ClearPlan(ctx context.Context, db *sql.DB, tenantID, sessionID string) error {
	_, err := db.ExecContext(ctx,
		`DELETE FROM session_plans WHERE tenant_id = ? AND session_id = ?`,
		tenantID, sessionID)
	if err != nil {
		return fmt.Errorf("clear plan: %w", err)
	}
	return nil
}
