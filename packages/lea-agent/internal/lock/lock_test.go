package lock

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"os"
	"testing"
	"time"
)

// 这组测试打真 Redis —— 锁的正确性全在 Redis 的原子性语义上（SET NX PX、EVAL 里的
// 比对后删），用假实现测等于在测假实现。没配 TEST_REDIS_URL 就跳过，CI 上没 Redis
// 时不会红。
//
//	TEST_REDIS_URL=redis://202.205.160.9:26379/0 go test ./internal/lock/
func testLocker(t *testing.T, ttl time.Duration) *Locker {
	t.Helper()
	url := os.Getenv("TEST_REDIS_URL")
	if url == "" {
		t.Skip("未配 TEST_REDIS_URL，跳过（锁的语义没法脱离真 Redis 验证）")
	}
	l, err := Open(url, ttl)
	if err != nil {
		t.Fatalf("连 Redis 失败: %v", err)
	}
	t.Cleanup(func() { _ = l.rdb.Close() })
	return l
}

// 每个用例用独立 session id，这样重复跑、并行跑都不会互相踩。
func uniqueSession(t *testing.T) string {
	t.Helper()
	var b [6]byte
	if _, err := rand.Read(b[:]); err != nil {
		t.Fatal(err)
	}
	return "test-" + t.Name() + "-" + hex.EncodeToString(b[:])
}

// 核心那条：同一会话第二个抢锁的必须被拒。这就是整个包存在的理由 ——
// 两个 pod 同时跑一轮会把 agent_messages 写成两个写者交错的产物。
func TestAcquire_SecondCallerIsRejected(t *testing.T) {
	l := testLocker(t, 10*time.Second)
	sid := uniqueSession(t)
	ctx := context.Background()

	h1, err := l.Acquire(ctx, sid)
	if err != nil {
		t.Fatalf("第一个该拿到锁: %v", err)
	}
	defer h1.Release()

	if _, err := l.Acquire(ctx, sid); !errors.Is(err, ErrHeld) {
		t.Fatalf("第二个必须吃 ErrHeld，实际 err=%v", err)
	}
}

// 不同会话之间互不影响 —— 锁的粒度是会话，不是全局。粒度搞错的话整个服务会
// 退化成一次只能跑一个对话。
func TestAcquire_DifferentSessionsDoNotBlock(t *testing.T) {
	l := testLocker(t, 10*time.Second)
	ctx := context.Background()

	h1, err := l.Acquire(ctx, uniqueSession(t)+"-a")
	if err != nil {
		t.Fatalf("会话 a 该拿到: %v", err)
	}
	defer h1.Release()

	h2, err := l.Acquire(ctx, uniqueSession(t)+"-b")
	if err != nil {
		t.Fatalf("会话 b 也该拿到（不同会话不该互斥）: %v", err)
	}
	defer h2.Release()
}

func TestRelease_FreesTheLock(t *testing.T) {
	l := testLocker(t, 10*time.Second)
	sid := uniqueSession(t)
	ctx := context.Background()

	h1, err := l.Acquire(ctx, sid)
	if err != nil {
		t.Fatal(err)
	}
	h1.Release()

	h2, err := l.Acquire(ctx, sid)
	if err != nil {
		t.Fatalf("放锁之后该能重新拿到: %v", err)
	}
	h2.Release()
}

// 重复 Release 不该 panic —— 调用点是 defer，而收尾路径上可能已经显式放过一次。
func TestRelease_IsIdempotent(t *testing.T) {
	l := testLocker(t, 10*time.Second)
	h, err := l.Acquire(context.Background(), uniqueSession(t))
	if err != nil {
		t.Fatal(err)
	}
	h.Release()
	h.Release() // 不该 panic（close(h.stop) 走了 sync.Once）
}

// 这条是分布式锁最容易写错的地方：**过期的持有者不能删掉后来者的锁**。
//
// 场景：pod A 卡住（GC、网络挂起）导致锁过期，B 抢到；A 恢复过来走收尾逻辑 Release。
// 如果 Release 是裸 DEL，A 就把 B 正在用的锁删了，于是 C 又能进来 —— 一次卡顿把
// 互斥彻底击穿，而且没有任何报错。
//
// 这里用短 TTL + 掐掉心跳来精确复现那个卡顿。
func TestRelease_DoesNotDeleteAnotherOwnersLock(t *testing.T) {
	l := testLocker(t, 600*time.Millisecond)
	sid := uniqueSession(t)
	ctx := context.Background()

	stale, err := l.Acquire(ctx, sid)
	if err != nil {
		t.Fatal(err)
	}
	// 模拟"进程卡死"：心跳停了但锁没放，于是锁会自然过期。
	stale.stopOnce.Do(func() { close(stale.stop) })
	<-stale.done

	// 等它过期，让位给下一个。
	time.Sleep(900 * time.Millisecond)

	fresh, err := l.Acquire(ctx, sid)
	if err != nil {
		t.Fatalf("过期之后新的持有者该拿到锁: %v", err)
	}
	defer fresh.Release()

	// 卡死的那个"恢复"了，走它的收尾 Release。
	stale.Release()

	// fresh 的锁必须还在 —— 否则第三个人也能进来，互斥就没了。
	if _, err := l.Acquire(ctx, sid); !errors.Is(err, ErrHeld) {
		t.Fatalf("过期持有者的 Release 把后来者的锁删了 —— 互斥被击穿，err=%v", err)
	}
}

// 心跳要能让锁活过 TTL。没有它，TTL 就得配得比最长的一轮还长，
// 而那样 pod 崩掉之后锁要闲置那么久。
func TestHeartbeat_KeepsLockAliveBeyondTTL(t *testing.T) {
	ttl := 900 * time.Millisecond // 心跳每 300ms 续一次
	l := testLocker(t, ttl)
	sid := uniqueSession(t)
	ctx := context.Background()

	h, err := l.Acquire(ctx, sid)
	if err != nil {
		t.Fatal(err)
	}
	defer h.Release()

	// 睡到远超一个 TTL。没有心跳的话这时候锁早没了。
	time.Sleep(3 * ttl)

	if _, err := l.Acquire(ctx, sid); !errors.Is(err, ErrHeld) {
		t.Fatalf("跑了 %s（TTL=%s）锁应当还被心跳续着，实际 err=%v", 3*ttl, ttl, err)
	}
}

// 持有者进程死了（SIGKILL，没人执行 Release）时锁必须自己过期，
// 否则这个会话从此永远发不出消息。
func TestExpiry_FreesLockWhenHolderDies(t *testing.T) {
	l := testLocker(t, 500*time.Millisecond)
	sid := uniqueSession(t)
	ctx := context.Background()

	h, err := l.Acquire(ctx, sid)
	if err != nil {
		t.Fatal(err)
	}
	// 停心跳、不放锁 = 进程被 SIGKILL。
	h.stopOnce.Do(func() { close(h.stop) })
	<-h.done

	time.Sleep(800 * time.Millisecond)

	h2, err := l.Acquire(ctx, sid)
	if err != nil {
		t.Fatalf("持有者死掉后锁该过期释放，实际 err=%v", err)
	}
	h2.Release()
}

// 没配 REDIS_URL 时 Shared() 返回 nil，而 nil 上的 Acquire/Release 必须能正常走完 ——
// 单实例部署（今天的现状）就靠这条，调用点不需要判空、不需要分叉。
func TestNilLocker_IsNoop(t *testing.T) {
	var l *Locker // Shared() 在未启用时返回的就是这个

	h, err := l.Acquire(context.Background(), "whatever")
	if err != nil {
		t.Fatalf("未启用时 Acquire 必须永远成功，实际 err=%v", err)
	}
	if h == nil {
		t.Fatal("未启用时也要返回可用的 no-op 句柄，否则调用点 defer h.Release() 会崩")
	}
	h.Release()
	h.Release()

	// 同一个 key 连拿两次也该成功 —— 未启用就是没有互斥，不该假装有。
	if _, err := l.Acquire(context.Background(), "whatever"); err != nil {
		t.Fatalf("未启用时不该产生互斥，实际 err=%v", err)
	}

	// 未启用时"有没有 pod 在跑"只能按本进程的内存回答，而这一层看不到内存，
	// 所以答 false。调用方（Manager.ForwardStop）据此走"没什么可转发"那条路，
	// 也就是接 pub/sub 之前的行为。
	held, err := l.IsHeld(context.Background(), "whatever")
	if err != nil {
		t.Fatalf("未启用时 IsHeld 不该报错，实际 err=%v", err)
	}
	if held {
		t.Error("未启用时 IsHeld 必须返 false")
	}
}

// IsHeld 是跨 pod 停止的路由依据：它把锁当路由表用，回答"全集群有没有在跑这个会话"。
// 答错的后果是静默的 —— 假 false 会让停止请求根本发不出去（前端看到 404「没什么在跑」，
// 而回答还在生成）。
func TestIsHeld_TracksLockLifecycle(t *testing.T) {
	l := testLocker(t, 10*time.Second)
	id := uniqueSession(t)

	held, err := l.IsHeld(context.Background(), id)
	if err != nil {
		t.Fatal(err)
	}
	if held {
		t.Fatal("还没人加锁就报告持有中")
	}

	h, err := l.Acquire(context.Background(), id)
	if err != nil {
		t.Fatal(err)
	}
	if held, err = l.IsHeld(context.Background(), id); err != nil || !held {
		t.Fatalf("持锁期间 IsHeld = %v (err=%v)，期望 true —— 停止请求会因此发不出去", held, err)
	}

	// 换个 session id 不能串台：全局停止频道下这道判断是唯一防止"停错人"的东西。
	if held, err = l.IsHeld(context.Background(), uniqueSession(t)); err != nil || held {
		t.Errorf("另一个会话的 IsHeld = %v (err=%v)，期望 false", held, err)
	}

	h.Release()
	if held, err = l.IsHeld(context.Background(), id); err != nil || held {
		t.Errorf("释放之后 IsHeld = %v (err=%v)，期望 false —— 会导致对已结束的 turn 转发停止请求", held, err)
	}
}
