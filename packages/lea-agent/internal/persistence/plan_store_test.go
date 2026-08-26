package persistence

import (
	"context"
	"database/sql"
	"testing"

	_ "modernc.org/sqlite" // registers the pure-Go "sqlite" database/sql driver
)

// planDDL 跟 migrations/009 之后的表结构对齐。SQLite 的写法跟 MySQL 有差别
// （没有 ENGINE/CHARSET，AUTOINCREMENT 拼法不同），但 store 那几条 SQL 是两边
// 都跑得通的同一条 —— 这正是当初不用 ON DUPLICATE KEY UPDATE 的原因。
const planDDL = `CREATE TABLE IF NOT EXISTS session_plans (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	session_id          TEXT NOT NULL,
	tenant_id           TEXT NOT NULL DEFAULT '',
	approved_content    TEXT NULL,
	approved_steps_json TEXT NULL,
	draft_content       TEXT NULL,
	draft_steps_json    TEXT NULL,
	created_at TEXT DEFAULT (datetime('now')),
	updated_at TEXT DEFAULT (datetime('now')),
	UNIQUE (tenant_id, session_id)
)`

// loadApproved / loadDraft 是测试里的便捷读法。LoadPlan 一次返回四样东西，而多数
// 用例只关心其中一样。
func loadApproved(ctx context.Context, db *sql.DB, tenant, session string) (string, error) {
	p, err := LoadPlan(ctx, db, tenant, session)
	return p.Approved, err
}

func loadDraft(ctx context.Context, db *sql.DB, tenant, session string) (string, error) {
	p, err := LoadPlan(ctx, db, tenant, session)
	return p.Draft, err
}

// steps 把标题列表变成全 pending 的步骤 —— 跟 save_plan 工具提交时的形状一致。
func steps(titles ...string) []PlanStep {
	out := make([]PlanStep, 0, len(titles))
	for _, t := range titles {
		out = append(out, PlanStep{Title: t, Status: StepPending})
	}
	return out
}

func testPlanDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if _, err := db.Exec(planDDL); err != nil {
		t.Fatalf("create table: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

const (
	testTenant  = "t1"
	testSession = "s1"
)

func approved(t *testing.T, db *sql.DB) string {
	t.Helper()
	got, err := loadApproved(context.Background(), db, testTenant, testSession)
	if err != nil {
		t.Fatalf("LoadApprovedPlan: %v", err)
	}
	return got
}

func draft(t *testing.T, db *sql.DB) string {
	t.Helper()
	got, err := loadDraft(context.Background(), db, testTenant, testSession)
	if err != nil {
		t.Fatalf("LoadDraftPlan: %v", err)
	}
	return got
}

func saveDraft(t *testing.T, db *sql.DB, content string, st ...PlanStep) {
	t.Helper()
	if err := SaveDraftPlan(context.Background(), db, testTenant, testSession, content, st); err != nil {
		t.Fatalf("SaveDraftPlan(%q): %v", content, err)
	}
}

func approve(t *testing.T, db *sql.DB) {
	t.Helper()
	if err := ApprovePlan(context.Background(), db, testTenant, testSession); err != nil {
		t.Fatalf("ApprovePlan: %v", err)
	}
}

// ── 基本流转 ──────────────────────────────────────────────────────────────

// 待批准的计划不参与注入。批准之前模型不该看到它，否则用户还没点同意就已经在
// 按新计划跑了。
func TestPlanStore_DraftIsNotApproved(t *testing.T) {
	db := testPlanDB(t)
	saveDraft(t, db, "计划正文")

	if got := approved(t, db); got != "" {
		t.Fatalf("没批准就成了已批准: %q", got)
	}
	if got := draft(t, db); got != "计划正文" {
		t.Fatalf("draft 没存进去: %q", got)
	}
}

func TestPlanStore_ApproveMovesDraftToApproved(t *testing.T) {
	db := testPlanDB(t)
	saveDraft(t, db, "计划正文")
	approve(t, db)

	if got := approved(t, db); got != "计划正文" {
		t.Fatalf("批准后没成为已批准: %q", got)
	}
	// draft 必须清空，否则下一次 approve 会把同一份内容再"转正"一遍，
	// 而更糟的是 reject 会以为还有待批准的东西要否。
	if got := draft(t, db); got != "" {
		t.Fatalf("批准后 draft 没清: %q", got)
	}
}

func TestPlanStore_NoPlanReturnsEmpty(t *testing.T) {
	db := testPlanDB(t)
	if got := approved(t, db); got != "" {
		t.Fatalf("凭空冒出已批准的计划: %q", got)
	}
	if got := draft(t, db); got != "" {
		t.Fatalf("凭空冒出待批准的计划: %q", got)
	}
}

// ── 这一组是 009 存在的理由 ───────────────────────────────────────────────

// 执行中途提交修订版时，**已批准的那份必须原样留着**。
//
// 008 的形状（单列 content + status，REPLACE INTO 重置为 draft）在这里会把 v1
// 直接删掉：用户还没表态，正在执行的计划就没了。实测过：
//
//	批准后        approved = "计划 v1"
//	提交 v2 之后  approved = ""        ← 就是这一格
func TestPlanStore_ReviseKeepsApprovedUntilItIsApproved(t *testing.T) {
	db := testPlanDB(t)
	saveDraft(t, db, "计划 v1")
	approve(t, db)

	saveDraft(t, db, "计划 v2") // 执行中途的修订版

	if got := approved(t, db); got != "计划 v1" {
		t.Fatalf("提交修订版把已批准的计划弄丢了: %q", got)
	}
	if got := draft(t, db); got != "计划 v2" {
		t.Fatalf("修订版没存进去: %q", got)
	}
}

// 用户否决修订版 → 回到原计划继续执行，而不是两份都没有。
//
// 这条挂了的表现最阴：执行阶段一份计划都不注入，模型继续跑但不再有约束，
// 而且没有任何报错 —— 用户的本意是"别改了，按原来的走"。
func TestPlanStore_RejectKeepsApprovedPlan(t *testing.T) {
	db := testPlanDB(t)
	saveDraft(t, db, "计划 v1")
	approve(t, db)
	saveDraft(t, db, "计划 v2")

	if err := RejectPlan(context.Background(), db, testTenant, testSession); err != nil {
		t.Fatalf("RejectPlan: %v", err)
	}

	if got := approved(t, db); got != "计划 v1" {
		t.Fatalf("否决修订版把原计划也否了: %q", got)
	}
	if got := draft(t, db); got != "" {
		t.Fatalf("否决之后修订版还在: %q", got)
	}
}

// 批准修订版 → 新的取代旧的。
func TestPlanStore_ApproveRevisionReplacesApproved(t *testing.T) {
	db := testPlanDB(t)
	saveDraft(t, db, "计划 v1")
	approve(t, db)
	saveDraft(t, db, "计划 v2")
	approve(t, db)

	if got := approved(t, db); got != "计划 v2" {
		t.Fatalf("修订版批准后没生效: %q", got)
	}
}

// 没有待批准的计划时 approve 是 no-op —— 尤其**不能**拿 NULL 把已批准的那份盖掉。
// 用户重复点"同意"、前端重发、多副本同时处理同一条消息，走的都是这一支。
func TestPlanStore_ApproveWithNoDraftDoesNotWipeApproved(t *testing.T) {
	db := testPlanDB(t)
	saveDraft(t, db, "计划正文")
	approve(t, db)

	approve(t, db) // 再点一次
	approve(t, db) // 再来

	if got := approved(t, db); got != "计划正文" {
		t.Fatalf("重复 approve 把计划擦掉了: %q", got)
	}
}

func TestPlanStore_ApproveWithNothingAtAll(t *testing.T) {
	db := testPlanDB(t)
	approve(t, db) // 连行都没有
	if got := approved(t, db); got != "" {
		t.Fatalf("凭空冒出一份计划: %q", got)
	}
}

// ── SaveDraftPlan 的 upsert 路径 ──────────────────────────────────────────

// 连续存同样的内容。MySQL 的 RowsAffected 数的是**改动**行数，所以第二次 UPDATE
// 会报 0 行，走到 INSERT 撞唯一键那条补偿路径上。这条钉住那条路径能收敛且不报错。
func TestPlanStore_SaveDraftIdenticalContentTwice(t *testing.T) {
	db := testPlanDB(t)
	saveDraft(t, db, "一模一样")
	saveDraft(t, db, "一模一样")

	if got := draft(t, db); got != "一模一样" {
		t.Fatalf("重复保存之后 draft 不对: %q", got)
	}

	var n int
	if err := db.QueryRow(`SELECT COUNT(*) FROM session_plans`).Scan(&n); err != nil {
		t.Fatalf("count: %v", err)
	}
	if n != 1 {
		t.Fatalf("一个会话应当只有一行，实际 %d 行", n)
	}
}

// ── 清理与隔离 ────────────────────────────────────────────────────────────

func TestPlanStore_ClearRemovesBoth(t *testing.T) {
	db := testPlanDB(t)
	saveDraft(t, db, "计划 v1")
	approve(t, db)
	saveDraft(t, db, "计划 v2")

	if err := ClearPlan(context.Background(), db, testTenant, testSession); err != nil {
		t.Fatalf("ClearPlan: %v", err)
	}

	if got := approved(t, db); got != "" {
		t.Fatalf("清理后已批准的还在: %q", got)
	}
	if got := draft(t, db); got != "" {
		t.Fatalf("清理后待批准的还在: %q", got)
	}
}

// 计划正文会被原样拼进模型上下文，所以一个泄漏的 session_id 不能成为读到别人计划
// 的充分条件 —— 每条查询都必须同时过滤租户。
func TestPlanStore_TenantIsolation(t *testing.T) {
	db := testPlanDB(t)
	ctx := context.Background()

	if err := SaveDraftPlan(ctx, db, "tenantA", "same-session-id", "A 的计划", nil); err != nil {
		t.Fatalf("save A: %v", err)
	}
	if err := SaveDraftPlan(ctx, db, "tenantB", "same-session-id", "B 的计划", nil); err != nil {
		t.Fatalf("save B: %v", err)
	}
	if err := ApprovePlan(ctx, db, "tenantA", "same-session-id"); err != nil {
		t.Fatalf("approve A: %v", err)
	}

	gotA, _ := loadApproved(ctx, db, "tenantA", "same-session-id")
	if gotA != "A 的计划" {
		t.Fatalf("A 读到的不是自己的: %q", gotA)
	}
	// A 的批准动作不能顺手把 B 的也批了
	gotB, _ := loadApproved(ctx, db, "tenantB", "same-session-id")
	if gotB != "" {
		t.Fatalf("A 的 approve 影响了 B: %q", gotB)
	}
	gotBDraft, _ := loadDraft(ctx, db, "tenantB", "same-session-id")
	if gotBDraft != "B 的计划" {
		t.Fatalf("B 的 draft 被动了: %q", gotBDraft)
	}
}

// ── 结构化步骤（migrations/010）─────────────────────────────────────────────

// 步骤跟着正文一起从 draft 搬到 approved。分开搬就会出现"正文是 v2、进度是 v1"
// 这种没人能解释的状态。
func TestPlanStore_ApproveCarriesSteps(t *testing.T) {
	db := testPlanDB(t)
	saveDraft(t, db, "计划正文", steps("调研", "改代码", "补测试")...)

	// 批准之前步骤挂在 draft 上
	p, _ := LoadPlan(context.Background(), db, testTenant, testSession)
	if len(p.ApprovedSteps) != 0 {
		t.Fatalf("没批准就有已批准的步骤: %+v", p.ApprovedSteps)
	}
	if len(p.DraftSteps) != 3 {
		t.Fatalf("draft 步骤数不对: %+v", p.DraftSteps)
	}

	approve(t, db)

	p, _ = LoadPlan(context.Background(), db, testTenant, testSession)
	if len(p.ApprovedSteps) != 3 {
		t.Fatalf("批准后步骤没搬过来: %+v", p.ApprovedSteps)
	}
	if len(p.DraftSteps) != 0 {
		t.Fatalf("批准后 draft 步骤没清: %+v", p.DraftSteps)
	}
	if p.ApprovedSteps[0].Title != "调研" || p.ApprovedSteps[0].Status != StepPending {
		t.Fatalf("步骤内容不对: %+v", p.ApprovedSteps[0])
	}
}

func TestPlanStore_UpdateStepStatus(t *testing.T) {
	db := testPlanDB(t)
	ctx := context.Background()
	saveDraft(t, db, "计划正文", steps("调研", "改代码", "补测试")...)
	approve(t, db)

	p, err := UpdateStepStatus(ctx, db, testTenant, testSession, 2, StepCompleted)
	if err != nil {
		t.Fatalf("UpdateStepStatus: %v", err)
	}
	if p.ApprovedSteps[1].Status != StepCompleted {
		t.Fatalf("第 2 步没标上: %+v", p.ApprovedSteps)
	}
	// 别的步骤不能被顺手改掉
	if p.ApprovedSteps[0].Status != StepPending || p.ApprovedSteps[2].Status != StepPending {
		t.Fatalf("改一步动了别的: %+v", p.ApprovedSteps)
	}

	// 落库了才算数 —— 下一轮（可能在另一个 pod）读的是库
	reloaded, _ := LoadPlan(ctx, db, testTenant, testSession)
	if reloaded.ApprovedSteps[1].Status != StepCompleted {
		t.Fatalf("状态没落库: %+v", reloaded.ApprovedSteps)
	}
}

// 序号从 1 开始，且越界要报错而不是静默改错一步或 panic。
//
// 错误原文会回给模型，它下一轮能自己纠正 —— 所以越界必须是个能读懂的错误，
// 而不是"成功了"。
func TestPlanStore_UpdateStepRejectsBadInput(t *testing.T) {
	db := testPlanDB(t)
	ctx := context.Background()
	saveDraft(t, db, "计划正文", steps("一", "二")...)
	approve(t, db)

	for _, c := range []struct {
		name   string
		index  int
		status string
	}{
		{"序号 0（从 1 开始）", 0, StepCompleted},
		{"序号超出范围", 3, StepCompleted},
		{"负数", -1, StepCompleted},
		{"状态词拼错", 1, "complete"},
		{"状态词是空串", 1, ""},
	} {
		t.Run(c.name, func(t *testing.T) {
			if _, err := UpdateStepStatus(ctx, db, testTenant, testSession, c.index, c.status); err == nil {
				t.Fatal("应当报错")
			}
		})
	}

	// 上面那些失败都不能改动库里的状态
	p, _ := LoadPlan(ctx, db, testTenant, testSession)
	for i, s := range p.ApprovedSteps {
		if s.Status != StepPending {
			t.Fatalf("失败的调用改了第 %d 步: %+v", i+1, s)
		}
	}
}

// 没有已批准计划时标进度要报错 —— 模型在计划批准前就开始标步骤，说明它理解错了
// 当前状态，一个明确的错误比静默成功有用。
func TestPlanStore_UpdateStepWithoutApprovedPlan(t *testing.T) {
	db := testPlanDB(t)
	ctx := context.Background()
	saveDraft(t, db, "计划正文", steps("一")...) // 只提交，没批准

	if _, err := UpdateStepStatus(ctx, db, testTenant, testSession, 1, StepCompleted); err == nil {
		t.Fatal("计划还没批准就允许标进度了")
	}
}

func TestPlan_ProgressAndCurrentStep(t *testing.T) {
	p := Plan{ApprovedSteps: []PlanStep{
		{Title: "一", Status: StepCompleted},
		{Title: "二", Status: StepInProgress},
		{Title: "三", Status: StepPending},
	}}
	if done, total := p.Progress(); done != 1 || total != 3 {
		t.Fatalf("进度不对: %d/%d", done, total)
	}
	if got := p.CurrentStep(); got != "二" {
		t.Fatalf("当前步骤应当是进行中的那个: %q", got)
	}

	// 没有 in_progress 时退回第一个 pending —— 模型忘了标 in_progress 是常态，
	// 那时"下一件该做的事"比"什么都不说"有用。
	p2 := Plan{ApprovedSteps: []PlanStep{
		{Title: "一", Status: StepCompleted},
		{Title: "二", Status: StepPending},
	}}
	if got := p2.CurrentStep(); got != "二" {
		t.Fatalf("应当退回第一个 pending: %q", got)
	}

	// 全做完了就没有"当前"了
	p3 := Plan{ApprovedSteps: []PlanStep{{Title: "一", Status: StepCompleted}}}
	if got := p3.CurrentStep(); got != "" {
		t.Fatalf("全完成时不该有当前步骤: %q", got)
	}
}

// 库里存着一份坏 JSON 不该让整个会话不可用。退化成"这个计划没有步骤"是能继续跑的
// 降级 —— 正文还在，注入的指针会走没有进度的那个分支。
func TestPlanStore_CorruptStepsJSONDegradesGracefully(t *testing.T) {
	db := testPlanDB(t)
	ctx := context.Background()
	saveDraft(t, db, "计划正文", steps("一")...)
	approve(t, db)

	if _, err := db.ExecContext(ctx,
		`UPDATE session_plans SET approved_steps_json = '{ 这不是 JSON'`); err != nil {
		t.Fatalf("弄坏它: %v", err)
	}

	p, err := LoadPlan(ctx, db, testTenant, testSession)
	if err != nil {
		t.Fatalf("坏 JSON 让 LoadPlan 报错了: %v", err)
	}
	if p.Approved != "计划正文" {
		t.Fatalf("正文丢了: %q", p.Approved)
	}
	if len(p.ApprovedSteps) != 0 {
		t.Fatalf("坏 JSON 应当退化成没有步骤: %+v", p.ApprovedSteps)
	}
}
