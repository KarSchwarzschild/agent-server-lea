package persistence

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"strings"
	"time"

	"github.com/google/uuid"
	enginesessions "gitlab.ouc-online.com.cn/aibase/agentloop/sessions"
)

type RunStatus string

var ErrActiveRun = errors.New("an active run already exists for this session")

// ErrCannotClaimRun 标记"抢执行权失败"这一类特定错误，供调用方（目前是
// temporalrun.Activities.Execute）用 errors.Is 识别，从而把它标成不可重试的
// Temporal 错误。这不是瞬时故障（网络抖动、worker 崩溃）—— 它的含义是"已经有
// 更新的 attempt 抢到了，或者这个 run 的状态已经离开 queued/running"，重试
// 只会拿到一模一样的结果，还会在竞争里继续添乱（见 MarkAgentRunRunning 的
// 调用点说明）。
var ErrCannotClaimRun = errors.New("cannot claim run attempt")

// ErrSessionArchived 标记"这个会话已经被软删（is_archived=1）"。
//
// 会话列表按 is_archived 过滤，但归属校验（GetSessionOwner/verifySessionOwner）
// 和内存里已经建好的会话对象完全不检查这个字段——于是"删除会话"只是从侧栏
// 隐藏，另一个还开着这个会话的标签页可以无限期继续聊下去，正常落库，用户
// 却在列表里再也看不到它。这里在创建 run 的必经之路上补一次检查：不是每条
// 消息都要单独查一次库，是复用 CreateExclusiveAgentRun 本来就要做的
// `SELECT ... FOR UPDATE`，同一次查询顺带取 is_archived，零额外开销。
var ErrSessionArchived = errors.New("session has been archived")

const (
	RunQueued     RunStatus = "queued"
	RunRunning    RunStatus = "running"
	RunCancelling RunStatus = "cancelling"
	RunCancelled  RunStatus = "cancelled"
	RunCompleted  RunStatus = "completed"
	RunFailed     RunStatus = "failed"
	RunTimedOut   RunStatus = "timed_out"
)

type CreateRunParams struct {
	ID        string
	SessionID string
	TenantID  string
	UserID    string
	AgentID   int
	ProjectID *int
	Request   any
}

type AgentRun struct {
	ID                string
	SessionID         string
	TenantID          string
	UserID            string
	Status            RunStatus
	CancelRequestedAt *time.Time
	StartedAt         *time.Time
	FinishedAt        *time.Time
	ErrorMessage      string
	Attempt           int
	AgentID           int
	ProjectID         *int
	WorkerID          string
	DispatchAttempt   int
	DispatchError     string
	CreatedAt         time.Time
	UpdatedAt         time.Time
}

type AgentRunRequest struct {
	Message       string  `json:"message"`
	AttachmentIDs []int64 `json:"attachment_ids"`
	PlanMode      bool    `json:"plan_mode"`
	PlanAction    string  `json:"plan_action"`
}

type AgentRunDispatch struct {
	ID        string
	SessionID string
	TenantID  string
	UserID    string
	AgentID   int
	ProjectID *int
	Request   AgentRunRequest
}

type agentRunAttemptKey struct{}

type agentRunAttempt struct {
	RunID   string
	Attempt int
}

func WithAgentRunAttempt(ctx context.Context, runID string, attempt int) context.Context {
	if runID == "" || attempt <= 0 {
		return ctx
	}
	return context.WithValue(ctx, agentRunAttemptKey{}, agentRunAttempt{RunID: runID, Attempt: attempt})
}

func agentRunAttemptFromContext(ctx context.Context) (agentRunAttempt, bool) {
	run, ok := ctx.Value(agentRunAttemptKey{}).(agentRunAttempt)
	return run, ok && run.RunID != "" && run.Attempt > 0
}

func CreateAgentRun(ctx context.Context, db *sql.DB, p CreateRunParams) error {
	if db == nil {
		return nil
	}
	// 只有非 Temporal（直连）执行模式会走到这里——Temporal 模式走的是
	// CreateExclusiveAgentRun，那边的 SELECT ... FOR UPDATE 顺带查了
	// is_archived。这里没有事务/行锁，多一次单独查询：直连模式下这个函数
	// 是"每条新消息都会调一次"（见 agent_session.go 的 startRun），但部署
	// 现状（docker-compose.test-server.yml）固定用 Temporal 模式，这条路径
	// 只在本地开发/未来别的部署形态下才会被打到，正确性优先于这点开销。
	//
	// 查询本身失败（库抖动、连接超时……）时放行而不是拦下——这不是一道安全
	// 边界，挡不住的最坏后果只是一个继续存在的"幽灵会话"，不是数据泄漏给
	// 别人；而拦下的后果是一次基础设施抖动让用户一条正常消息发不出去。跟
	// attachmentsChanged/MergeAttachments 是同一条原则："查不到真相时保持
	// 现状，好过为了一次防御性检查把一段正常使用的对话变得打不开"。
	var archived bool
	if err := db.QueryRowContext(ctx,
		`SELECT COALESCE(is_archived, 0) FROM chat_sessions WHERE id = ?`, p.SessionID,
	).Scan(&archived); err != nil {
		if !errors.Is(err, sql.ErrNoRows) {
			log.Printf("session_archived_check_failed session=%s err=%v (跳过检查，照常建 run)", p.SessionID, err)
		}
	} else if archived {
		return ErrSessionArchived
	}
	return insertAgentRun(ctx, db, p)
}

type runExecer interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
}

func insertAgentRun(ctx context.Context, exec runExecer, p CreateRunParams) error {
	payload, err := json.Marshal(p.Request)
	if err != nil {
		return fmt.Errorf("marshal agent run request: %w", err)
	}
	_, err = exec.ExecContext(ctx,
		`INSERT INTO agent_runs
		    (id, session_id, tenant_id, user_id, agent_id, project_id, status, request_json)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
		p.ID, p.SessionID, p.TenantID, p.UserID, p.AgentID, p.ProjectID, RunQueued, string(payload),
	)
	if err != nil {
		return fmt.Errorf("create agent run: %w", err)
	}
	return nil
}

// CreateExclusiveAgentRun serializes submissions on the chat_sessions row. The Redis execution
// lock protects runners; this transaction protects the queued state before any runner owns it.
func CreateExclusiveAgentRun(ctx context.Context, db *sql.DB, p CreateRunParams) error {
	if db == nil {
		return errors.New("run store is disabled")
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin create agent run: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var sessionID string
	var archived bool
	if err := tx.QueryRowContext(ctx,
		`SELECT id, COALESCE(is_archived, 0) FROM chat_sessions WHERE id = ? FOR UPDATE`, p.SessionID,
	).Scan(&sessionID, &archived); err != nil {
		return fmt.Errorf("lock run session: %w", err)
	}
	if archived {
		return ErrSessionArchived
	}
	var activeID string
	err = tx.QueryRowContext(ctx,
		`SELECT id FROM agent_runs
		  WHERE session_id = ? AND status IN (?, ?, ?)
		  ORDER BY created_at DESC LIMIT 1`,
		p.SessionID, RunQueued, RunRunning, RunCancelling,
	).Scan(&activeID)
	if err == nil {
		return ErrActiveRun
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return fmt.Errorf("check active agent run: %w", err)
	}
	if err := insertAgentRun(ctx, tx, p); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit create agent run: %w", err)
	}
	return nil
}

func FailAgentRunSubmission(ctx context.Context, db *sql.DB, runID string, cause error) error {
	if db == nil {
		return nil
	}
	var message any
	if cause != nil {
		message = cause.Error()
	}
	_, err := db.ExecContext(ctx,
		`UPDATE agent_runs
		    SET status = ?, error_message = ?, finished_at = CURRENT_TIMESTAMP
		  WHERE id = ? AND status = ?`,
		RunFailed, message, runID, RunQueued,
	)
	if err != nil {
		return fmt.Errorf("fail agent run submission: %w", err)
	}
	return nil
}

func ListPendingAgentRunDispatches(ctx context.Context, db *sql.DB, limit int) ([]AgentRunDispatch, error) {
	if db == nil {
		return nil, nil
	}
	if limit <= 0 {
		limit = 100
	}
	rows, err := db.QueryContext(ctx,
		`SELECT id, session_id, tenant_id, user_id, agent_id, project_id, request_json
		   FROM agent_runs
		  WHERE status = ? AND dispatched_at IS NULL
		  ORDER BY created_at
		  LIMIT ?`,
		RunQueued, limit,
	)
	if err != nil {
		return nil, fmt.Errorf("list pending agent run dispatches: %w", err)
	}
	defer rows.Close()

	var dispatches []AgentRunDispatch
	for rows.Next() {
		var dispatch AgentRunDispatch
		var projectID sql.NullInt64
		var payload []byte
		if err := rows.Scan(&dispatch.ID, &dispatch.SessionID, &dispatch.TenantID, &dispatch.UserID,
			&dispatch.AgentID, &projectID, &payload); err != nil {
			return nil, fmt.Errorf("scan pending agent run dispatch: %w", err)
		}
		if projectID.Valid {
			value := int(projectID.Int64)
			dispatch.ProjectID = &value
		}
		if err := json.Unmarshal(payload, &dispatch.Request); err != nil {
			return nil, fmt.Errorf("decode agent run %s request: %w", dispatch.ID, err)
		}
		dispatches = append(dispatches, dispatch)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate pending agent run dispatches: %w", err)
	}
	return dispatches, nil
}

// MarkAgentRunDispatched 原子地把一行"待派发"标记成"已认领"。
//
// 返回值告诉调用方"是不是我抢到的"——这不是多余的信息：dispatchPending 的
// 周期轮询和 submitTemporalRun 的内联派发是同一个进程里两条独立的 goroutine，
// 仍然会并发看到同一行"还没派发"，WHERE 条件里的 dispatched_at IS NULL
// 保证只有一个 UPDATE 能改到这一行，另一个影响 0 行——调用方必须知道自己
// 是不是那"唯一一个"，才能决定要不要接着调用 Temporal 的 StartWorkflowExecution
// （不知道就会两边都调，纵使 WorkflowIDReusePolicy 挡住了重复建 workflow，
// 也已经构成了一次不必要的、可能触发限流/告警的额外 RPC）。
func MarkAgentRunDispatched(ctx context.Context, db *sql.DB, runID string) (bool, error) {
	if db == nil || runID == "" {
		return false, nil
	}
	res, err := db.ExecContext(ctx,
		`UPDATE agent_runs
		    SET dispatched_at = CURRENT_TIMESTAMP,
		        dispatch_attempt = dispatch_attempt + 1,
		        dispatch_error = NULL
		  WHERE id = ? AND status IN (?, ?) AND dispatched_at IS NULL`,
		runID, RunQueued, RunRunning,
	)
	if err != nil {
		return false, fmt.Errorf("mark agent run dispatched: %w", err)
	}
	n, err := res.RowsAffected()
	return n > 0, err
}

// UnmarkAgentRunDispatched 把"已认领"退回去，只在认领成功之后 StartWorkflowExecution
// 真的失败时才调用——认领和真正起 workflow 隔着一次网络往返，中间失败的话
// 这一行必须能被下一轮 dispatchPending 重新捡到，否则永久卡在"认领了但没有
// 真正起 workflow"这个死角，谁都不会再碰它。
func UnmarkAgentRunDispatched(ctx context.Context, db *sql.DB, runID string) error {
	if db == nil || runID == "" {
		return nil
	}
	_, err := db.ExecContext(ctx,
		`UPDATE agent_runs SET dispatched_at = NULL WHERE id = ?`,
		runID,
	)
	return err
}

func RecordAgentRunDispatchFailure(ctx context.Context, db *sql.DB, runID string, cause error) error {
	if db == nil || runID == "" {
		return nil
	}
	var message any
	if cause != nil {
		message = cause.Error()
	}
	_, err := db.ExecContext(ctx,
		`UPDATE agent_runs
		    SET dispatch_attempt = dispatch_attempt + 1, dispatch_error = ?
		  WHERE id = ? AND status = ? AND dispatched_at IS NULL`,
		message, runID, RunQueued,
	)
	if err != nil {
		return fmt.Errorf("record agent run dispatch failure: %w", err)
	}
	return nil
}

// FinalizeAgentRunAfterWorkflow reconciles a run when Temporal cannot receive the execution
// activity's normal completion, most commonly after a worker process dies. Terminal rows are not
// overwritten, so a late activity response cannot corrupt a completed run.
func FinalizeAgentRunAfterWorkflow(ctx context.Context, db *sql.DB, runID string, cancelled bool, errorMessage string) error {
	if db == nil || runID == "" {
		return nil
	}
	status := RunFailed
	if cancelled {
		status = RunCancelled
	}
	var message any
	if !cancelled && errorMessage != "" {
		message = errorMessage
	}
	_, err := db.ExecContext(ctx,
		`UPDATE agent_runs
		    SET status = ?, error_message = ?, finished_at = CURRENT_TIMESTAMP,
		        cancel_requested_at = CASE
		          WHEN ? = ? THEN COALESCE(cancel_requested_at, CURRENT_TIMESTAMP)
		          ELSE cancel_requested_at
		        END
		  WHERE id = ? AND status IN (?, ?, ?)`,
		status, message, status, RunCancelled, runID, RunQueued, RunRunning, RunCancelling,
	)
	if err != nil {
		return fmt.Errorf("finalize agent run after workflow: %w", err)
	}
	return nil
}

func FindActiveAgentRun(ctx context.Context, db *sql.DB, sessionID string) (AgentRun, error) {
	if db == nil || sessionID == "" {
		return AgentRun{}, sql.ErrNoRows
	}
	var run AgentRun
	err := db.QueryRowContext(ctx,
		`SELECT id, session_id, tenant_id, user_id, status
		   FROM agent_runs
		  WHERE session_id = ? AND status IN (?, ?, ?)
		  ORDER BY created_at DESC
		  LIMIT 1`,
		sessionID, RunQueued, RunRunning, RunCancelling,
	).Scan(&run.ID, &run.SessionID, &run.TenantID, &run.UserID, &run.Status)
	return run, err
}

func MarkAgentRunRunning(ctx context.Context, db *sql.DB, runID, workerID string, attempt int) error {
	if db == nil {
		return nil
	}
	if attempt <= 0 {
		return errors.New("mark agent run running: attempt must be positive")
	}
	res, err := db.ExecContext(ctx,
		`UPDATE agent_runs
		    SET status = ?, worker_id = ?, attempt = ?,
		        started_at = COALESCE(started_at, CURRENT_TIMESTAMP)
		  WHERE id = ? AND status IN (?, ?) AND attempt < ?`,
		RunRunning, workerID, attempt, runID, RunQueued, RunRunning, attempt,
	)
	if err != nil {
		return fmt.Errorf("mark agent run running: %w", err)
	}
	if n, _ := res.RowsAffected(); n == 0 {
		// 诊断性只读查询：只是为了把"当时那一行实际是什么状态"打进错误里，
		// 帮排查区分两种完全不同的病因——
		//   status 不在 (queued,running)   →  这行在被抢之前就已经被别的路径
		//                                      改走了（取消请求/超时收尾…）
		//   status 对但 attempt >= 当前值  →  真的有更早/更快的一次已经抢到手
		// 查失败不影响主流程，只是那种情况下诊断信息里留空。
		var curStatus, curWorker string
		var curAttempt int
		_ = db.QueryRowContext(ctx,
			`SELECT status, attempt, worker_id FROM agent_runs WHERE id = ?`, runID,
		).Scan(&curStatus, &curAttempt, &curWorker)
		return fmt.Errorf(
			"mark agent run running: run %s cannot claim attempt %d (current status=%s attempt=%d worker=%s): %w",
			runID, attempt, curStatus, curAttempt, curWorker, ErrCannotClaimRun,
		)
	}
	return nil
}

func FinishAgentRun(ctx context.Context, db *sql.DB, runID string, attempt int, status RunStatus, runErr error) error {
	if db == nil {
		return nil
	}
	if status != RunCompleted && status != RunCancelled && status != RunFailed && status != RunTimedOut {
		return fmt.Errorf("finish agent run: invalid terminal status %q", status)
	}
	var errorMessage any
	if runErr != nil {
		errorMessage = runErr.Error()
	}
	res, err := db.ExecContext(ctx,
		`UPDATE agent_runs
		    SET status = ?, error_message = ?, finished_at = CURRENT_TIMESTAMP
		  WHERE id = ? AND attempt = ? AND status IN (?, ?)`,
		status, errorMessage, runID, attempt, RunRunning, RunCancelling,
	)
	if err != nil {
		return fmt.Errorf("finish agent run: %w", err)
	}
	if n, _ := res.RowsAffected(); n == 0 {
		return fmt.Errorf("finish agent run: run %s is not active", runID)
	}
	return nil
}

func IsAgentRunAttemptCurrent(ctx context.Context, db *sql.DB, runID string, attempt int) (bool, error) {
	if db == nil || runID == "" || attempt <= 0 {
		return false, nil
	}
	var count int
	err := db.QueryRowContext(ctx,
		`SELECT COUNT(*) FROM agent_runs
		  WHERE id = ? AND attempt = ? AND status IN (?, ?)`,
		runID, attempt, RunRunning, RunCancelling,
	).Scan(&count)
	if err != nil {
		return false, fmt.Errorf("check agent run attempt: %w", err)
	}
	return count == 1, nil
}

type AgentRunCheckpoint struct {
	Attempt    int
	Checkpoint enginesessions.TurnCheckpoint
}

func LoadAgentRunCheckpoint(ctx context.Context, db *sql.DB, runID string) (AgentRunCheckpoint, error) {
	if db == nil || runID == "" {
		return AgentRunCheckpoint{}, sql.ErrNoRows
	}
	var checkpoint AgentRunCheckpoint
	var raw []byte
	err := db.QueryRowContext(ctx,
		`SELECT attempt, checkpoint_json FROM agent_run_checkpoints WHERE run_id = ?`, runID,
	).Scan(&checkpoint.Attempt, &raw)
	if err != nil {
		return AgentRunCheckpoint{}, err
	}
	if err := json.Unmarshal(raw, &checkpoint.Checkpoint); err != nil {
		return AgentRunCheckpoint{}, fmt.Errorf("decode agent run checkpoint: %w", err)
	}
	return checkpoint, nil
}

func ResetAgentRunHistoryForRetry(ctx context.Context, db *sql.DB, runID, sessionID, tenantID string) error {
	if db == nil || runID == "" {
		return nil
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin reset agent run history: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	if _, err := tx.ExecContext(ctx, `DELETE FROM agent_messages WHERE run_id = ?`, runID); err != nil {
		return fmt.Errorf("delete agent run history: %w", err)
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM agent_run_checkpoints WHERE run_id = ?`, runID); err != nil {
		return fmt.Errorf("delete agent run checkpoint: %w", err)
	}
	if _, err := tx.ExecContext(ctx,
		`DELETE FROM session_summaries WHERE session_id = ? AND tenant_id = ?`, sessionID, tenantID,
	); err != nil {
		return fmt.Errorf("delete agent run summary: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit reset agent run history: %w", err)
	}
	return nil
}

func GetAgentRunByID(ctx context.Context, db *sql.DB, runID string) (AgentRun, error) {
	if db == nil {
		return AgentRun{}, sql.ErrNoRows
	}
	var run AgentRun
	var errorMessage sql.NullString
	err := db.QueryRowContext(ctx,
		`SELECT id, session_id, tenant_id, user_id, status, attempt, error_message
		   FROM agent_runs WHERE id = ?`, runID,
	).Scan(&run.ID, &run.SessionID, &run.TenantID, &run.UserID, &run.Status, &run.Attempt, &errorMessage)
	if errorMessage.Valid {
		run.ErrorMessage = errorMessage.String
	}
	return run, err
}

func RequestAgentRunCancellation(ctx context.Context, db *sql.DB, runID string) (bool, error) {
	if db == nil || runID == "" {
		return false, nil
	}
	res, err := db.ExecContext(ctx,
		`UPDATE agent_runs
		    SET cancel_requested_at = COALESCE(cancel_requested_at, CURRENT_TIMESTAMP),
		        finished_at = CASE WHEN status = ? THEN CURRENT_TIMESTAMP ELSE finished_at END,
		        status = CASE WHEN status = ? THEN ? ELSE ? END
		  WHERE id = ? AND status IN (?, ?)`,
		RunQueued, RunQueued, RunCancelled, RunCancelling, runID, RunQueued, RunRunning,
	)
	if err != nil {
		return false, fmt.Errorf("request agent run cancellation: %w", err)
	}
	n, _ := res.RowsAffected()
	return n > 0, nil
}

func RequestActiveRunCancellation(ctx context.Context, db *sql.DB, sessionID string) (string, bool, error) {
	if db == nil || sessionID == "" {
		return "", false, nil
	}
	var runID string
	err := db.QueryRowContext(ctx,
		`SELECT id
		   FROM agent_runs
		  WHERE session_id = ? AND status IN (?, ?, ?)
		  ORDER BY created_at DESC
		  LIMIT 1`,
		sessionID, RunQueued, RunRunning, RunCancelling,
	).Scan(&runID)
	if errors.Is(err, sql.ErrNoRows) {
		return "", false, nil
	}
	if err != nil {
		return "", false, fmt.Errorf("find active agent run: %w", err)
	}
	var status RunStatus
	if err := db.QueryRowContext(ctx, `SELECT status FROM agent_runs WHERE id = ?`, runID).Scan(&status); err != nil {
		return "", false, fmt.Errorf("read active agent run status: %w", err)
	}
	if status == RunCancelling {
		return runID, true, nil
	}
	changed, err := RequestAgentRunCancellation(ctx, db, runID)
	return runID, changed, err
}

func IsAgentRunCancellationRequested(ctx context.Context, db *sql.DB, runID string) (bool, error) {
	if db == nil || runID == "" {
		return false, nil
	}
	var status RunStatus
	var requested sql.NullTime
	err := db.QueryRowContext(ctx,
		`SELECT status, cancel_requested_at FROM agent_runs WHERE id = ?`, runID,
	).Scan(&status, &requested)
	if err != nil {
		return false, fmt.Errorf("read agent run cancellation: %w", err)
	}
	return requested.Valid || status == RunCancelling || status == RunCancelled, nil
}

func GetAgentRun(ctx context.Context, db *sql.DB, runID, tenantID, userID string) (AgentRun, error) {
	if db == nil {
		return AgentRun{}, sql.ErrNoRows
	}
	var run AgentRun
	var cancelAt, startedAt, finishedAt sql.NullTime
	var errorMessage sql.NullString
	err := db.QueryRowContext(ctx,
		`SELECT id, session_id, tenant_id, user_id, status, cancel_requested_at,
		        started_at, finished_at, error_message, attempt
		   FROM agent_runs
		  WHERE id = ? AND tenant_id = ? AND user_id = ?`,
		runID, tenantID, userID,
	).Scan(&run.ID, &run.SessionID, &run.TenantID, &run.UserID, &run.Status,
		&cancelAt, &startedAt, &finishedAt, &errorMessage, &run.Attempt)
	if err != nil {
		return AgentRun{}, err
	}
	if cancelAt.Valid {
		run.CancelRequestedAt = &cancelAt.Time
	}
	if startedAt.Valid {
		run.StartedAt = &startedAt.Time
	}
	if finishedAt.Valid {
		run.FinishedAt = &finishedAt.Time
	}
	if errorMessage.Valid {
		run.ErrorMessage = errorMessage.String
	}
	return run, nil
}

type AdminRunFilter struct {
	TenantID string
	UserID   string
	Status   RunStatus
	AgentID  int
	Page     int
	Size     int
}

func scanAdminAgentRun(scanner interface{ Scan(...any) error }) (AgentRun, error) {
	var run AgentRun
	var projectID sql.NullInt64
	var cancelAt, startedAt, finishedAt sql.NullTime
	var errorMessage, dispatchError sql.NullString
	err := scanner.Scan(
		&run.ID, &run.SessionID, &run.TenantID, &run.UserID, &run.AgentID,
		&projectID, &run.Status, &run.WorkerID, &run.Attempt, &run.DispatchAttempt,
		&dispatchError, &cancelAt, &startedAt, &finishedAt, &errorMessage,
		&run.CreatedAt, &run.UpdatedAt,
	)
	if err != nil {
		return AgentRun{}, err
	}
	if projectID.Valid {
		value := int(projectID.Int64)
		run.ProjectID = &value
	}
	if cancelAt.Valid {
		run.CancelRequestedAt = &cancelAt.Time
	}
	if startedAt.Valid {
		run.StartedAt = &startedAt.Time
	}
	if finishedAt.Valid {
		run.FinishedAt = &finishedAt.Time
	}
	if errorMessage.Valid {
		run.ErrorMessage = errorMessage.String
	}
	if dispatchError.Valid {
		run.DispatchError = dispatchError.String
	}
	return run, nil
}

const adminRunColumns = `id, session_id, tenant_id, user_id, agent_id, project_id,
status, worker_id, attempt, dispatch_attempt, dispatch_error, cancel_requested_at,
started_at, finished_at, error_message, created_at, updated_at`

func ListAgentRunsForAdmin(ctx context.Context, db *sql.DB, filter AdminRunFilter) ([]AgentRun, int, error) {
	if db == nil {
		return nil, 0, errors.New("run store is disabled")
	}
	if filter.Page <= 0 {
		filter.Page = 1
	}
	if filter.Size <= 0 || filter.Size > 200 {
		filter.Size = 50
	}
	where := []string{"1=1"}
	args := []any{}
	if filter.TenantID != "" {
		where = append(where, "tenant_id = ?")
		args = append(args, filter.TenantID)
	}
	if filter.UserID != "" {
		where = append(where, "user_id = ?")
		args = append(args, filter.UserID)
	}
	if filter.Status != "" {
		where = append(where, "status = ?")
		args = append(args, filter.Status)
	}
	if filter.AgentID > 0 {
		where = append(where, "agent_id = ?")
		args = append(args, filter.AgentID)
	}
	clause := strings.Join(where, " AND ")
	var total int
	if err := db.QueryRowContext(ctx, "SELECT COUNT(*) FROM agent_runs WHERE "+clause, args...).Scan(&total); err != nil {
		return nil, 0, fmt.Errorf("count agent runs: %w", err)
	}
	queryArgs := append(append([]any{}, args...), filter.Size, (filter.Page-1)*filter.Size)
	rows, err := db.QueryContext(ctx,
		"SELECT "+adminRunColumns+" FROM agent_runs WHERE "+clause+" ORDER BY created_at DESC LIMIT ? OFFSET ?",
		queryArgs...,
	)
	if err != nil {
		return nil, 0, fmt.Errorf("list agent runs: %w", err)
	}
	defer rows.Close()
	result := make([]AgentRun, 0, filter.Size)
	for rows.Next() {
		run, err := scanAdminAgentRun(rows)
		if err != nil {
			return nil, 0, fmt.Errorf("scan agent run: %w", err)
		}
		result = append(result, run)
	}
	return result, total, rows.Err()
}

func GetAgentRunForAdmin(ctx context.Context, db *sql.DB, runID, tenantID string) (AgentRun, error) {
	if db == nil {
		return AgentRun{}, sql.ErrNoRows
	}
	query := "SELECT " + adminRunColumns + " FROM agent_runs WHERE id = ?"
	args := []any{runID}
	if tenantID != "" {
		query += " AND tenant_id = ?"
		args = append(args, tenantID)
	}
	return scanAdminAgentRun(db.QueryRowContext(ctx, query, args...))
}

func RetryAgentRunForAdmin(ctx context.Context, db *sql.DB, runID, tenantID string) (AgentRunDispatch, error) {
	if db == nil {
		return AgentRunDispatch{}, errors.New("run store is disabled")
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return AgentRunDispatch{}, fmt.Errorf("begin retry agent run: %w", err)
	}
	defer func() { _ = tx.Rollback() }()
	query := `SELECT id, session_id, tenant_id, user_id, agent_id, project_id, request_json, status
		FROM agent_runs WHERE id = ?`
	args := []any{runID}
	if tenantID != "" {
		query += " AND tenant_id = ?"
		args = append(args, tenantID)
	}
	if !strings.Contains(strings.ToLower(fmt.Sprintf("%T", db.Driver())), "sqlite") {
		query += " FOR UPDATE"
	}
	var dispatch AgentRunDispatch
	var projectID sql.NullInt64
	var requestJSON []byte
	var status RunStatus
	if err := tx.QueryRowContext(ctx, query, args...).Scan(
		&dispatch.ID, &dispatch.SessionID, &dispatch.TenantID, &dispatch.UserID,
		&dispatch.AgentID, &projectID, &requestJSON, &status,
	); err != nil {
		return AgentRunDispatch{}, err
	}
	if status != RunFailed && status != RunTimedOut && status != RunCancelled {
		return AgentRunDispatch{}, fmt.Errorf("run %s in status %s cannot be retried", runID, status)
	}
	var activeRunID string
	err = tx.QueryRowContext(ctx, `SELECT id FROM agent_runs
		WHERE session_id = ? AND status IN (?, ?, ?) LIMIT 1`,
		dispatch.SessionID, RunQueued, RunRunning, RunCancelling,
	).Scan(&activeRunID)
	if err == nil {
		return AgentRunDispatch{}, ErrActiveRun
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return AgentRunDispatch{}, fmt.Errorf("check active retry run: %w", err)
	}
	if projectID.Valid {
		value := int(projectID.Int64)
		dispatch.ProjectID = &value
	}
	if err := json.Unmarshal(requestJSON, &dispatch.Request); err != nil {
		return AgentRunDispatch{}, fmt.Errorf("decode retry request: %w", err)
	}
	dispatch.ID = uuid.NewString()
	if err := insertAgentRun(ctx, tx, CreateRunParams{
		ID:        dispatch.ID,
		SessionID: dispatch.SessionID,
		TenantID:  dispatch.TenantID,
		UserID:    dispatch.UserID,
		AgentID:   dispatch.AgentID,
		ProjectID: dispatch.ProjectID,
		Request:   dispatch.Request,
	}); err != nil {
		return AgentRunDispatch{}, fmt.Errorf("create retry agent run: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return AgentRunDispatch{}, fmt.Errorf("commit retry agent run: %w", err)
	}
	return dispatch, nil
}
