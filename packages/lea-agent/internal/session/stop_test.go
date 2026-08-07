package session

import (
	"context"
	"sync"
	"testing"
)

// Stop 的核心契约是"取消当前 turn 的 ctx，且不误伤下一轮"。
// 这里直接验 turnCancel/turnEpoch 这套簿记 —— 起真 engine 要连 LLM，
// 那部分留给端到端冒烟。

func TestStop_NoActiveTurn(t *testing.T) {
	s := &AgentSession{subscribers: make(map[chan SSEEvent]struct{})}
	if s.Stop() {
		t.Fatal("Stop should report false when no turn is running")
	}
}

func TestStop_CancelsActiveTurn(t *testing.T) {
	s := &AgentSession{subscribers: make(map[chan SSEEvent]struct{})}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	s.turnMu.Lock()
	s.turnCancel = cancel
	s.turnEpoch++
	s.turnMu.Unlock()

	if !s.Stop() {
		t.Fatal("Stop should report true while a turn is running")
	}
	select {
	case <-ctx.Done():
	default:
		t.Fatal("turn ctx not cancelled")
	}
	s.turnMu.Lock()
	stopped := s.turnStopped
	s.turnMu.Unlock()
	if !stopped {
		t.Fatal("turnStopped not recorded — persistTurn would lose the [已中断] marker")
	}
}

// 收尾 goroutine 只有在 epoch 仍是自己那轮时才清 turnCancel。
// 否则慢收尾的上一轮会把下一轮刚注册的 cancel 清成 nil，Stop 就静默失效。
func TestStop_StaleTurnDoesNotClearNewCancel(t *testing.T) {
	s := &AgentSession{subscribers: make(map[chan SSEEvent]struct{})}

	s.turnMu.Lock()
	s.turnEpoch++
	staleEpoch := s.turnEpoch
	s.turnMu.Unlock()

	// 第二轮抢过所有权
	ctx2, cancel2 := context.WithCancel(context.Background())
	defer cancel2()
	s.turnMu.Lock()
	s.turnCancel = cancel2
	s.turnEpoch++
	s.turnMu.Unlock()

	// 第一轮此刻才收尾，按 epoch 判定应放弃清理
	s.turnMu.Lock()
	if s.turnEpoch == staleEpoch {
		s.turnCancel = nil
	}
	s.turnMu.Unlock()

	if !s.Stop() {
		t.Fatal("stale turn cleanup wiped the live cancel")
	}
	select {
	case <-ctx2.Done():
	default:
		t.Fatal("second turn ctx not cancelled")
	}
}

func TestStop_ConcurrentCallsSafe(t *testing.T) {
	s := &AgentSession{subscribers: make(map[chan SSEEvent]struct{})}
	_, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.turnMu.Lock()
	s.turnCancel = cancel
	s.turnEpoch++
	s.turnMu.Unlock()

	// 用户连点停止按钮：只要不 panic / 不 race 即可，返回值允许有真有假
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			s.Stop()
		}()
	}
	wg.Wait()
}

func TestOwnedBy(t *testing.T) {
	s := &AgentSession{tenantID: "default", userID: "u1"}
	if !s.OwnedBy("default", "u1") {
		t.Fatal("owner rejected")
	}
	// 拿到别人的 session_id 不能停别人的 turn
	if s.OwnedBy("default", "u2") || s.OwnedBy("other", "u1") {
		t.Fatal("cross-user/tenant stop must be refused")
	}
}
