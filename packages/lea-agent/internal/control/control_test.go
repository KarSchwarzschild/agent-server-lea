package control

import (
	"context"
	"errors"
	"os"
	"sync"
	"testing"
	"time"
)

// 这些测试要一个真 Redis。pub/sub 的行为（订阅建立的时机、投递、退订之后不再收）
// 没法用 mock 验证 —— mock 会按我以为的语义回答，而这里要验的恰恰是我以为的对不对。
//
//	TEST_REDIS_URL='redis://127.0.0.1:6379/0' go test ./internal/control/
func testBus(t *testing.T) *Bus {
	t.Helper()
	url := os.Getenv("TEST_REDIS_URL")
	if url == "" {
		t.Skip("TEST_REDIS_URL 未设置，跳过（需要真 Redis）")
	}
	b, err := Open(url)
	if err != nil {
		t.Fatalf("连 Redis 失败: %v", err)
	}
	t.Cleanup(func() { _ = b.Close() })
	return b
}

// collector 收订阅到的 session_id，带锁因为 handler 在订阅 goroutine 里跑。
type collector struct {
	mu  sync.Mutex
	got []string
	ch  chan string
}

func newCollector() *collector {
	return &collector{ch: make(chan string, 16)}
}

func (c *collector) handle(sessionID string) {
	c.mu.Lock()
	c.got = append(c.got, sessionID)
	c.mu.Unlock()
	select {
	case c.ch <- sessionID:
	default:
	}
}

func (c *collector) waitFor(t *testing.T, d time.Duration) (string, bool) {
	t.Helper()
	select {
	case id := <-c.ch:
		return id, true
	case <-time.After(d):
		return "", false
	}
}

func (c *collector) count() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.got)
}

// 基本回路：发出去的能收到，而且 session_id 原样过来。
func TestPublishStop_ReachesSubscriber(t *testing.T) {
	b := testBus(t)
	c := newCollector()

	unsub := b.SubscribeStop(c.handle)
	defer unsub()

	// Subscribe 是异步建立的，立刻 Publish 会打在订阅生效之前 —— pub/sub 没有
	// 堆积，那条消息就真的没了。这不是测试的瑕疵而是这个机制的性质，所以生产代码
	// 里也不能假设"刚订阅就能收到"（进程启动早于第一个请求，实践上够了）。
	waitForSubscriber(t, b)

	if err := b.PublishStop(context.Background(), "sess-abc"); err != nil {
		t.Fatalf("PublishStop 失败: %v", err)
	}

	got, ok := c.waitFor(t, 2*time.Second)
	if !ok {
		t.Fatal("订阅者没收到停止请求")
	}
	if got != "sess-abc" {
		t.Errorf("session_id = %q, want sess-abc", got)
	}
}

// 退订之后不能再收 —— 否则 Manager.Close 之后订阅 goroutine 还活着，
// 测试里每 NewManager 一次就漏一条，生产里发版排空时也停不干净。
func TestSubscribeStop_UnsubscribeStopsDelivery(t *testing.T) {
	b := testBus(t)
	c := newCollector()

	unsub := b.SubscribeStop(c.handle)
	waitForSubscriber(t, b)

	if err := b.PublishStop(context.Background(), "before"); err != nil {
		t.Fatalf("PublishStop 失败: %v", err)
	}
	if _, ok := c.waitFor(t, 2*time.Second); !ok {
		t.Fatal("退订前就收不到，测试前提不成立")
	}

	unsub()
	before := c.count()

	if err := b.PublishStop(context.Background(), "after"); err != nil {
		t.Fatalf("PublishStop 失败: %v", err)
	}
	time.Sleep(300 * time.Millisecond) // 给它机会犯错

	if c.count() != before {
		t.Errorf("退订之后仍在投递：收到了 %v", c.got)
	}
}

// 退订可以重复调（Manager.Close 有 closeOnce，但别让正确性依赖调用方的纪律）。
func TestSubscribeStop_UnsubscribeIsIdempotent(t *testing.T) {
	b := testBus(t)
	unsub := b.SubscribeStop(func(string) {})
	unsub()
	unsub() // 第二次不能 panic 也不能挂死
}

// 多个 pod 各自订阅，一条消息全都收到 —— 这正是全局频道的形状：投递是广播的，
// 过滤在各 pod 本地做（见 Manager.applyRemoteStop）。
func TestPublishStop_FanOutToAllSubscribers(t *testing.T) {
	b := testBus(t)
	a, bb := newCollector(), newCollector()

	unsubA := b.SubscribeStop(a.handle)
	defer unsubA()
	unsubB := b.SubscribeStop(bb.handle)
	defer unsubB()
	waitForSubscriberCount(t, b, 2)

	if err := b.PublishStop(context.Background(), "sess-fan"); err != nil {
		t.Fatalf("PublishStop 失败: %v", err)
	}

	for name, c := range map[string]*collector{"A": a, "B": bb} {
		if _, ok := c.waitFor(t, 2*time.Second); !ok {
			t.Errorf("订阅者 %s 没收到", name)
		}
	}
}

// 坏载荷不能把订阅 goroutine 带走 —— 它挂了之后这个 pod 就再也收不到任何停止
// 请求，而且完全静默。
func TestSubscribeStop_SurvivesGarbagePayload(t *testing.T) {
	b := testBus(t)
	c := newCollector()

	unsub := b.SubscribeStop(c.handle)
	defer unsub()
	waitForSubscriber(t, b)

	// 直接往频道里塞非 JSON，绕过 PublishStop
	if err := b.rdb.Publish(context.Background(), stopChannel, "not json at all").Err(); err != nil {
		t.Fatalf("publish 失败: %v", err)
	}
	// 紧跟一条正常的：能收到就说明上一条没把 goroutine 弄死
	if err := b.PublishStop(context.Background(), "sess-after-garbage"); err != nil {
		t.Fatalf("PublishStop 失败: %v", err)
	}

	got, ok := c.waitFor(t, 2*time.Second)
	if !ok {
		t.Fatal("坏载荷之后订阅就死了")
	}
	if got != "sess-after-garbage" {
		t.Errorf("收到 %q，期望 sess-after-garbage（坏载荷不该被当成 session_id）", got)
	}
}

// 空 session_id 不能触发 handler —— 那会变成 Manager 里一次对 "" 的查表。
func TestSubscribeStop_IgnoresEmptySessionID(t *testing.T) {
	b := testBus(t)
	c := newCollector()

	unsub := b.SubscribeStop(c.handle)
	defer unsub()
	waitForSubscriber(t, b)

	if err := b.rdb.Publish(context.Background(), stopChannel, `{"session_id":"","origin":"x"}`).Err(); err != nil {
		t.Fatalf("publish 失败: %v", err)
	}
	if err := b.PublishStop(context.Background(), "real"); err != nil {
		t.Fatalf("PublishStop 失败: %v", err)
	}

	got, _ := c.waitFor(t, 2*time.Second)
	if got != "real" {
		t.Errorf("收到 %q —— 空 session_id 应该被丢掉", got)
	}
}

// ── 未启用时的行为（不需要 Redis）─────────────────────────────
//
// 这两条是整个设计里最容易写错的地方，所以单独钉住。
//
// 发布必须**失败**：nil 当成功的话，acquireTurnLock 会以为远端收到了让位请求，
// 于是白等五秒才返 409。如实报错它就能立刻拒。
func TestNilBus_PublishFails(t *testing.T) {
	var b *Bus
	err := b.PublishStop(context.Background(), "sess")
	if !errors.Is(err, ErrUnavailable) {
		t.Errorf("nil bus 的 PublishStop = %v, want ErrUnavailable —— "+
			"静默成功会让调用方去等一个永远不来的让位", err)
	}
}

// 订阅必须**静默成功**：单实例下没有别的 pod 会发消息，收不到是对的，
// 而且返回的退订函数要能安全调用（Manager.Close 无条件调它）。
func TestNilBus_SubscribeIsNoop(t *testing.T) {
	var b *Bus
	unsub := b.SubscribeStop(func(string) { t.Error("nil bus 不该投递任何消息") })
	if unsub == nil {
		t.Fatal("退订函数是 nil —— Manager.Close 会 panic")
	}
	unsub()
}

func TestNilBus_CloseIsNoop(t *testing.T) {
	var b *Bus
	if err := b.Close(); err != nil {
		t.Errorf("nil bus 的 Close = %v, want nil", err)
	}
}

// ── 辅助 ────────────────────────────────────────────────────
//
// waitForSubscriber 等订阅真的在 Redis 上建立起来。
//
// 用 PUBSUB NUMSUB 而不是 sleep：sleep 多长都是猜的，而这个是 Redis 自己的答案。
// pub/sub 不堆积，订阅生效之前发的消息直接丢，所以这一步不确认的话测试会随机假红。
func waitForSubscriber(t *testing.T, b *Bus) {
	t.Helper()
	waitForSubscriberCount(t, b, 1)
}

func waitForSubscriberCount(t *testing.T, b *Bus, want int64) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		res, err := b.rdb.PubSubNumSub(context.Background(), stopChannel).Result()
		if err == nil && res[stopChannel] >= want {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("等了 3 秒仍不足 %d 个订阅者", want)
}
