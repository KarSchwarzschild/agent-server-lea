package persistence

import (
	"context"
	"database/sql"
	"testing"

	_ "modernc.org/sqlite" // registers the pure-Go "sqlite" database/sql driver
)

// chatSessionsDDL 只取 Get/SetSessionActiveAgent 用得到的那几列，跟 migrations/001 + 023
// 对齐。SQLite 写法跟 MySQL 有差别，但这两条 SQL（SELECT active_agent_id / UPDATE …
// WHERE id+tenant+user）两边都跑得通 —— TouchSession 那条带 NOW() 的不在这里测，它的
// active_agent_id 播种由真库 e2e（scripts/expert_switch_e2e.py 判据①）覆盖。
const chatSessionsDDL = `CREATE TABLE IF NOT EXISTS chat_sessions (
	id              TEXT PRIMARY KEY,
	user_id         TEXT NOT NULL,
	tenant_id       TEXT NOT NULL DEFAULT '',
	project_id      INTEGER NULL,
	active_agent_id INTEGER NOT NULL DEFAULT 0
)`

func testSessionsDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	if _, err := db.Exec(chatSessionsDDL); err != nil {
		t.Fatalf("create table: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

func seedSession(t *testing.T, db *sql.DB, id, tenant, user string, activeAgent int) {
	t.Helper()
	if _, err := db.Exec(
		`INSERT INTO chat_sessions (id, user_id, tenant_id, active_agent_id) VALUES (?, ?, ?, ?)`,
		id, user, tenant, activeAgent,
	); err != nil {
		t.Fatalf("seed session: %v", err)
	}
}

// 缺行 / 空 sessionID / nil db 一律返回 0（默认），不报错 —— 这一步在每条消息热路径上，
// 拿不到就当默认。
func TestGetSessionActiveAgent_Defaults(t *testing.T) {
	db := testSessionsDB(t)
	ctx := context.Background()

	if got := GetSessionActiveAgent(ctx, db, "does-not-exist"); got != 0 {
		t.Fatalf("缺行应返 0，得到 %d", got)
	}
	if got := GetSessionActiveAgent(ctx, db, ""); got != 0 {
		t.Fatalf("空 sessionID 应返 0，得到 %d", got)
	}
	if got := GetSessionActiveAgent(ctx, nil, "s1"); got != 0 {
		t.Fatalf("nil db 应返 0，得到 %d", got)
	}
}

// Set → Get 往返；取消（置 0）也走同一条 UPDATE。
func TestSetGetSessionActiveAgent_RoundTrip(t *testing.T) {
	db := testSessionsDB(t)
	ctx := context.Background()
	seedSession(t, db, "s1", "t1", "u1", 0)

	changed, err := SetSessionActiveAgent(ctx, db, "s1", "t1", "u1", 7)
	if err != nil {
		t.Fatalf("Set: %v", err)
	}
	if !changed {
		t.Fatal("命中自己的会话却报 not changed")
	}
	if got := GetSessionActiveAgent(ctx, db, "s1"); got != 7 {
		t.Fatalf("切专家后应为 7，得到 %d", got)
	}

	// 取消 = 置 0，回默认。
	if _, err := SetSessionActiveAgent(ctx, db, "s1", "t1", "u1", 0); err != nil {
		t.Fatalf("Set 0: %v", err)
	}
	if got := GetSessionActiveAgent(ctx, db, "s1"); got != 0 {
		t.Fatalf("取消后应回 0，得到 %d", got)
	}
}

// WHERE 带 tenant + user 过滤是防越权的第二道闸（sessionOwnerGuard 之外）：别人的会话
// 不命中行，返回 changed=false，让 handler 走 404，且绝不改到别人的列。
func TestSetSessionActiveAgent_OwnershipFilter(t *testing.T) {
	db := testSessionsDB(t)
	ctx := context.Background()
	seedSession(t, db, "victim", "t1", "victim", 3)

	for _, c := range []struct{ tenant, user string }{
		{"t1", "attacker"}, // 同租户别的用户
		{"t2", "victim"},   // 别的租户同名用户
	} {
		changed, err := SetSessionActiveAgent(ctx, db, "victim", c.tenant, c.user, 999)
		if err != nil {
			t.Fatalf("Set(%s/%s): %v", c.tenant, c.user, err)
		}
		if changed {
			t.Fatalf("%s/%s 改到了不属于它的会话", c.tenant, c.user)
		}
	}
	if got := GetSessionActiveAgent(ctx, db, "victim"); got != 3 {
		t.Fatalf("受害会话的 active_agent_id 被越权改动：得到 %d，应仍是 3", got)
	}
}
