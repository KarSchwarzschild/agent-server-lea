package persistence

import (
	"context"
	"database/sql"
	"errors"
	"testing"

	_ "modernc.org/sqlite"
)

func testRunsDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	_, err = db.Exec(`CREATE TABLE agent_runs (
		id TEXT PRIMARY KEY,
		session_id TEXT NOT NULL,
		tenant_id TEXT NOT NULL,
		user_id TEXT NOT NULL,
		agent_id INTEGER NOT NULL DEFAULT 0,
		project_id INTEGER NULL,
		status TEXT NOT NULL,
		request_json TEXT NOT NULL,
		worker_id TEXT NOT NULL DEFAULT '',
		attempt INTEGER NOT NULL DEFAULT 0,
		dispatched_at DATETIME NULL,
		dispatch_attempt INTEGER NOT NULL DEFAULT 0,
		dispatch_error TEXT NULL,
		cancel_requested_at DATETIME NULL,
		started_at DATETIME NULL,
		finished_at DATETIME NULL,
		error_message TEXT NULL,
		created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
		updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
	)`)
	if err != nil {
		t.Fatalf("create agent_runs: %v", err)
	}
	t.Cleanup(func() { _ = db.Close() })
	return db
}

// withChatSessionsTable 给测试库补一张最小的 chat_sessions 表，只挂需要的两列——
// 默认的 testRunsDB 故意不带这张表，用来覆盖"is_archived 检查那条查询本身失败
// （表不存在/库抖动）时必须 fail-open，不能拦住一条正常消息"这个行为（见
// CreateAgentRun 里的说明）。只有明确要测"这行的 is_archived 到底生效没生效"
// 的用例才调用这个 helper，其余测试维持"表都不存在"的默认状态不变。
func withChatSessionsTable(t *testing.T, db *sql.DB) {
	t.Helper()
	_, err := db.Exec(`CREATE TABLE chat_sessions (
		id TEXT PRIMARY KEY,
		is_archived INTEGER NOT NULL DEFAULT 0
	)`)
	if err != nil {
		t.Fatalf("create chat_sessions: %v", err)
	}
}

func insertChatSession(t *testing.T, db *sql.DB, id string, archived bool) {
	t.Helper()
	archivedInt := 0
	if archived {
		archivedInt = 1
	}
	if _, err := db.Exec(`INSERT INTO chat_sessions (id, is_archived) VALUES (?, ?)`, id, archivedInt); err != nil {
		t.Fatalf("insert chat_sessions: %v", err)
	}
}

func createTestRun(t *testing.T, db *sql.DB, id string) {
	t.Helper()
	err := CreateAgentRun(context.Background(), db, CreateRunParams{
		ID: id, SessionID: "s1", TenantID: "t1", UserID: "u1",
		Request: map[string]any{"message": "hello"},
	})
	if err != nil {
		t.Fatalf("CreateAgentRun: %v", err)
	}
}

func TestAgentRunLifecycle(t *testing.T) {
	db := testRunsDB(t)
	ctx := context.Background()
	createTestRun(t, db, "r1")

	if err := MarkAgentRunRunning(ctx, db, "r1", "worker-a", 1); err != nil {
		t.Fatalf("MarkAgentRunRunning: %v", err)
	}
	if err := FinishAgentRun(ctx, db, "r1", 1, RunCompleted, nil); err != nil {
		t.Fatalf("FinishAgentRun: %v", err)
	}
	run, err := GetAgentRun(ctx, db, "r1", "t1", "u1")
	if err != nil {
		t.Fatalf("GetAgentRun: %v", err)
	}
	if run.Status != RunCompleted || run.StartedAt == nil || run.FinishedAt == nil {
		t.Fatalf("unexpected run: %+v", run)
	}
	if _, err := GetAgentRun(ctx, db, "r1", "t1", "other"); !errors.Is(err, sql.ErrNoRows) {
		t.Fatalf("ownership filter err=%v", err)
	}
}

func TestAdminRunListIsTenantScoped(t *testing.T) {
	db := testRunsDB(t)
	ctx := context.Background()
	for _, params := range []CreateRunParams{
		{ID: "t1-run", SessionID: "s1", TenantID: "t1", UserID: "u1", AgentID: 3, Request: AgentRunRequest{Message: "one"}},
		{ID: "t2-run", SessionID: "s2", TenantID: "t2", UserID: "u2", AgentID: 4, Request: AgentRunRequest{Message: "two"}},
	} {
		if err := insertAgentRun(ctx, db, params); err != nil {
			t.Fatal(err)
		}
	}
	runs, total, err := ListAgentRunsForAdmin(ctx, db, AdminRunFilter{TenantID: "t1", Page: 1, Size: 50})
	if err != nil {
		t.Fatal(err)
	}
	if total != 1 || len(runs) != 1 || runs[0].ID != "t1-run" {
		t.Fatalf("unexpected tenant-scoped runs: total=%d runs=%+v", total, runs)
	}
}

func TestAdminRetryCreatesNewRunAndPreservesFailure(t *testing.T) {
	db := testRunsDB(t)
	ctx := context.Background()
	if err := insertAgentRun(ctx, db, CreateRunParams{
		ID: "failed-run", SessionID: "s1", TenantID: "t1", UserID: "u1", AgentID: 7,
		Request: AgentRunRequest{Message: "retry me", AttachmentIDs: []int64{9}},
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`UPDATE agent_runs SET status = ?, error_message = 'boom' WHERE id = ?`, RunFailed, "failed-run"); err != nil {
		t.Fatal(err)
	}
	dispatch, err := RetryAgentRunForAdmin(ctx, db, "failed-run", "t1")
	if err != nil {
		t.Fatal(err)
	}
	if dispatch.ID == "failed-run" || dispatch.Request.Message != "retry me" {
		t.Fatalf("unexpected retry dispatch: %+v", dispatch)
	}
	var originalStatus, retryStatus RunStatus
	if err := db.QueryRow(`SELECT status FROM agent_runs WHERE id = ?`, "failed-run").Scan(&originalStatus); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRow(`SELECT status FROM agent_runs WHERE id = ?`, dispatch.ID).Scan(&retryStatus); err != nil {
		t.Fatal(err)
	}
	if originalStatus != RunFailed || retryStatus != RunQueued {
		t.Fatalf("original=%s retry=%s", originalStatus, retryStatus)
	}
}

func TestAdminRetryRejectsConcurrentSessionRun(t *testing.T) {
	db := testRunsDB(t)
	ctx := context.Background()
	for _, params := range []CreateRunParams{
		{ID: "failed-run", SessionID: "s1", TenantID: "t1", UserID: "u1", AgentID: 7, Request: AgentRunRequest{Message: "retry me"}},
		{ID: "active-run", SessionID: "s1", TenantID: "t1", UserID: "u1", AgentID: 7, Request: AgentRunRequest{Message: "active"}},
	} {
		if err := insertAgentRun(ctx, db, params); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := db.Exec(`UPDATE agent_runs SET status = ? WHERE id = ?`, RunFailed, "failed-run"); err != nil {
		t.Fatal(err)
	}
	if _, err := RetryAgentRunForAdmin(ctx, db, "failed-run", "t1"); !errors.Is(err, ErrActiveRun) {
		t.Fatalf("expected ErrActiveRun, got %v", err)
	}
}

func TestAgentRunCancellation(t *testing.T) {
	db := testRunsDB(t)
	ctx := context.Background()
	createTestRun(t, db, "r1")
	if err := MarkAgentRunRunning(ctx, db, "r1", "worker-a", 1); err != nil {
		t.Fatal(err)
	}

	runID, changed, err := RequestActiveRunCancellation(ctx, db, "s1")
	if err != nil || !changed || runID != "r1" {
		t.Fatalf("RequestActiveRunCancellation run=%q changed=%v err=%v", runID, changed, err)
	}
	requested, err := IsAgentRunCancellationRequested(ctx, db, "r1")
	if err != nil || !requested {
		t.Fatalf("IsAgentRunCancellationRequested requested=%v err=%v", requested, err)
	}
	if err := FinishAgentRun(ctx, db, "r1", 1, RunCancelled, context.Canceled); err != nil {
		t.Fatalf("FinishAgentRun cancelled: %v", err)
	}
}

func TestQueuedAgentRunCancellationIsTerminal(t *testing.T) {
	db := testRunsDB(t)
	ctx := context.Background()
	createTestRun(t, db, "queued")
	changed, err := RequestAgentRunCancellation(ctx, db, "queued")
	if err != nil || !changed {
		t.Fatalf("RequestAgentRunCancellation changed=%v err=%v", changed, err)
	}
	run, err := GetAgentRun(ctx, db, "queued", "t1", "u1")
	if err != nil || run.Status != RunCancelled || run.FinishedAt == nil || run.CancelRequestedAt == nil {
		t.Fatalf("queued cancellation did not become terminal: %+v err=%v", run, err)
	}
}

func TestFinalizeAgentRunAfterWorkflow(t *testing.T) {
	db := testRunsDB(t)
	ctx := context.Background()
	createTestRun(t, db, "failed")
	if err := MarkAgentRunRunning(ctx, db, "failed", "worker-a", 1); err != nil {
		t.Fatal(err)
	}
	if err := FinalizeAgentRunAfterWorkflow(ctx, db, "failed", false, "heartbeat timeout"); err != nil {
		t.Fatal(err)
	}
	run, err := GetAgentRun(ctx, db, "failed", "t1", "u1")
	if err != nil || run.Status != RunFailed || run.ErrorMessage != "heartbeat timeout" {
		t.Fatalf("unexpected failed run: %+v err=%v", run, err)
	}

	createTestRun(t, db, "cancelled")
	if err := FinalizeAgentRunAfterWorkflow(ctx, db, "cancelled", true, "workflow canceled"); err != nil {
		t.Fatal(err)
	}
	run, err = GetAgentRun(ctx, db, "cancelled", "t1", "u1")
	if err != nil || run.Status != RunCancelled || run.CancelRequestedAt == nil {
		t.Fatalf("unexpected cancelled run: %+v err=%v", run, err)
	}

	createTestRun(t, db, "completed")
	if err := MarkAgentRunRunning(ctx, db, "completed", "worker-a", 1); err != nil {
		t.Fatal(err)
	}
	if err := FinishAgentRun(ctx, db, "completed", 1, RunCompleted, nil); err != nil {
		t.Fatal(err)
	}
	if err := FinalizeAgentRunAfterWorkflow(ctx, db, "completed", false, "late failure"); err != nil {
		t.Fatal(err)
	}
	run, err = GetAgentRun(ctx, db, "completed", "t1", "u1")
	if err != nil || run.Status != RunCompleted {
		t.Fatalf("terminal run was overwritten: %+v err=%v", run, err)
	}
}

func TestAgentRunDispatchOutbox(t *testing.T) {
	db := testRunsDB(t)
	ctx := context.Background()
	createTestRun(t, db, "r1")

	dispatches, err := ListPendingAgentRunDispatches(ctx, db, 10)
	if err != nil || len(dispatches) != 1 || dispatches[0].Request.Message != "hello" {
		t.Fatalf("ListPendingAgentRunDispatches: %+v err=%v", dispatches, err)
	}
	if err := RecordAgentRunDispatchFailure(ctx, db, "r1", errors.New("temporal unavailable")); err != nil {
		t.Fatal(err)
	}
	if claimed, err := MarkAgentRunDispatched(ctx, db, "r1"); err != nil || !claimed {
		t.Fatalf("MarkAgentRunDispatched: claimed=%v err=%v", claimed, err)
	}
	dispatches, err = ListPendingAgentRunDispatches(ctx, db, 10)
	if err != nil || len(dispatches) != 0 {
		t.Fatalf("dispatched run still pending: %+v err=%v", dispatches, err)
	}
}

// TestMarkAgentRunDispatchedIsExclusive 是这次修复要保住的不变式：两条并发路径
// （agent 自己的周期轮询 + submitTemporalRun 的内联派发）看到同一行"待派发"时，
// 只能有一个真的去起 workflow，另一个必须拿到 claimed=false 而不是也去调
// StartWorkflowExecution——这正是"同一条消息被派发成两个独立执行互相打架"
// 那次事故的根因，回归它比回归别的都重要。
func TestMarkAgentRunDispatchedIsExclusive(t *testing.T) {
	db := testRunsDB(t)
	ctx := context.Background()
	createTestRun(t, db, "r1")

	firstClaimed, err := MarkAgentRunDispatched(ctx, db, "r1")
	if err != nil || !firstClaimed {
		t.Fatalf("first claim should succeed: claimed=%v err=%v", firstClaimed, err)
	}
	secondClaimed, err := MarkAgentRunDispatched(ctx, db, "r1")
	if err != nil || secondClaimed {
		t.Fatalf("second concurrent claim must fail: claimed=%v err=%v", secondClaimed, err)
	}

	// Unmark 之后应该能重新认领——对应"起 workflow 失败，退回认领标记，
	// 下一轮 dispatchPending 重新捡回来"这条路径。
	if err := UnmarkAgentRunDispatched(ctx, db, "r1"); err != nil {
		t.Fatal(err)
	}
	thirdClaimed, err := MarkAgentRunDispatched(ctx, db, "r1")
	if err != nil || !thirdClaimed {
		t.Fatalf("claim after unmark should succeed: claimed=%v err=%v", thirdClaimed, err)
	}
}

func TestAgentRunAttemptFencing(t *testing.T) {
	db := testRunsDB(t)
	ctx := context.Background()
	createTestRun(t, db, "r1")
	if err := MarkAgentRunRunning(ctx, db, "r1", "worker-a", 1); err != nil {
		t.Fatal(err)
	}
	if err := MarkAgentRunRunning(ctx, db, "r1", "worker-b", 2); err != nil {
		t.Fatal(err)
	}
	current, err := IsAgentRunAttemptCurrent(ctx, db, "r1", 1)
	if err != nil || current {
		t.Fatalf("attempt 1 current=%v err=%v", current, err)
	}
	current, err = IsAgentRunAttemptCurrent(ctx, db, "r1", 2)
	if err != nil || !current {
		t.Fatalf("attempt 2 current=%v err=%v", current, err)
	}
	if err := FinishAgentRun(ctx, db, "r1", 1, RunCompleted, nil); err == nil {
		t.Fatal("stale attempt unexpectedly finished run")
	}
	if err := FinishAgentRun(ctx, db, "r1", 2, RunCompleted, nil); err != nil {
		t.Fatal(err)
	}
}

func TestResetAgentRunHistoryForRetry(t *testing.T) {
	db := testRunsDB(t)
	ctx := context.Background()
	_, err := db.Exec(`CREATE TABLE agent_messages (
		id INTEGER PRIMARY KEY,
		run_id TEXT NULL,
		run_attempt INTEGER NULL
	); CREATE TABLE session_summaries (
		session_id TEXT NOT NULL,
		tenant_id TEXT NOT NULL
	); CREATE TABLE agent_run_checkpoints (
		run_id TEXT PRIMARY KEY,
		attempt INTEGER NOT NULL,
		checkpoint_json TEXT NOT NULL
	)`)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`INSERT INTO agent_messages (id, run_id, run_attempt) VALUES
		(1, 'r1', 1), (2, 'r1', 2), (3, 'other', 1);
		INSERT INTO session_summaries (session_id, tenant_id) VALUES ('s1', 't1');
		INSERT INTO agent_run_checkpoints (run_id, attempt, checkpoint_json)
		VALUES ('r1', 1, '{"phase":"tools_committed","iteration":2}')`)
	if err != nil {
		t.Fatal(err)
	}
	checkpoint, err := LoadAgentRunCheckpoint(ctx, db, "r1")
	if err != nil || checkpoint.Attempt != 1 || checkpoint.Checkpoint.Iteration != 2 {
		t.Fatalf("LoadAgentRunCheckpoint: %+v err=%v", checkpoint, err)
	}
	if err := ResetAgentRunHistoryForRetry(ctx, db, "r1", "s1", "t1"); err != nil {
		t.Fatal(err)
	}
	var stale, current, other, summaries, checkpoints int
	_ = db.QueryRow(`SELECT COUNT(*) FROM agent_messages WHERE id = 1`).Scan(&stale)
	_ = db.QueryRow(`SELECT COUNT(*) FROM agent_messages WHERE id = 2`).Scan(&current)
	_ = db.QueryRow(`SELECT COUNT(*) FROM agent_messages WHERE id = 3`).Scan(&other)
	_ = db.QueryRow(`SELECT COUNT(*) FROM session_summaries`).Scan(&summaries)
	_ = db.QueryRow(`SELECT COUNT(*) FROM agent_run_checkpoints`).Scan(&checkpoints)
	if stale != 0 || current != 0 || other != 1 || summaries != 0 || checkpoints != 0 {
		t.Fatalf("unexpected reset result stale=%d current=%d other=%d summaries=%d checkpoints=%d",
			stale, current, other, summaries, checkpoints)
	}
}

func TestFindActiveAgentRun(t *testing.T) {
	db := testRunsDB(t)
	createTestRun(t, db, "r1")
	run, err := FindActiveAgentRun(context.Background(), db, "s1")
	if err != nil || run.ID != "r1" || run.Status != RunQueued {
		t.Fatalf("FindActiveAgentRun: %+v err=%v", run, err)
	}
}

// ── is_archived 检查（"删除会话"另一个标签页还能继续用"那个 bug）─────────────

func TestCreateAgentRun_RejectsArchivedSession(t *testing.T) {
	db := testRunsDB(t)
	withChatSessionsTable(t, db)
	insertChatSession(t, db, "s1", true)

	err := CreateAgentRun(context.Background(), db, CreateRunParams{
		ID: "r1", SessionID: "s1", TenantID: "t1", UserID: "u1",
		Request: map[string]any{"message": "hello"},
	})
	if !errors.Is(err, ErrSessionArchived) {
		t.Fatalf("want ErrSessionArchived, got %v", err)
	}
}

func TestCreateAgentRun_AllowsNonArchivedSession(t *testing.T) {
	db := testRunsDB(t)
	withChatSessionsTable(t, db)
	insertChatSession(t, db, "s1", false)

	if err := CreateAgentRun(context.Background(), db, CreateRunParams{
		ID: "r1", SessionID: "s1", TenantID: "t1", UserID: "u1",
		Request: map[string]any{"message": "hello"},
	}); err != nil {
		t.Fatalf("CreateAgentRun: %v", err)
	}
}

// 没有 chat_sessions 表（testRunsDB 的默认状态，模拟"查询本身失败"）时必须
// fail-open——这不是一道安全边界，查不到真相时拦住一条正常消息比放行的代价
// 更大，见 CreateAgentRun 里的说明。这条测试本身也顺带证明了这份文件里其余
// 几十个既有测试为什么在这次改动前后都不用碰：它们全部依赖这个"表不存在也
// 放行"的行为。
func TestCreateAgentRun_FailsOpenWhenArchivedCheckErrors(t *testing.T) {
	db := testRunsDB(t) // 故意不调 withChatSessionsTable
	if err := CreateAgentRun(context.Background(), db, CreateRunParams{
		ID: "r1", SessionID: "s1", TenantID: "t1", UserID: "u1",
		Request: map[string]any{"message": "hello"},
	}); err != nil {
		t.Fatalf("CreateAgentRun should fail open when the archived check itself errors: %v", err)
	}
}

// CreateExclusiveAgentRun 用了 `SELECT ... FOR UPDATE`（MySQL 语法），这份测试
// 用的 SQLite 驱动不支持这个子句——这不是这次改动引入的缺口，是这个函数从一开始
// 就没能被这份 SQLite 测试库覆盖过（真实的行锁语义本来也测不出多进程竞态）。
// is_archived 检查在这条路径上的正确性，靠上面 CreateAgentRun 的等价测试
// 加上 TestMarkAgentRunDispatchedIsExclusive 那类针对并发原语本身的测试来间接
// 覆盖；这个函数的完整行为需要一次真实的 MySQL 集成测试，不在这个文件的范围内。
