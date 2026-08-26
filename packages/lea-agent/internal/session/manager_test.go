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

// 库里不存在的 session_id 应该落到正常的新建流程，不能报 forbidden
// （这里因为没有 config 服务会失败，但错误不该是 ErrForbidden）。
//
// ⚠️ 这条测试原本的注释还写着"不能走进归属校验"，现在**不成立了** —— 内存里没有
// 这个 id 时会去查一次库，见 verifySessionOwner。改动的原因是那条路径上有一个实测
// 到的跨用户历史泄漏（详见 ownership_test.go 顶部）。
//
// 原注释担心的"暴露 id 存在与否"确实被引入了：给一个别人的 id 会拿到 403，
// 给一个不存在的 id 会走到新建（200 或配置错误）。这个存在性预言是**有意接受**的：
//
//   - session_id 是 UUIDv4，猜不出来，所以枚举不可行；预言只在"已经拿到某个具体
//     id"时才给出一个比特的信息，而那种情况下"这不是你的"本来就必须告诉调用方。
//   - 另一边的代价是确定的、已复现的数据泄漏（别人的对话历史进 LLM 上下文）。
//
// 两害相权很清楚。但如果哪天 session_id 变成可枚举的（自增、按时间戳派生等），
// 这个取舍要重新算。
func TestGetOrCreate_UnknownSessionIsNotForbidden(t *testing.T) {
	m := newTestManager()
	_, err := m.GetOrCreate(context.Background(), Params{
		SessionID: "nope", TenantID: "t1", UserID: "u1",
	})
	if errors.Is(err, ErrForbidden) {
		t.Fatal("unknown session must not report forbidden — leaks id existence")
	}
}
