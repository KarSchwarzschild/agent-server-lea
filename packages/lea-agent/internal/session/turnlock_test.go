package session

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"os"
	"sync"
	"testing"
	"time"

	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/control"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/lock"
)

// 这里测的是**接线**，不是锁本身（锁的语义在 internal/lock 里对着真 Redis 测过了）。
// 要盯住的是两条容易改坏的行为：
//   - 同 pod 再发一条仍然是"打断重问"，而不是被 409 拒掉；
//   - 别的 pod 占着时立刻拒，而不是白等满 lockWaitTimeout。

func lockedSession(t *testing.T, ttl time.Duration) (*AgentSession, *lock.Locker) {
	t.Helper()
	url := os.Getenv("TEST_REDIS_URL")
	if url == "" {
		t.Skip("未配 TEST_REDIS_URL，跳过")
	}
	l, err := lock.Open(url, ttl)
	if err != nil {
		t.Fatalf("连 Redis 失败: %v", err)
	}
	var b [6]byte
	if _, err := rand.Read(b[:]); err != nil {
		t.Fatal(err)
	}
	s := &AgentSession{
		id:          "turnlock-" + hex.EncodeToString(b[:]),
		subscribers: make(map[chan SSEEvent]struct{}),
		locker:      l,
	}
	return s, l
}

// 别的 pod 占着锁，而控制面不可用（s.control == nil，模拟没配 Redis 或连不上）——
// 送不出让位请求就该立刻拒，不能白等满 lockWaitTimeout。
//
// 这条是降级路径，但值得钉死：如果哪天 control.PublishStop 在 nil 上改成"静默成功"，
// 这里就会变成等 5 秒才返 409 —— 一个纯粹的延迟回归，功能上看不出任何异常。
func TestAcquireTurnLock_RejectsImmediatelyWhenControlUnavailable(t *testing.T) {
	s, l := lockedSession(t, 10*time.Second)

	// 模拟 pod B：同一个 session id，另一个 Locker（owner token 不同，
	// 对 Redis 来说跟真的两个进程没有区别）。
	other, err := l.Acquire(context.Background(), s.id)
	if err != nil {
		t.Fatalf("pod B 该先拿到锁: %v", err)
	}
	defer other.Release()

	start := time.Now()
	h, err := s.acquireTurnLock()
	elapsed := time.Since(start)

	if !errors.Is(err, ErrBusy) {
		t.Fatalf("送不出让位请求时必须返 ErrBusy（→409），实际 err=%v", err)
	}
	if h != nil {
		t.Fatal("拿不到锁时不该返回句柄")
	}
	// 请不动对方，也没什么可等的 —— 轮询满 5s 是纯粹让用户干等。
	if elapsed > time.Second {
		t.Fatalf("控制面不可用时应立刻拒，实际等了 %s", elapsed)
	}
}

// ── 跨 pod 打断的完整闭环 ────────────────────────────────────
//
// 这是 #10 的核心：接控制面之前，"别的 pod 在跑"只能返 409，于是同一个操作
// （生成过程中再发一条消息）在单副本下是"打断重问"、多副本下变成"被拒"，而
// 到底是哪个取决于负载均衡把请求丢给了谁。语义随部署形态变化是最难查的一类问题。
//
// 造一个真的两 pod 场景：pod A 有自己的 Locker 和自己的 control.Bus（对 Redis
// 来说跟两个进程没有区别），并且像真的 pod 一样挂着订阅 —— 收到停止请求就取消
// 自己那轮、释放执行权。pod B 就是被测的 s。
func remotePod(t *testing.T, l *lock.Locker, sessionID string) (release func(), stopped <-chan struct{}) {
	t.Helper()
	url := os.Getenv("TEST_REDIS_URL")
	bus, err := control.Open(url)
	if err != nil {
		t.Fatalf("pod A 连 Redis 失败: %v", err)
	}

	h, err := l.Acquire(context.Background(), sessionID)
	if err != nil {
		t.Fatalf("pod A 该先拿到执行权: %v", err)
	}

	got := make(chan struct{})
	var once sync.Once
	unsub := bus.SubscribeStop(func(id string) {
		if id != sessionID {
			return // 全局频道，别的会话的消息照常会来
		}
		// 真实 pod 上这里是 AgentSession.Stop()，随后 turn 收尾时 defer 掉锁。
		once.Do(func() {
			h.Release()
			close(got)
		})
	})

	t.Cleanup(func() {
		unsub()
		once.Do(func() { h.Release() })
		_ = bus.Close()
	})
	// 等订阅在 Redis 上真的建立起来 —— pub/sub 不堆积，早发的消息直接丢。
	waitForRemoteSubscriber(t, bus, sessionID)
	return func() { once.Do(func() { h.Release(); close(got) }) }, got
}

func waitForRemoteSubscriber(t *testing.T, bus *control.Bus, _ string) {
	t.Helper()
	// 没有对外暴露 NUMSUB，用一次真实往返确认链路通了：往频道发一条不相干的
	// session_id，pod A 的 handler 会忽略它，但 Publish 的返回值能告诉我们有没有人在听。
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if err := bus.PublishStop(context.Background(), "warmup-not-a-real-session"); err == nil {
			// PublishStop 在无人监听时只打日志不报错，所以再给一小会儿让订阅稳定。
			time.Sleep(80 * time.Millisecond)
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("pod A 的订阅没能建立")
}

// 别的 pod 在跑 + 控制面可用 = 打断，跟同 pod 一样。
func TestAcquireTurnLock_InterruptsRemotePodsTurn(t *testing.T) {
	s, l := lockedSession(t, 30*time.Second)
	s.control = testControlBus(t)

	_, remoteStopped := remotePod(t, l, s.id)

	start := time.Now()
	h, err := s.acquireTurnLock()
	elapsed := time.Since(start)

	if err != nil {
		t.Fatalf("控制面可用时跨 pod 也该打断并拿到执行权，实际 err=%v（这就是接 pub/sub 之前的 409）", err)
	}
	defer h.Release()

	select {
	case <-remoteStopped:
	default:
		t.Fatal("拿到了执行权但远端没收到停止请求 —— 那一轮还在烧 token")
	}
	// 一次 PUBLISH + 一个轮询周期，远快于 lockWaitTimeout。
	if elapsed > 2*time.Second {
		t.Errorf("跨 pod 打断花了 %s，慢得不正常（轮询周期 %s）", elapsed, lockPollEvery)
	}
}

// 远端收到了停止请求但就是不放（pod 被 SIGKILL、或者那一轮收尾比 5 秒还慢）——
// 等超了仍然要返 409，不能无限期挂着 HTTP 请求。
func TestAcquireTurnLock_GivesUpWhenRemoteNeverYields(t *testing.T) {
	s, l := lockedSession(t, 30*time.Second)
	s.control = testControlBus(t)

	// 拿着锁但**不订阅** —— 相当于那个 pod 已经死了，没人会处理停止请求。
	stuck, err := l.Acquire(context.Background(), s.id)
	if err != nil {
		t.Fatal(err)
	}
	defer stuck.Release()

	start := time.Now()
	_, err = s.acquireTurnLock()
	elapsed := time.Since(start)

	if !errors.Is(err, ErrBusy) {
		t.Fatalf("远端不让位时该返 ErrBusy，实际 err=%v", err)
	}
	if elapsed < lockWaitTimeout {
		t.Fatalf("不该提前放弃：等了 %s，应至少 %s", elapsed, lockWaitTimeout)
	}
	if elapsed > lockWaitTimeout+2*time.Second {
		t.Fatalf("超时后拖太久：%s", elapsed)
	}
}

func testControlBus(t *testing.T) *control.Bus {
	t.Helper()
	b, err := control.Open(os.Getenv("TEST_REDIS_URL"))
	if err != nil {
		t.Fatalf("连 Redis 失败: %v", err)
	}
	t.Cleanup(func() { _ = b.Close() })
	return b
}

// 同 pod 再发一条：既有行为是打断上一轮，接锁之后必须还是这样 ——
// 用户在生成过程中改主意重新问，不该收到 409。
func TestAcquireTurnLock_InterruptsOwnPreviousTurn(t *testing.T) {
	s, _ := lockedSession(t, 10*time.Second)

	// 第一轮：拿到锁并登记 cancel，模拟正在跑。
	first, err := s.acquireTurnLock()
	if err != nil {
		t.Fatalf("第一轮该拿到锁: %v", err)
	}
	prevCtx, prevCancel := context.WithCancel(context.Background())
	s.turnMu.Lock()
	s.turnCancel = prevCancel
	s.turnEpoch++
	s.turnMu.Unlock()

	// 真实收尾是在 turn 的 goroutine 里 defer Release 的；这里代它做，
	// 因为 acquireTurnLock 会等锁被放掉。
	go func() {
		<-prevCtx.Done()
		s.turnMu.Lock()
		s.turnCancel = nil
		s.turnMu.Unlock()
		first.Release()
	}()

	second, err := s.acquireTurnLock()
	if err != nil {
		t.Fatalf("同 pod 再发一条该打断上一轮并拿到锁，而不是被拒: %v", err)
	}
	defer second.Release()

	select {
	case <-prevCtx.Done():
	default:
		t.Fatal("上一轮的 ctx 没被取消 —— 打断行为丢了")
	}
}

// 等不到就拒，不能无限期挂着 HTTP 请求。
//
// 构造：本进程登记了 cancel（于是走"等自己上一轮"那条分支），但锁被别人占着且
// 永不释放。应当在 lockWaitTimeout 附近放弃。
func TestAcquireTurnLock_GivesUpAfterWaitTimeout(t *testing.T) {
	s, l := lockedSession(t, 30*time.Second)

	stuck, err := l.Acquire(context.Background(), s.id)
	if err != nil {
		t.Fatal(err)
	}
	defer stuck.Release()

	_, prevCancel := context.WithCancel(context.Background())
	s.turnMu.Lock()
	s.turnCancel = prevCancel
	s.turnEpoch++
	s.turnMu.Unlock()

	start := time.Now()
	_, err = s.acquireTurnLock()
	elapsed := time.Since(start)

	if !errors.Is(err, ErrBusy) {
		t.Fatalf("等超了该返 ErrBusy，实际 err=%v", err)
	}
	if elapsed < lockWaitTimeout {
		t.Fatalf("不该提前放弃：等了 %s，应至少 %s", elapsed, lockWaitTimeout)
	}
	if elapsed > lockWaitTimeout+2*time.Second {
		t.Fatalf("超时后拖太久才返回：%s", elapsed)
	}
}

// ErrBusy 必须跟 lock.ErrHeld 是同一个 sentinel —— 两个 router handler 都靠
// errors.Is(err, ErrBusy) 决定返 409。哪天有人把它改写成一个新的 errors.New，
// 这条会红；否则 409 会静默退化成 500，而 500 恰好会让客户端去重试 ——
// 在"这个会话已经有一轮在跑"的场景下，重试是最不该做的动作。
func TestErrBusy_IsTheLockSentinel(t *testing.T) {
	if !errors.Is(lock.ErrHeld, ErrBusy) {
		t.Fatal("ErrBusy 必须转发 lock.ErrHeld，否则 router 的 409 映射会静默失效")
	}
}

// 没配 Redis（locker == nil）时行为必须跟接锁之前一模一样：永远拿得到执行权，
// 并且照旧打断上一轮。这是今天单实例部署走的路 —— 它一旦坏了，坏的是线上。
func TestAcquireTurnLock_WithoutRedisBehavesAsBefore(t *testing.T) {
	s := &AgentSession{
		id:          "no-redis",
		subscribers: make(map[chan SSEEvent]struct{}),
		// locker 留 nil = 未启用
	}

	prevCtx, prevCancel := context.WithCancel(context.Background())
	s.turnMu.Lock()
	s.turnCancel = prevCancel
	s.turnEpoch++
	s.turnMu.Unlock()

	h, err := s.acquireTurnLock()
	if err != nil {
		t.Fatalf("未启用锁时必须永远成功，实际 err=%v", err)
	}
	if h == nil {
		t.Fatal("必须返回可用的 no-op 句柄，否则 defer handle.Release() 会崩")
	}
	h.Release()

	select {
	case <-prevCtx.Done():
	default:
		t.Fatal("未启用锁时也要打断上一轮 —— 既有行为被改坏了")
	}
}
