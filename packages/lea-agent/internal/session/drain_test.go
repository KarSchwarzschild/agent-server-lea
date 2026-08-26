package session

import (
	"context"
	"testing"
	"time"
)

// 造一个不起清理协程的 Manager —— 这些用例只关心 turn 计数，不想跟 sweepLoop
// 抢会话。
func drainFixture(sessions ...*AgentSession) *Manager {
	m := &Manager{
		sessions:  make(map[string]*AgentSession),
		stopSweep: make(chan struct{}),
	}
	for i, s := range sessions {
		if s.id == "" {
			s.id = string(rune('a' + i))
		}
		m.sessions[s.id] = s
	}
	return m
}

// 登记一个"正在跑的 turn"，返回收尾函数。模拟 Send 起手做的那套簿记。
func startTurn(s *AgentSession) func() {
	_, cancel := context.WithCancel(context.Background())
	s.turnMu.Lock()
	s.turnCancel = cancel
	s.turnEpoch++
	s.turnMu.Unlock()
	return func() {
		cancel()
		s.turnMu.Lock()
		s.turnCancel = nil
		s.turnMu.Unlock()
	}
}

func TestActiveTurns_CountsOnlyRunningOnes(t *testing.T) {
	busy := &AgentSession{id: "busy", subscribers: make(map[chan SSEEvent]struct{})}
	idle := &AgentSession{id: "idle", subscribers: make(map[chan SSEEvent]struct{})}
	m := drainFixture(busy, idle)

	if n := m.ActiveTurns(); n != 0 {
		t.Fatalf("还没有 turn 在跑，ActiveTurns 应为 0，实际 %d", n)
	}

	finish := startTurn(busy)
	if n := m.ActiveTurns(); n != 1 {
		t.Fatalf("一个 turn 在跑，应为 1，实际 %d", n)
	}

	finish()
	if n := m.ActiveTurns(); n != 0 {
		t.Fatalf("turn 收尾后应回到 0，实际 %d", n)
	}
}

// 没有 turn 在跑时必须立刻返回 —— 空闲的 pod 不该为了退出白等一个轮询周期。
func TestWaitIdle_ReturnsImmediatelyWhenIdle(t *testing.T) {
	m := drainFixture(&AgentSession{id: "a", subscribers: make(map[chan SSEEvent]struct{})})

	start := time.Now()
	if err := m.WaitIdle(context.Background()); err != nil {
		t.Fatalf("空闲时不该报错: %v", err)
	}
	if elapsed := time.Since(start); elapsed > 100*time.Millisecond {
		t.Errorf("空闲时应立刻返回，实际等了 %s", elapsed)
	}
}

// 核心那条：等到在跑的 turn 收尾为止。没有它，发版时跑到一半的 turn 直接消失 ——
// persistTurn 在 goroutine 末尾，不执行就等于答案既不在流里也不在库里。
func TestWaitIdle_WaitsForRunningTurn(t *testing.T) {
	s := &AgentSession{id: "a", subscribers: make(map[chan SSEEvent]struct{})}
	m := drainFixture(s)
	finish := startTurn(s)

	go func() {
		time.Sleep(400 * time.Millisecond)
		finish()
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	start := time.Now()
	if err := m.WaitIdle(ctx); err != nil {
		t.Fatalf("turn 收尾了就该正常返回: %v", err)
	}
	if elapsed := time.Since(start); elapsed < 300*time.Millisecond {
		t.Errorf("没等 turn 收尾就返回了（%s）—— 排空是假的", elapsed)
	}
}

// 等不完要认账退出，不能无限期挂着。kubelet 的 terminationGracePeriodSeconds
// 到了会 SIGKILL，与其被杀不如自己有个上限并把"还剩几个"打进日志。
func TestWaitIdle_GivesUpOnTimeout(t *testing.T) {
	s := &AgentSession{id: "a", subscribers: make(map[chan SSEEvent]struct{})}
	m := drainFixture(s)
	defer startTurn(s)() // 起一个永不收尾的 turn（defer 里收尾，等测试结束）

	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()

	start := time.Now()
	err := m.WaitIdle(ctx)
	elapsed := time.Since(start)

	if err == nil {
		t.Fatal("turn 一直没收尾，WaitIdle 必须报超时而不是成功返回")
	}
	if elapsed > 2*time.Second {
		t.Errorf("超时后拖太久才返回: %s", elapsed)
	}
}

// 多个会话同时在跑时要全部等到 —— 只等第一个就返回的话，其余 turn 的结果照样丢。
func TestWaitIdle_WaitsForAllSessions(t *testing.T) {
	a := &AgentSession{id: "a", subscribers: make(map[chan SSEEvent]struct{})}
	b := &AgentSession{id: "b", subscribers: make(map[chan SSEEvent]struct{})}
	m := drainFixture(a, b)

	finishA, finishB := startTurn(a), startTurn(b)
	if n := m.ActiveTurns(); n != 2 {
		t.Fatalf("两个 turn 在跑，应为 2，实际 %d", n)
	}

	go func() {
		time.Sleep(100 * time.Millisecond)
		finishA()
		time.Sleep(300 * time.Millisecond)
		finishB()
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	start := time.Now()
	if err := m.WaitIdle(ctx); err != nil {
		t.Fatalf("两个 turn 都收尾了就该返回: %v", err)
	}
	// 必须等到第二个（约 400ms），而不是第一个（约 100ms）就走
	if elapsed := time.Since(start); elapsed < 350*time.Millisecond {
		t.Errorf("只等了第一个 turn 就返回（%s）—— 另一个的结果会丢", elapsed)
	}
}
