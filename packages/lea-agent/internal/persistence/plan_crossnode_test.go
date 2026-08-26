package persistence

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"testing"

	_ "modernc.org/sqlite" // pure-Go SQLite driver
)

// crossNodePlanDB 建一个**文件级** SQLite 库（不是 :memory:），这样两个独立的
// *sql.DB 能同时打开它 —— 模拟两个 pod 连同一个 MySQL。
//
// :memory: 是按连接私有的：每次 Open(":memory:") 拿到的是一个全新的空库。用它写
// "A 写 B 读" 会必然失败，但失败的原因不是跨节点不通，而是它们压根是两个库 ——
// 那种绿/红都不说明问题的测试比没有测试更糟。临时文件是最小的、行为像共享库的东西。
func crossNodePlanDB(t *testing.T) (nodeA, nodeB *sql.DB) {
	t.Helper()

	dir := t.TempDir()
	dbPath := filepath.Join(dir, "shared_plans.db")

	a, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatalf("open nodeA: %v", err)
	}
	if _, err := a.Exec(planDDL); err != nil {
		t.Fatalf("create table from nodeA: %v", err)
	}

	// Node B 打开**同一个**文件。表已存在，IF NOT EXISTS 是 no-op。
	b, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatalf("open nodeB: %v", err)
	}
	if _, err := b.Exec(planDDL); err != nil {
		t.Fatalf("create table from nodeB: %v", err)
	}

	for _, db := range []*sql.DB{a, b} {
		// WAL 允许多读一写并存，不会动不动 "database is locked" —— 跟 InnoDB 的
		// MVCC 是同一个并发模型，只是轻量得多。
		if _, err := db.Exec(`PRAGMA journal_mode=WAL`); err != nil {
			t.Fatalf("set WAL: %v", err)
		}
		// SQLite 默认 busy_timeout=0，一有争用立刻返回 SQLITE_BUSY。给 5 秒，
		// 免得跨节点的写-读序列在 CI 负载下偶发失败。
		if _, err := db.Exec(`PRAGMA busy_timeout=5000`); err != nil {
			t.Fatalf("set busy_timeout: %v", err)
		}
	}

	t.Cleanup(func() {
		a.Close()
		b.Close()
		os.RemoveAll(dir)
	})
	return a, b
}

// ─── 场景 1：提交在 A，批准和加载在 B ────────────────────────────────────
//
// Plan 模式跨节点最核心的路径。计划要是存在进程内存里，Node B 根本看不到 A 存的
// 东西 —— 而那正是多副本部署下的常态（负载均衡不保证同一会话落同一个 pod）。
func TestPlanCrossNode_SaveOnA_ApproveAndLoadOnB(t *testing.T) {
	nodeA, nodeB := crossNodePlanDB(t)
	ctx := context.Background()

	tenant, sess := "tenant1", "session-cross-1"
	planContent := "# 执行计划\n1. 分析需求\n2. 编写代码\n3. 运行测试"

	// Node A：规划态，模型调 save_plan 提交计划
	if err := SaveDraftPlan(ctx, nodeA, tenant, sess, planContent, nil); err != nil {
		t.Fatalf("nodeA SaveDraftPlan: %v", err)
	}
	// 还没批准，A 自己也不该把它当成已批准的
	if got, _ := loadApproved(ctx, nodeA, tenant, sess); got != "" {
		t.Fatalf("nodeA 上未批准的计划就生效了: %q", got)
	}

	// Node B：用户点「同意」，这条请求落到了另一个 pod
	if err := ApprovePlan(ctx, nodeB, tenant, sess); err != nil {
		t.Fatalf("nodeB ApprovePlan: %v", err)
	}
	got, err := loadApproved(ctx, nodeB, tenant, sess)
	if err != nil {
		t.Fatalf("nodeB LoadApprovedPlan: %v", err)
	}
	if got != planContent {
		t.Fatalf("nodeB 读到的计划不对:\n want %q\n got  %q", planContent, got)
	}

	// 回到 A 继续执行 —— 它必须看到 B 做的批准
	if got, _ := loadApproved(ctx, nodeA, tenant, sess); got != planContent {
		t.Fatalf("nodeA 看不到 nodeB 的批准: %q", got)
	}
}

// ─── 场景 2：修订跨节点，且旧计划在批准前不能丢 ──────────────────────────
//
// 执行中途模型在 A 上提交修订版，用户的表态落到 B。无论用户点同意还是否决，
// 中间那段时间里 **A 和 B 读到的都必须还是老计划**。
func TestPlanCrossNode_RevisionKeepsApprovedOnBothNodes(t *testing.T) {
	nodeA, nodeB := crossNodePlanDB(t)
	ctx := context.Background()

	tenant, sess := "tenant1", "session-cross-2"
	v1, v2 := "计划 v1：先做 A 再做 B", "计划 v2：改成先做 B"

	if err := SaveDraftPlan(ctx, nodeA, tenant, sess, v1, nil); err != nil {
		t.Fatalf("save v1: %v", err)
	}
	if err := ApprovePlan(ctx, nodeA, tenant, sess); err != nil {
		t.Fatalf("approve v1: %v", err)
	}

	// A 上提交修订版
	if err := SaveDraftPlan(ctx, nodeA, tenant, sess, v2, nil); err != nil {
		t.Fatalf("save v2: %v", err)
	}

	// 用户还没表态 —— 两个节点读到的都必须还是 v1
	for name, db := range map[string]*sql.DB{"nodeA": nodeA, "nodeB": nodeB} {
		if got, _ := loadApproved(ctx, db, tenant, sess); got != v1 {
			t.Fatalf("%s 在用户表态前就丢了 v1: %q", name, got)
		}
	}

	// B 上用户点「同意」
	if err := ApprovePlan(ctx, nodeB, tenant, sess); err != nil {
		t.Fatalf("approve v2 on B: %v", err)
	}
	if got, _ := loadApproved(ctx, nodeA, tenant, sess); got != v2 {
		t.Fatalf("nodeA 没看到 v2 生效: %q", got)
	}
}

// ─── 场景 3：否决落在另一个节点 ─────────────────────────────────────────
func TestPlanCrossNode_RejectOnBKeepsApprovedVisibleOnA(t *testing.T) {
	nodeA, nodeB := crossNodePlanDB(t)
	ctx := context.Background()

	tenant, sess := "tenant1", "session-cross-3"
	v1, v2 := "计划 v1", "计划 v2"

	if err := SaveDraftPlan(ctx, nodeA, tenant, sess, v1, nil); err != nil {
		t.Fatalf("save v1: %v", err)
	}
	if err := ApprovePlan(ctx, nodeA, tenant, sess); err != nil {
		t.Fatalf("approve v1: %v", err)
	}
	if err := SaveDraftPlan(ctx, nodeA, tenant, sess, v2, nil); err != nil {
		t.Fatalf("save v2: %v", err)
	}

	if err := RejectPlan(ctx, nodeB, tenant, sess); err != nil {
		t.Fatalf("reject on B: %v", err)
	}

	// 否的是修订版，原计划继续执行 —— 两个节点都要这么看
	for name, db := range map[string]*sql.DB{"nodeA": nodeA, "nodeB": nodeB} {
		if got, _ := loadApproved(ctx, db, tenant, sess); got != v1 {
			t.Fatalf("%s 上否决修订版把原计划也否了: %q", name, got)
		}
		if got, _ := loadDraft(ctx, db, tenant, sess); got != "" {
			t.Fatalf("%s 上修订版没被清掉: %q", name, got)
		}
	}
}

// ─── 场景 4：清理跨节点可见 ─────────────────────────────────────────────
func TestPlanCrossNode_ClearFromOtherNode(t *testing.T) {
	nodeA, nodeB := crossNodePlanDB(t)
	ctx := context.Background()

	tenant, sess := "tenant1", "session-cross-4"

	if err := SaveDraftPlan(ctx, nodeA, tenant, sess, "计划", nil); err != nil {
		t.Fatalf("save: %v", err)
	}
	if err := ApprovePlan(ctx, nodeA, tenant, sess); err != nil {
		t.Fatalf("approve: %v", err)
	}
	if err := ClearPlan(ctx, nodeB, tenant, sess); err != nil {
		t.Fatalf("clear on B: %v", err)
	}

	if got, _ := loadApproved(ctx, nodeA, tenant, sess); got != "" {
		t.Fatalf("nodeA 还看得到被 nodeB 清掉的计划: %q", got)
	}
}

// ─── 场景 5：会话之间互不干扰 ───────────────────────────────────────────
//
// 同一个租户下两个会话各有各的计划，一个的批准不能顺手把另一个也批了。
func TestPlanCrossNode_MultiSessionIsolation(t *testing.T) {
	nodeA, nodeB := crossNodePlanDB(t)
	ctx := context.Background()
	tenant := "tenant1"

	if err := SaveDraftPlan(ctx, nodeA, tenant, "sess1", "计划一", nil); err != nil {
		t.Fatalf("save sess1: %v", err)
	}
	if err := SaveDraftPlan(ctx, nodeB, tenant, "sess2", "计划二", nil); err != nil {
		t.Fatalf("save sess2: %v", err)
	}
	if err := ApprovePlan(ctx, nodeA, tenant, "sess1"); err != nil {
		t.Fatalf("approve sess1: %v", err)
	}

	if got, _ := loadApproved(ctx, nodeB, tenant, "sess1"); got != "计划一" {
		t.Fatalf("sess1 跨节点读不到: %q", got)
	}
	if got, _ := loadApproved(ctx, nodeA, tenant, "sess2"); got != "" {
		t.Fatalf("批准 sess1 顺手把 sess2 也批了: %q", got)
	}
}
