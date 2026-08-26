package session

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"os"
	"testing"
	"time"

	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/control"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/lock"
)

// 这里测的是"停止请求该往哪儿走"这套判断，也就是 handleStop 转发路径背后的真值表。
//
// 关键性质是**诚实**：ForwardStop 返回 false 意味着全集群真的没有在跑的 turn（有锁
// 作依据），于是 404 是有根据的结论而不是猜测。乐观转发看起来能用，代价是"停止按钮
// 没反应"变成一个无法从响应上区分的静默故障。

func randSessionID(t *testing.T, prefix string) string {
	t.Helper()
	var b [6]byte
	if _, err := rand.Read(b[:]); err != nil {
		t.Fatal(err)
	}
	return prefix + hex.EncodeToString(b[:])
}

// clusterManager 造一个接了 Redis 的 Manager。不用 NewManager() —— 它读 REDIS_URL，
// 而测试用的是 TEST_REDIS_URL（故意分开，免得跑一次单测就把本机 .env 里的地址当真）。
func clusterManager(t *testing.T) *Manager {
	t.Helper()
	url := os.Getenv("TEST_REDIS_URL")
	if url == "" {
		t.Skip("未配 TEST_REDIS_URL，跳过")
	}
	l, err := lock.Open(url, 30*time.Second)
	if err != nil {
		t.Fatalf("连 Redis 失败: %v", err)
	}
	bus, err := control.Open(url)
	if err != nil {
		t.Fatalf("连 Redis 失败: %v", err)
	}

	m := &Manager{
		sessions:    make(map[string]*AgentSession),
		idleTTL:     30 * time.Minute,
		maxSessions: 500,
		stopSweep:   make(chan struct{}),
		locker:      l,
		control:     bus,
	}
	m.unsubControl = bus.SubscribeStop(m.applyRemoteStop)
	t.Cleanup(func() {
		m.Close()
		_ = bus.Close()
	})
	return m
}

// 全集群都没在跑 → 如实说没有。这条是 404 的依据。
func TestForwardStop_NoTurnAnywhere(t *testing.T) {
	m := clusterManager(t)

	forwarded, err := m.ForwardStop(context.Background(), randSessionID(t, "nothing-running-"))
	if err != nil {
		t.Fatalf("查锁不该出错: %v", err)
	}
	if forwarded {
		t.Error("没有 turn 在跑却报告转发成功 —— 这个 404 就变成了猜的")
	}
}

// 有 pod 在跑（锁存在）→ 把信号发出去。
func TestForwardStop_TurnRunningElsewhere(t *testing.T) {
	m := clusterManager(t)
	sessionID := randSessionID(t, "running-elsewhere-")

	// 模拟别的 pod 正持有执行权
	h, err := m.locker.Acquire(context.Background(), sessionID)
	if err != nil {
		t.Fatalf("拿锁失败: %v", err)
	}
	defer h.Release()

	forwarded, err := m.ForwardStop(context.Background(), sessionID)
	if err != nil {
		t.Fatalf("转发失败: %v", err)
	}
	if !forwarded {
		t.Error("锁明明被占着，却报告没有 turn 在跑")
	}
}

// 控制面不通但锁在 → 必须报错（→503），不能返 false。
//
// 返 false 会让前端收到 404「没什么在跑」，而真相是「有一轮在跑，但我们送不出停止
// 信号」。这两件事对用户的意义完全相反：前者是"已经结束了"，后者是"还在烧 token
// 且你按的按钮没用"。
func TestForwardStop_ControlUnavailableIsAnError(t *testing.T) {
	m := clusterManager(t)
	sessionID := randSessionID(t, "no-control-")

	h, err := m.locker.Acquire(context.Background(), sessionID)
	if err != nil {
		t.Fatalf("拿锁失败: %v", err)
	}
	defer h.Release()

	m.control = nil // 模拟控制面未启用/连不上

	forwarded, err := m.ForwardStop(context.Background(), sessionID)
	if err == nil {
		t.Error("控制面不通时必须报错（→503），静默返 false 会被当成 404「没什么在跑」")
	}
	if forwarded {
		t.Error("发都没发出去，不能报告转发成功")
	}
}

// 单实例（locker == nil）：本进程内存就是全部真相，转发一律返 false → 404，
// 跟接 pub/sub 之前的行为完全一致。
func TestForwardStop_SingleInstanceReportsNothingRunning(t *testing.T) {
	m := &Manager{sessions: make(map[string]*AgentSession), stopSweep: make(chan struct{})}

	forwarded, err := m.ForwardStop(context.Background(), "whatever")
	if err != nil {
		t.Fatalf("未启用时不该报错: %v", err)
	}
	if forwarded {
		t.Error("未启用集群协调时不该声称转发成功")
	}
}

// ── 生成状态查询 ────────────────────────────────────────────
//
// 前端断流后靠它决定"接着等"还是"去拉最终结果"。答错的两个方向都有实际代价：
// 假 false 让前端过早放弃、丢掉正在生成的答案；假 true 让它死等一个不存在的 turn。

// 别的 pod 在跑 → 本 pod 也要能答 true。这正是它比"查本地内存"多出来的价值。
func TestIsTurnRunning_SeesOtherPodsTurn(t *testing.T) {
	m := clusterManager(t)
	sessionID := randSessionID(t, "status-remote-")

	running, err := m.IsTurnRunning(context.Background(), sessionID)
	if err != nil {
		t.Fatal(err)
	}
	if running {
		t.Fatal("没人在跑却答 true")
	}

	h, err := m.locker.Acquire(context.Background(), sessionID)
	if err != nil {
		t.Fatal(err)
	}
	if running, err = m.IsTurnRunning(context.Background(), sessionID); err != nil || !running {
		t.Fatalf("别的 pod 在跑时应答 true，实际 %v (err=%v)", running, err)
	}

	h.Release()
	if running, err = m.IsTurnRunning(context.Background(), sessionID); err != nil || running {
		t.Errorf("跑完之后应答 false，实际 %v (err=%v)", running, err)
	}
}

// 单实例（locker == nil）：只有本进程内存这一个真相来源，必须先查它。
// 顺序反了的话这里恒为 false —— 而这是今天线上跑的那条路。
func TestIsTurnRunning_SingleInstanceUsesLocalMemory(t *testing.T) {
	m := &Manager{sessions: make(map[string]*AgentSession), stopSweep: make(chan struct{})}

	s := &AgentSession{id: "solo", subscribers: make(map[chan SSEEvent]struct{})}
	m.sessions[s.id] = s

	if running, _ := m.IsTurnRunning(context.Background(), s.id); running {
		t.Error("没在跑却答 true")
	}

	_, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.turnMu.Lock()
	s.turnCancel = cancel
	s.turnMu.Unlock()

	running, err := m.IsTurnRunning(context.Background(), s.id)
	if err != nil {
		t.Fatal(err)
	}
	if !running {
		t.Error("本进程有 turn 在跑却答 false —— 单实例下这是唯一的真相来源")
	}
}

// ── 收到别人转发过来的停止请求 ────────────────────────────────

// 本 pod 没有这个会话 —— 什么也不做。全局频道下这是**绝大多数**调用的结果
// （N 个副本收到同一条消息，只有一个手里有会话），所以它必须便宜且安全。
func TestApplyRemoteStop_IgnoresUnknownSession(t *testing.T) {
	m := &Manager{sessions: make(map[string]*AgentSession), stopSweep: make(chan struct{})}
	m.applyRemoteStop("never-heard-of-it") // 不能 panic
}

// 本 pod 有这个会话 → 掐断它，效果跟本地点停止按钮一样。
func TestApplyRemoteStop_CancelsLocalTurn(t *testing.T) {
	m := &Manager{sessions: make(map[string]*AgentSession), stopSweep: make(chan struct{})}

	s := &AgentSession{id: "local-turn", subscribers: make(map[chan SSEEvent]struct{})}
	turnCtx, cancel := context.WithCancel(context.Background())
	s.turnMu.Lock()
	s.turnCancel = cancel
	s.turnEpoch++
	s.turnMu.Unlock()
	m.sessions[s.id] = s

	m.applyRemoteStop(s.id)

	select {
	case <-turnCtx.Done():
	default:
		t.Fatal("远端的停止请求没有掐断本地 turn")
	}
	// turnStopped 决定落库时记「用户停」而不是「出错」—— 跨 pod 停止不能丢掉这个区分，
	// 否则用户主动停一次，历史里就多一条看起来像故障的记录。
	s.turnMu.Lock()
	stopped := s.turnStopped
	s.turnMu.Unlock()
	if !stopped {
		t.Error("turnStopped 没置位 —— 落库会把用户主动停记成出错")
	}
}

// 端到端：pod A 上的会话，由 pod B 发起停止。
//
// 这是 #10 要修的那个 bug 的直接复现 —— 接 pub/sub 之前，POST /chat/stop 落到
// 不持有会话的副本上就是 404 + 回答继续生成。
func TestRemoteStop_EndToEndAcrossPods(t *testing.T) {
	podA := clusterManager(t) // 会话住在这里，并且挂着订阅
	podB := clusterManager(t) // 停止请求落在这里

	sessionID := randSessionID(t, "cross-pod-stop-")
	s := &AgentSession{id: sessionID, subscribers: make(map[chan SSEEvent]struct{})}
	turnCtx, cancel := context.WithCancel(context.Background())
	s.turnMu.Lock()
	s.turnCancel = cancel
	s.turnEpoch++
	s.turnMu.Unlock()

	podA.mu.Lock()
	podA.sessions[sessionID] = s
	podA.mu.Unlock()

	// pod A 正在跑这一轮，所以它持有执行权 —— 这也是 pod B 判断"有东西可停"的依据。
	h, err := podA.locker.Acquire(context.Background(), sessionID)
	if err != nil {
		t.Fatalf("pod A 拿锁失败: %v", err)
	}
	defer h.Release()

	// pod B 上没有这个会话（它的 map 是空的），走转发路径。
	forwarded, err := podB.ForwardStop(context.Background(), sessionID)
	if err != nil {
		t.Fatalf("pod B 转发失败: %v", err)
	}
	if !forwarded {
		t.Fatal("pod B 认为没有 turn 在跑 —— 锁明明被 pod A 占着")
	}

	select {
	case <-turnCtx.Done():
	case <-time.After(3 * time.Second):
		t.Fatal("pod A 上的 turn 没被停掉 —— 停止请求跨不过 pod 边界（正是 #10 要修的 bug）")
	}
}
