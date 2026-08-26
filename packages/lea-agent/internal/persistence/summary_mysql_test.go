package persistence

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"testing"
	"time"

	_ "github.com/go-sql-driver/mysql"
	"gitlab.ouc-online.com.cn/aibase/agentloop/llm"
)

// 摘要落库这条路只能对着**真 MySQL** 验，SQLite 顶不上。
//
// SaveSummary 里有两处方言特有的写法：
//
//	ON DUPLICATE KEY UPDATE          SQLite 语法直接拒绝（它是 ON CONFLICT）
//	ORDER BY id DESC LIMIT 1 OFFSET ? SQLite 认，但 OFFSET 的行为要一致才有意义
//
// 而这个包里现有的库测试全跑在 modernc.org/sqlite 上（见 plan_crossnode_test.go），
// 于是 SaveSummary 的 SQL 到今天为止**一次都没在 MySQL 上执行过** —— 它唯一的调用点
// 在引擎里是 `_ = ss.SaveSummary(...)`，错误整个扔掉，所以就算语法是错的也没人会知道。
// 这个文件补的就是那一格。
//
// 默认跳过。要跑：
//
//	LEA_TEST_MYSQL_DSN='user:pass@tcp(host:3307)/lea-platform-test?parseTime=true' \
//	    go test ./internal/persistence/ -run MySQL -v
//
// 用一个随机 session id + 独立 tenant，跑完 defer 删干净，不碰任何真实会话。
func mysqlTestDB(t *testing.T) *sql.DB {
	t.Helper()

	dsn := os.Getenv("LEA_TEST_MYSQL_DSN")
	if dsn == "" {
		t.Skip("LEA_TEST_MYSQL_DSN 未设置 —— 跳过真库往返测试")
	}

	db, err := sql.Open("mysql", dsn)
	if err != nil {
		t.Fatalf("open mysql: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := db.PingContext(ctx); err != nil {
		t.Fatalf("ping mysql: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

func textMsg(role llm.Role, s string) llm.Message {
	return llm.Message{
		Role:    role,
		Content: []llm.ContentBlock{{Type: llm.ContentBlockTypeText, Text: s}},
	}
}

// TestSummaryRoundTripOnMySQL 走一遍 compact 之后的完整形状：
// 攒历史 → SaveSummary → 另一个 EngineBackend（模拟 Pod B）LoadHistory。
//
// 关键断言是**条数和顺序**：[摘要, 最近 recentCount 条]。差一位的话表现不是报错，
// 是恢复出来的历史多一段或少一段 —— covered_id 的算法（LIMIT 1 OFFSET recentCount）
// 依赖"工作历史条数 == DB 行数"这个前提，这条测试就是那个前提的哨兵。
func TestSummaryRoundTripOnMySQL(t *testing.T) {
	db := mysqlTestDB(t)
	ctx := context.Background()

	const tenant = "test-summary-tenant"
	session := fmt.Sprintf("test-summary-%d", time.Now().UnixNano())

	t.Cleanup(func() {
		db.Exec(`DELETE FROM session_summaries WHERE tenant_id = ? AND session_id = ?`, tenant, session)
		db.Exec(`DELETE FROM agent_messages WHERE tenant_id = ? AND session_id = ?`, tenant, session)
	})

	backend := NewEngineBackend(db, tenant, 1_000_000)
	store, err := backend.OpenStore(ctx, session)
	if err != nil {
		t.Fatalf("open store: %v", err)
	}

	// 10 条：5 轮 user/assistant。
	var all []llm.Message
	for i := 0; i < 5; i++ {
		all = append(all,
			textMsg(llm.RoleUser, fmt.Sprintf("问题 %d", i)),
			textMsg(llm.RoleAssistant, fmt.Sprintf("回答 %d", i)),
		)
	}
	if err := store.Append(ctx, all); err != nil {
		t.Fatalf("append: %v", err)
	}

	ss, ok := store.(interface {
		SaveSummary(context.Context, llm.Message, int) error
	})
	if !ok {
		t.Fatal("engineStore 没实现 SaveSummary —— SummaryStore 断言掉了")
	}

	// 引擎压缩后的形状：摘要 + 最近 4 条。摘要开头必须是这句 ——
	// compaction.IsSummaryMessage 认的就是它，改了的话恢复时这条会被当成普通
	// user 消息，摘要路径静默失效。
	summary := textMsg(llm.RoleUser, "Summary of earlier conversation: 聊了五个问题。")
	const recentCount = 4

	if err := ss.SaveSummary(ctx, summary, recentCount); err != nil {
		t.Fatalf("SaveSummary 在真 MySQL 上失败: %v", err)
	}

	// Pod B：同一个库、同一个租户，全新的 backend，没有任何内存状态。
	podB := NewEngineBackend(db, tenant, 1_000_000)
	got, err := podB.LoadHistory(ctx, session)
	if err != nil {
		t.Fatalf("LoadHistory: %v", err)
	}

	if len(got) != 1+recentCount {
		t.Fatalf("期望 [摘要 + %d 条]，拿到 %d 条", recentCount, len(got))
	}
	if got[0].Content[0].Text != summary.Content[0].Text {
		t.Errorf("第一条不是摘要: %q", got[0].Content[0].Text)
	}
	// 最近 4 条 = 问题3/回答3/问题4/回答4
	wantTail := []string{"问题 3", "回答 3", "问题 4", "回答 4"}
	for i, want := range wantTail {
		if got[i+1].Content[0].Text != want {
			t.Errorf("第 %d 条: 期望 %q，拿到 %q", i+1, want, got[i+1].Content[0].Text)
		}
	}
}

// TestSaveSummaryNoBoundaryOnMySQL 钉住 sql.ErrNoRows 那条路。
//
// recentCount >= 总行数 = 摘要之后一条不剩 = 没有边界可存。这不是故障，所以返 nil
// 且不写行 —— 写一行 covered_id 指向不存在的位置，下次 LoadHistory 就会返回
// [摘要 + 空]，把整段近期对话吃掉。
//
// 之所以要显式测：SaveSummary 的返回值在引擎那边是 `_ =`，所以「返 error」和
// 「返 nil」对调用方没区别，区别全在**有没有写那一行**。
func TestSaveSummaryNoBoundaryOnMySQL(t *testing.T) {
	db := mysqlTestDB(t)
	ctx := context.Background()

	const tenant = "test-summary-tenant"
	session := fmt.Sprintf("test-noboundary-%d", time.Now().UnixNano())

	t.Cleanup(func() {
		db.Exec(`DELETE FROM session_summaries WHERE tenant_id = ? AND session_id = ?`, tenant, session)
		db.Exec(`DELETE FROM agent_messages WHERE tenant_id = ? AND session_id = ?`, tenant, session)
	})

	store, _ := NewEngineBackend(db, tenant, 1_000_000).OpenStore(ctx, session)
	if err := store.Append(ctx, []llm.Message{
		textMsg(llm.RoleUser, "q"),
		textMsg(llm.RoleAssistant, "a"),
	}); err != nil {
		t.Fatalf("append: %v", err)
	}

	ss := store.(interface {
		SaveSummary(context.Context, llm.Message, int) error
	})

	// recentCount = 2 = 全部行数，摘要覆盖不到任何东西。
	if err := ss.SaveSummary(ctx, textMsg(llm.RoleUser, "Summary of earlier conversation: 空"), 2); err != nil {
		t.Fatalf("没有边界不该报错: %v", err)
	}

	var n int
	db.QueryRow(`SELECT COUNT(*) FROM session_summaries WHERE tenant_id = ? AND session_id = ?`,
		tenant, session).Scan(&n)
	if n != 0 {
		t.Errorf("不该写行，实际写了 %d 行 —— covered_id 会指向不存在的位置", n)
	}
}

// TestSaveSummaryIsIdempotentOnMySQL 钉住 upsert。
//
// 引擎每次 compact 都调 SaveSummary，而压缩是增量的（新摘要带上旧摘要的内容）。
// 要是这里变成 INSERT，第二次就撞主键 —— 而那个错误同样被 `_ =` 吞掉，表现是
// 摘要**永远停在第一次**，越往后恢复出来的历史越不对。
func TestSaveSummaryIsIdempotentOnMySQL(t *testing.T) {
	db := mysqlTestDB(t)
	ctx := context.Background()

	const tenant = "test-summary-tenant"
	session := fmt.Sprintf("test-upsert-%d", time.Now().UnixNano())

	t.Cleanup(func() {
		db.Exec(`DELETE FROM session_summaries WHERE tenant_id = ? AND session_id = ?`, tenant, session)
		db.Exec(`DELETE FROM agent_messages WHERE tenant_id = ? AND session_id = ?`, tenant, session)
	})

	backend := NewEngineBackend(db, tenant, 1_000_000)
	store, _ := backend.OpenStore(ctx, session)

	var all []llm.Message
	for i := 0; i < 4; i++ {
		all = append(all,
			textMsg(llm.RoleUser, fmt.Sprintf("q%d", i)),
			textMsg(llm.RoleAssistant, fmt.Sprintf("a%d", i)),
		)
	}
	if err := store.Append(ctx, all); err != nil {
		t.Fatalf("append: %v", err)
	}

	ss := store.(interface {
		SaveSummary(context.Context, llm.Message, int) error
	})

	if err := ss.SaveSummary(ctx, textMsg(llm.RoleUser, "Summary of earlier conversation: 第一版"), 4); err != nil {
		t.Fatalf("第一次 SaveSummary: %v", err)
	}
	if err := ss.SaveSummary(ctx, textMsg(llm.RoleUser, "Summary of earlier conversation: 第二版"), 2); err != nil {
		t.Fatalf("第二次 SaveSummary（upsert 没生效？）: %v", err)
	}

	var n int
	var covered int64
	if err := db.QueryRow(
		`SELECT COUNT(*) FROM session_summaries WHERE tenant_id = ? AND session_id = ?`,
		tenant, session,
	).Scan(&n); err != nil {
		t.Fatalf("count: %v", err)
	}
	if n != 1 {
		t.Fatalf("期望 1 行（一个会话只有一条摘要），拿到 %d 行", n)
	}

	if err := db.QueryRow(
		`SELECT covered_id FROM session_summaries WHERE tenant_id = ? AND session_id = ?`,
		tenant, session,
	).Scan(&covered); err != nil {
		t.Fatalf("read covered_id: %v", err)
	}

	// recentCount 从 4 降到 2 = 摘要覆盖得更多了 = covered_id 必须往后走。
	got, err := NewEngineBackend(db, tenant, 1_000_000).LoadHistory(ctx, session)
	if err != nil {
		t.Fatalf("LoadHistory: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("第二次摘要后期望 [摘要 + 2 条]，拿到 %d 条", len(got))
	}
	if got[0].Content[0].Text != "Summary of earlier conversation: 第二版" {
		t.Errorf("摘要没被覆盖，还是: %q", got[0].Content[0].Text)
	}
}
