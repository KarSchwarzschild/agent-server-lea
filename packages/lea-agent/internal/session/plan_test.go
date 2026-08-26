package session

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"testing"

	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	_ "modernc.org/sqlite" // registers the pure-Go "sqlite" database/sql driver
)

// testPlanDB 建一个只有 session_plans 的内存库。DDL 跟 migrations/009 + 010 之后的
// 结构手工对齐（SQLite 没有 ENGINE/CHARSET，AUTOINCREMENT 写法也不同），store 那几条
// SQL 两边都跑得通。
func testPlanDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if _, err := db.Exec(`CREATE TABLE session_plans (
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
	)`); err != nil {
		t.Fatalf("create table: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

const (
	planTenant  = "t1"
	planSession = "s1"
)

func mustSaveDraft(t *testing.T, db *sql.DB, content string, steps ...persistence.PlanStep) {
	t.Helper()
	if err := persistence.SaveDraftPlan(context.Background(), db, planTenant, planSession, content, steps); err != nil {
		t.Fatalf("SaveDraftPlan: %v", err)
	}
}

func activePlan(t *testing.T, db *sql.DB) string {
	t.Helper()
	p, err := persistence.LoadPlan(context.Background(), db, planTenant, planSession)
	if err != nil {
		t.Fatalf("LoadPlan: %v", err)
	}
	return p.Approved
}

func draftPlan(t *testing.T, db *sql.DB) string {
	t.Helper()
	p, err := persistence.LoadPlan(context.Background(), db, planTenant, planSession)
	if err != nil {
		t.Fatalf("LoadPlan: %v", err)
	}
	return p.Draft
}

func apply(t *testing.T, db *sql.DB, plan PlanControl) {
	t.Helper()
	if err := applyPlanAction(context.Background(), db, planTenant, planSession, plan); err != nil {
		t.Fatalf("applyPlanAction(%+v): %v", plan, err)
	}
}

// 这条是整轮改动的核心回归。
//
// 老实现在 plan_mode=false 时无条件 ActivatePlan，也就是把"用户发了一条普通消息"
// 读成"用户批准了计划"。于是用户回一句"算了不用了"会激活那份被否掉的计划，而且
// 因为没有任何东西会让计划退休，它会注入到这个会话之后的每一轮。
//
// 挂了的表现极具迷惑性：功能"能用"（计划确实生效了），只是生效在用户没同意的时候。
func TestApplyPlanAction_NoActionDoesNotActivateDraft(t *testing.T) {
	db := testPlanDB(t)
	mustSaveDraft(t, db, "计划正文")

	// 执行态 + 没表态 —— 正是老实现会误激活的那一格
	apply(t, db, PlanControl{Mode: false, Action: PlanActionNone})

	if got := activePlan(t, db); got != "" {
		t.Fatalf("没表态就激活了计划: %q", got)
	}
}

func TestApplyPlanAction_ApproveActivatesDraft(t *testing.T) {
	db := testPlanDB(t)
	mustSaveDraft(t, db, "计划正文")

	apply(t, db, PlanControl{Mode: false, Action: PlanActionApprove})

	if got := activePlan(t, db); got != "计划正文" {
		t.Fatalf("approve 之后计划没激活: %q", got)
	}
}

// 用户重复点"同意"、或前端重发请求 —— 都不该报错。多副本下重发是常态。
func TestApplyPlanAction_ApproveIsIdempotent(t *testing.T) {
	db := testPlanDB(t)
	mustSaveDraft(t, db, "计划正文")

	apply(t, db, PlanControl{Action: PlanActionApprove})
	apply(t, db, PlanControl{Action: PlanActionApprove})

	if got := activePlan(t, db); got != "计划正文" {
		t.Fatalf("重复 approve 之后计划丢了: %q", got)
	}
}

// 没有 draft 时 approve 是 no-op，不是错误。
func TestApplyPlanAction_ApproveWithNoDraft(t *testing.T) {
	db := testPlanDB(t)

	apply(t, db, PlanControl{Action: PlanActionApprove})

	if got := activePlan(t, db); got != "" {
		t.Fatalf("凭空冒出一份计划: %q", got)
	}
}

// 计划激活之后，后续每一轮都是 Action=""，那些轮次不能把它弄丢 —— 执行阶段
// 绝大多数请求走的就是这一支。
func TestApplyPlanAction_ActivePlanSurvivesLaterTurns(t *testing.T) {
	db := testPlanDB(t)
	mustSaveDraft(t, db, "计划正文")
	apply(t, db, PlanControl{Action: PlanActionApprove})

	for i := 0; i < 3; i++ {
		apply(t, db, PlanControl{Mode: false, Action: PlanActionNone})
	}

	if got := activePlan(t, db); got != "计划正文" {
		t.Fatalf("执行途中计划丢了: %q", got)
	}
}

// Mode 不参与判断：用户在执行中途切回规划态（Mode=true、没表态）时，已批准的
// 计划应当原样留着 —— 他可能只是想让模型重新调研一下，还没决定要换计划。
//
// 把 Mode 和 Action 绑起来就是又造一次刚拆掉的那种耦合。
func TestApplyPlanAction_ReenteringPlanModeKeepsActivePlan(t *testing.T) {
	db := testPlanDB(t)
	mustSaveDraft(t, db, "计划正文")
	apply(t, db, PlanControl{Action: PlanActionApprove})

	apply(t, db, PlanControl{Mode: true, Action: PlanActionNone})

	if got := activePlan(t, db); got != "计划正文" {
		t.Fatalf("切回规划态把已批准的计划弄丢了: %q", got)
	}
}

// 认不出来的值按"没表态"处理，不报错也不激活。这个字段来自客户端，一个拼错的
// 值不该把用户的消息卡住；而"什么都不做"恰好是安全的那一边。
func TestApplyPlanAction_UnknownActionIsInert(t *testing.T) {
	db := testPlanDB(t)
	mustSaveDraft(t, db, "计划正文")

	apply(t, db, PlanControl{Action: "APPROVE"}) // 大小写不匹配
	apply(t, db, PlanControl{Action: "yes"})

	if got := activePlan(t, db); got != "" {
		t.Fatalf("无法识别的 action 激活了计划: %q", got)
	}
}

// reject 否掉的是**这一版修订**，已批准的那份原样留着继续执行。
//
// 用户点否决的意思是"别改了，按原来的走"。要是把已批准的也删了，执行阶段就一份
// 计划都不注入了 —— 模型继续跑但不再有约束，而且没有任何报错。
func TestApplyPlanAction_RejectDropsRevisionKeepsApproved(t *testing.T) {
	db := testPlanDB(t)
	mustSaveDraft(t, db, "计划 v1")
	apply(t, db, PlanControl{Action: PlanActionApprove})
	mustSaveDraft(t, db, "计划 v2") // 执行中途的修订

	apply(t, db, PlanControl{Action: PlanActionReject})

	if got := activePlan(t, db); got != "计划 v1" {
		t.Fatalf("否决修订版把原计划也否了: %q", got)
	}
	if got := draftPlan(t, db); got != "" {
		t.Fatalf("否决之后修订版还在: %q", got)
	}
}

// 首次提交就被否决 —— 没有旧计划可回退，结果是什么计划都没有。这是对的：
// 用户从一开始就没批准过任何东西。
func TestApplyPlanAction_RejectFirstPlanLeavesNothing(t *testing.T) {
	db := testPlanDB(t)
	mustSaveDraft(t, db, "计划正文")

	apply(t, db, PlanControl{Action: PlanActionReject})

	if got := activePlan(t, db); got != "" {
		t.Fatalf("reject 反而激活了计划: %q", got)
	}
	if got := draftPlan(t, db); got != "" {
		t.Fatalf("被否决的计划还留着: %q", got)
	}
}

// 提交修订版但用户还没表态时，注入的必须还是**老计划**。
//
// 这一格是 migrations/009 存在的理由：008 的单列 + REPLACE INTO 会在这里把老计划
// 直接删掉，于是用户还没点同意，模型已经不按任何计划跑了。
func TestApplyPlanAction_PendingRevisionDoesNotPreempt(t *testing.T) {
	db := testPlanDB(t)
	mustSaveDraft(t, db, "计划 v1")
	apply(t, db, PlanControl{Action: PlanActionApprove})
	mustSaveDraft(t, db, "计划 v2")

	// 用户没表态就又发了条普通消息
	apply(t, db, PlanControl{Mode: false, Action: PlanActionNone})

	if got := activePlan(t, db); got != "计划 v1" {
		t.Fatalf("未批准的修订版抢先生效了: %q", got)
	}
}

// ── 计划模式守卫 ───────────────────────────────────────────────────────────

// handoff 团里 engine 是 nil，而 SetPlanMode 会解引用它。实测：
//
//	PANIC: runtime error: invalid memory address or nil pointer dereference
//
// 而 Send 的那个 goroutine 里没有 recover —— 一次带 plan_mode 的请求打到一个
// handoff 团上，整个 lea-agent 进程就没了。所以必须在入口拒掉，而不是等它跑进去。
//
// 拒服而不是静默忽略：忽略的话用户以为自己在规划态（前端按钮亮着），agent 已经
// 在改东西了。
func TestSend_PlanModeRejectedForHandoffTeam(t *testing.T) {
	s := &AgentSession{id: "s1", tenantID: "t1", isHandoff: true} // engine 故意留 nil

	err := s.Send("跑一下", nil, PlanControl{Mode: true})

	if !errors.Is(err, ErrPlanModeUnsupported) {
		t.Fatalf("handoff 团上的 plan_mode 应当被拒，got %v", err)
	}
}

// 放行的几格直接测 planSupported，不走 Send。
//
// 从 Send 那一头测"放行"会让它继续往下起 goroutine，而手搓的假会话在那里会因为
// 别的 nil 字段炸掉（第一次写就踩了：ReloadHistory 上 nil deref）—— 那种失败跟被测
// 的规则毫无关系，只会让人以为守卫写错了。上面那条拒服的用例仍然走 Send，因为它
// 在起 goroutine 之前就返回了，能证明这道门真的接在 Send 上。
func TestPlanSupported(t *testing.T) {
	cases := []struct {
		name      string
		isHandoff bool
		reqMode   bool
		wantErr   bool
	}{
		// handoff 团的正常对话是主用例，不能被这道门挡掉。条件写成 `if s.isHandoff`
		// （漏了 plan.Mode）的表现就是所有接力团整个不可用。
		{"handoff team without plan_mode", true, false, false},
		{"handoff team with plan_mode", true, true, true},

		// 星型团 —— 放行。团长有常驻 engine，它就是那个"主 agent"，ask_expert 相当于
		// 派活给子代理。主 agent 规划、批准后再派活，这个形状成立。
		{"star team with plan_mode", false, true, false},

		// 单 agent（非团）—— 放行。计划模式是所有 agent 都支持的能力，没有按 agent
		// 的开关：要不要进规划态由每次请求带来，不是配置出来的。
		{"single agent with plan_mode", false, true, false},
		{"single agent execution", false, false, false},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			s := &AgentSession{id: "s1", tenantID: "t1", isHandoff: c.isHandoff}

			err := s.planSupported(PlanControl{Mode: c.reqMode})

			if c.wantErr && !errors.Is(err, ErrPlanModeUnsupported) {
				t.Fatalf("应当拒掉但 got %v", err)
			}
			if !c.wantErr && err != nil {
				t.Fatalf("应当放行但拒了: %v", err)
			}
		})
	}
}

// ── planHint：每轮注入的那一行 ────────────────────────────────────────────

func TestPlanHint(t *testing.T) {
	// 没有已批准的计划 → 什么都不拼。待批准的修订也不算，否则用户还没点同意，
	// 消息里已经在说"已批准，共 3 步"了。
	if got := planHint(persistence.Plan{}); got != "" {
		t.Fatalf("没有计划却拼了东西: %q", got)
	}
	if got := planHint(persistence.Plan{Draft: "待批准的", DraftSteps: []persistence.PlanStep{
		{Title: "一", Status: persistence.StepPending},
	}}); got != "" {
		t.Fatalf("待批准的计划被当成已批准的了: %q", got)
	}

	// 有步骤 → 带进度和当前这步
	p := persistence.Plan{Approved: "计划正文", ApprovedSteps: []persistence.PlanStep{
		{Title: "调研", Status: persistence.StepCompleted},
		{Title: "改代码", Status: persistence.StepInProgress},
		{Title: "补测试", Status: persistence.StepPending},
	}}
	hint := planHint(p)
	for _, want := range []string{"共 3 步", "已完成 1 步", "改代码", "get_plan"} {
		if !strings.Contains(hint, want) {
			t.Fatalf("指针里缺 %q: %q", want, hint)
		}
	}
	// 关键性质：拼的是指针不是全文。全文每轮拼一次会在历史里堆 O(轮数) 份，
	// 而这一行会跟着每轮的 user 消息落库，所以它必须短。
	if strings.Contains(hint, "计划正文") {
		t.Fatalf("指针里混进了计划全文: %q", hint)
	}
	if len(hint) > 300 {
		t.Fatalf("指针太长了（%d 字节），它每轮都会落一份: %q", len(hint), hint)
	}

	// 没有结构化步骤（老数据，或模型提交时没给 steps）→ 降级成纯指针，
	// 不谎报一个 0/0 的进度。
	bare := planHint(persistence.Plan{Approved: "计划正文"})
	if bare == "" || strings.Contains(bare, "0 步") {
		t.Fatalf("没有步骤时的降级不对: %q", bare)
	}
	if !strings.Contains(bare, "get_plan") {
		t.Fatalf("降级分支也得告诉模型去哪拿全文: %q", bare)
	}
}
