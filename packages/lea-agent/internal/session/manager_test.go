package session

import (
	"context"
	"errors"
	"testing"
)

// GetOrCreate 的缓存命中分支曾经不校验归属，于是把别人的 session_id 塞进
// /chat/send 就能驱动对方的会话。这几个用例盯住那条分支。
//
// 只测缓存命中路径 —— miss 会去 LoadAgentConfig 打 lea-config 的 HTTP，
// 那部分归端到端冒烟。

func newTestManager(sessions ...*AgentSession) *Manager {
	m := &Manager{sessions: make(map[string]*AgentSession)}
	for _, s := range sessions {
		m.sessions[s.id] = s
	}
	return m
}

func TestGetOrCreate_ReusesOwnSession(t *testing.T) {
	own := &AgentSession{id: "s1", tenantID: "t1", userID: "u1"}
	m := newTestManager(own)

	got, err := m.GetOrCreate(context.Background(), Params{
		SessionID: "s1", TenantID: "t1", UserID: "u1",
	})
	if err != nil {
		t.Fatalf("owner rejected: %v", err)
	}
	if got != own {
		t.Fatal("should return the cached session instance")
	}
}

func TestGetOrCreate_RefusesOtherUsersSession(t *testing.T) {
	m := newTestManager(&AgentSession{id: "s1", tenantID: "t1", userID: "u1"})

	for _, p := range []Params{
		{SessionID: "s1", TenantID: "t1", UserID: "u2"}, // 同租户，别的用户
		{SessionID: "s1", TenantID: "t2", UserID: "u1"}, // 别的租户，同名用户
	} {
		got, err := m.GetOrCreate(context.Background(), p)
		if !errors.Is(err, ErrForbidden) {
			t.Fatalf("tenant=%s user=%s: want ErrForbidden, got err=%v", p.TenantID, p.UserID, err)
		}
		if got != nil {
			// 返回了 session 就等于泄露 —— 调用方拿到它就能 Send
			t.Fatalf("tenant=%s user=%s: session leaked despite error", p.TenantID, p.UserID)
		}
	}
}

// session_id 不存在时不能走进归属校验、也不能直接报 forbidden —— 那会暴露
// "这个 id 存在与否"。应该落到正常的新建流程（这里因为没有 config 服务会失败，
// 但错误不该是 ErrForbidden）。
func TestGetOrCreate_UnknownSessionIsNotForbidden(t *testing.T) {
	m := newTestManager()
	_, err := m.GetOrCreate(context.Background(), Params{
		SessionID: "nope", TenantID: "t1", UserID: "u1",
	})
	if errors.Is(err, ErrForbidden) {
		t.Fatal("unknown session must not report forbidden — leaks id existence")
	}
}
