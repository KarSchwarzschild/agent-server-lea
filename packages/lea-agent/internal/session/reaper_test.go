package session

import (
	"context"
	"errors"
	"testing"
	"time"
)

// 空闲淘汰的核心契约是"清空闲的、绝不碰正在用的"。误清一个活跃会话
// 等于把用户的对话掐断，所以这几条都盯着"不该清的没被清"。

func idleSession(id string, lastActive time.Time) *AgentSession {
	return &AgentSession{
		id:          id,
		tenantID:    "t1",
		userID:      "u1",
		subscribers: make(map[chan SSEEvent]struct{}),
		lastActive:  lastActive,
	}
}

func newReaperManager(ttl time.Duration, max int, sessions ...*AgentSession) *Manager {
	m := &Manager{
		sessions:    make(map[string]*AgentSession),
		idleTTL:     ttl,
		maxSessions: max,
		stopSweep:   make(chan struct{}),
	}
	for _, s := range sessions {
		m.sessions[s.id] = s
	}
	return m
}

func TestSweepIdle_RemovesExpiredKeepsFresh(t *testing.T) {
	now := time.Now()
	m := newReaperManager(30*time.Minute, 500,
		idleSession("stale", now.Add(-45*time.Minute)),
		idleSession("fresh", now.Add(-5*time.Minute)),
	)

	if got := m.sweepIdle(now); got != 1 {
		t.Fatalf("swept %d, want 1", got)
	}
	if m.Get("stale") != nil {
		t.Fatal("expired session survived")
	}
	if m.Get("fresh") == nil {
		t.Fatal("fresh session was swept")
	}
}

// 长任务场景：会话空闲很久之后发来一条消息，turn 跑好几分钟，期间
// lastActive 不再更新。只看空闲时长会把正在生成回答的会话清掉。
func TestSweepIdle_SparseSessionWithRunningTurn(t *testing.T) {
	now := time.Now()
	busy := idleSession("busy", now.Add(-2*time.Hour))
	_, cancel := context.WithCancel(context.Background())
	defer cancel()
	busy.turnCancel = cancel // 模拟 turn 在跑

	m := newReaperManager(30*time.Minute, 500, busy)

	if got := m.sweepIdle(now); got != 0 {
		t.Fatalf("swept %d, want 0 — turn 在跑时不能清", got)
	}
	if m.Get("busy") == nil {
		t.Fatal("session with active turn was swept — 用户的对话被掐断了")
	}
}

// 用户挂着 SSE 盯着流，但这一轮已经结束（turnCancel 为 nil）。
// 仍然不能清 —— 清了他的流会直接断。
func TestSweepIdle_KeepsSessionWithSubscribers(t *testing.T) {
	now := time.Now()
	watched := idleSession("watched", now.Add(-2*time.Hour))
	ch := watched.Subscribe() // Subscribe 会 Touch，手动回拨
	watched.lastActive = now.Add(-2 * time.Hour)
	defer watched.Unsubscribe(ch)

	m := newReaperManager(30*time.Minute, 500, watched)

	if got := m.sweepIdle(now); got != 0 {
		t.Fatalf("swept %d, want 0 — 有订阅者时不能清", got)
	}
}

func TestEnsureCapacity_EvictsOldestIdle(t *testing.T) {
	now := time.Now()
	m := newReaperManager(30*time.Minute, 2,
		idleSession("older", now.Add(-20*time.Minute)),
		idleSession("newer", now.Add(-1*time.Minute)),
	)

	if err := m.ensureCapacity(); err != nil {
		t.Fatalf("ensureCapacity: %v", err)
	}
	if m.Get("older") != nil {
		t.Fatal("should have evicted the least-recently-active session")
	}
	if m.Get("newer") == nil {
		t.Fatal("evicted the wrong session")
	}
}

// 满了但全部在活跃 —— 应该拒绝新建，而不是挤掉一个正在用的。
func TestEnsureCapacity_RefusesWhenAllBusy(t *testing.T) {
	now := time.Now()
	a, b := idleSession("a", now), idleSession("b", now)
	_, ca := context.WithCancel(context.Background())
	_, cb := context.WithCancel(context.Background())
	defer ca()
	defer cb()
	a.turnCancel, b.turnCancel = ca, cb

	m := newReaperManager(30*time.Minute, 2, a, b)

	if err := m.ensureCapacity(); !errors.Is(err, ErrCapacity) {
		t.Fatalf("want ErrCapacity, got %v", err)
	}
	if m.Count() != 2 {
		t.Fatalf("count=%d — 活跃会话被挤掉了", m.Count())
	}
}

func TestEnsureCapacity_NoopBelowLimit(t *testing.T) {
	m := newReaperManager(30*time.Minute, 10, idleSession("a", time.Now()))
	if err := m.ensureCapacity(); err != nil {
		t.Fatalf("unexpected: %v", err)
	}
	if m.Count() != 1 {
		t.Fatalf("count=%d, want 1", m.Count())
	}
}

// Get / Send 要续期，否则正在轮询的会话会被判定空闲。
func TestTouch_ExtendsLifetime(t *testing.T) {
	now := time.Now()
	s := idleSession("s", now.Add(-45*time.Minute))
	m := newReaperManager(30*time.Minute, 500, s)

	if m.Get("s") == nil { // Get 内部 Touch
		t.Fatal("session missing")
	}
	if got := m.sweepIdle(time.Now()); got != 0 {
		t.Fatalf("swept %d after Touch, want 0", got)
	}
}

// 扫描周期必须跟着 TTL 走。硬编码成 2 分钟时，TTL 设 30s 也要等 2 分钟才清 ——
// 配置看着生效了实际没生效，属于最难发现的那类问题。
func TestSweepIntervalFor(t *testing.T) {
	cases := []struct {
		ttl  time.Duration
		want time.Duration
	}{
		{30 * time.Minute, maxSweepInterval}, // 长 TTL 夹到上限
		{8 * time.Second, minSweepInterval},  // 短 TTL 夹到下限
		{2 * time.Minute, 30 * time.Second},  // 正常区间取 TTL/4
		{0, minSweepInterval},                // 兜底不能返回 0，否则 NewTicker panic
	}
	for _, c := range cases {
		if got := sweepIntervalFor(c.ttl); got != c.want {
			t.Errorf("sweepIntervalFor(%s) = %s, want %s", c.ttl, got, c.want)
		}
	}
}

func TestEnvOverrides(t *testing.T) {
	t.Setenv("LEA_AGENT_SESSION_TTL", "5m")
	t.Setenv("LEA_AGENT_MAX_SESSIONS", "42")
	if got := durationFromEnv("LEA_AGENT_SESSION_TTL", time.Hour); got != 5*time.Minute {
		t.Fatalf("ttl=%s", got)
	}
	if got := intFromEnv("LEA_AGENT_MAX_SESSIONS", 500); got != 42 {
		t.Fatalf("max=%d", got)
	}
	// 垃圾值要回落到默认而不是 0 —— 0 会让每个会话立刻被清
	t.Setenv("LEA_AGENT_SESSION_TTL", "not-a-duration")
	t.Setenv("LEA_AGENT_MAX_SESSIONS", "0")
	if got := durationFromEnv("LEA_AGENT_SESSION_TTL", time.Hour); got != time.Hour {
		t.Fatalf("bad ttl should fall back, got %s", got)
	}
	if got := intFromEnv("LEA_AGENT_MAX_SESSIONS", 500); got != 500 {
		t.Fatalf("zero max should fall back, got %d", got)
	}
}
