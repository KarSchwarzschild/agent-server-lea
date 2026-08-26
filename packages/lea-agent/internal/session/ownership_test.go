package session

import (
	"context"
	"errors"
	"testing"
)

// 这个文件钉的是一个**实测出来的跨用户数据泄漏**，别删。
//
// ── 原来的洞 ────────────────────────────────────────────────────────────
//
// GetOrCreate 拿到 session_id 时只在"内存里有"的分支做归属校验。内存里没有就
// 直接往下走去新建 —— 而 NewAgentSession 会沿用客户端给的 id，引擎的 Resume 又会
// 按这个 id 调 backend.LoadHistory 把历史读进上下文。
//
// 于是：拿着别人的 session_id 打到一个"内存里没有它"的副本，就能让模型复述别人的
// 对话。双副本验证台上实测过，同一个请求打三次：
//
//	落到持有该会话的副本   → 403
//	落到另一个副本         → 200，并带出了受害者的历史（inputTokens 也涨了）
//
// 安全边界取决于负载均衡挑了谁。**单副本下永远命中内存分支，所以测不出来** ——
// 这也是为什么这个洞一直没被发现。
//
// ── 为什么这里只测"验不了就拒" ──────────────────────────────────────────
//
// 归属不符那条路径要真数据库（persistence.DB() 是 sync.Once 单例读环境变量，
// 没有注入口），归在双副本验证台的手工用例里，见 infra/docker-compose.verify.yml。
// 这里测的是安全上更关键的那一半：**验不了的时候必须拒，不能放行**。
// 那一半一旦回退，每次数据库抖动都会把整个洞重新打开，而且是静默的。

// 测试进程里 DB_HOST 没导出，persistence.DB() 会连 localhost:3306 失败 ——
// 正好就是"配了库但连不上"这个场景。
func TestGetOrCreate_RefusesUnverifiableSessionID(t *testing.T) {
	m := &Manager{sessions: map[string]*AgentSession{}, maxSessions: 10}

	_, err := m.GetOrCreate(context.Background(), Params{
		SessionID: "somebody-elses-session",
		TenantID:  "default",
		UserID:    "attacker",
	})

	if err == nil {
		t.Fatal("库不可达时放行了 —— 跨用户历史泄漏在这种情况下会重新打开")
	}
	if !errors.Is(err, ErrUnverifiable) {
		// 特别注意别退化成 ErrForbidden：那会让用户在数据库抖动时看到
		// "无权访问自己的会话"，而正确的语义是"稍后重试"。
		t.Errorf("err = %v, want ErrUnverifiable", err)
	}
	if len(m.sessions) != 0 {
		t.Errorf("拒绝之后仍然建了会话（%d 个）—— 会话对象一旦存在，"+
			"后续请求就会命中内存分支，等于洞还在", len(m.sessions))
	}
}

// 不带 session_id 的新会话不该走归属校验 —— 否则库不可达时连新对话都开不了。
// 这条界定了上面那个 fail-closed 的作用范围。
func TestGetOrCreate_NewSessionSkipsOwnershipCheck(t *testing.T) {
	m := &Manager{sessions: map[string]*AgentSession{}, maxSessions: 10}

	// 不传 SessionID。会话仍然会因为加载 agent 配置失败而建不起来（测试环境里
	// 没有 lea-config），但**不能是** ErrUnverifiable —— 那说明它错误地走了
	// 归属校验。
	_, err := m.GetOrCreate(context.Background(), Params{
		TenantID: "default",
		UserID:   "someone",
	})
	if errors.Is(err, ErrUnverifiable) {
		t.Error("新会话（不带 session_id）走了归属校验 —— 库不可达时连新对话都开不了")
	}
	if errors.Is(err, ErrForbidden) {
		t.Error("新会话被判无权 —— 不该有归属可言")
	}
}

// 内存里已有且属于本人时，快路径必须还在（不能每轮都去查一次库）。
func TestGetOrCreate_InMemoryOwnerStillFastPath(t *testing.T) {
	m := &Manager{sessions: map[string]*AgentSession{}, maxSessions: 10}
	s := &AgentSession{id: "mine", tenantID: "default", userID: "me"}
	m.sessions["mine"] = s

	got, err := m.GetOrCreate(context.Background(), Params{
		SessionID: "mine", TenantID: "default", UserID: "me",
	})
	if err != nil {
		t.Fatalf("本人访问内存里的会话被拒了: %v —— 这条路不该碰数据库", err)
	}
	if got != s {
		t.Error("没有复用内存里的会话对象")
	}
}

// 内存里已有但不是本人 → 403。这条本来就有，一起钉住，免得改归属逻辑时把它带坏。
func TestGetOrCreate_InMemoryForeignIsForbidden(t *testing.T) {
	m := &Manager{sessions: map[string]*AgentSession{}, maxSessions: 10}
	m.sessions["victim"] = &AgentSession{id: "victim", tenantID: "default", userID: "victim"}

	_, err := m.GetOrCreate(context.Background(), Params{
		SessionID: "victim", TenantID: "default", UserID: "attacker",
	})
	if !errors.Is(err, ErrForbidden) {
		t.Errorf("err = %v, want ErrForbidden", err)
	}
}
