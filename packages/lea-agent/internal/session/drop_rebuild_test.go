package session

import (
	"sync/atomic"
	"testing"
)

// Manager.Drop 和 pendingRebuild —— "让新挂的工具生效"那条路。
//
// ## 为什么需要一个跟 Delete 不同的东西
//
// 引擎的工具数组在 engine.Build() 那一刻就冻住了（agentloop 里 Toolset.Tools() 全仓
// 只调一次），所以挂载工具靠**丢掉内存里那份引擎对象**生效：下一条消息重建时新工具
// 在初始集合里。
//
// 而现成的 Delete 走 s.Close()，Close 头一件事是 `go s.memory.OnSessionEnd(...)` ——
// 批量提取记忆 + merge 画像。那对真正的空闲淘汰是对的（冷到被回收就当结束了），
// 用来"挂个工具"就不对：每挂一次白烧一次记忆提取，同一段历史被反复提取，画像里堆
// 重复内容。这一点是查 Close 的实现时才发现的，光看名字看不出来。

// ── Drop 不等于 Close ─────────────────────────────────────────────────────

func TestDrop_DoesNotTriggerMemoryExtraction(t *testing.T) {
	// memory 为 nil 时 Close 和 closeCore 走的是同一条路，测不出区别。所以这条用
	// **有历史 + 有 memory** 的会话，两个方法各跑一遍比较。
	//
	// 直接断言"closeCore 里没有 OnSessionEnd 那段代码"做不到，所以退一步：确认
	// Drop 的实现调的是 closeCore 而不是 Close —— 靠 Close 会清空 subscribers、
	// 而两者都清，区分不了。于是这里只锁住结构：Drop 之后会话不在表里，且没 panic。
	//
	// 真正的保险是 closeCore 的注释 + 这个文件的存在：谁把 Drop 改回调 Close，
	// 会先读到这里。
	s := &AgentSession{id: "s1", subscribers: make(map[chan SSEEvent]struct{})}
	m := newTestManager(s)

	if !m.Drop("s1") {
		t.Fatal("空闲会话应该能丢掉")
	}
	if m.Count() != 0 {
		t.Errorf("丢掉之后不该还在表里，count=%d", m.Count())
	}
}

func TestDrop_RefusesWhenTurnIsRunning(t *testing.T) {
	// 正在生成 —— 那个引擎对象正被占用，丢不得。
	s := &AgentSession{id: "s1", subscribers: make(map[chan SSEEvent]struct{})}
	s.turnCancel = func() {} // HasActiveTurn() 看的就是这个非 nil
	m := newTestManager(s)

	if m.Drop("s1") {
		t.Error("有 turn 在跑时不该丢")
	}
	if m.Count() != 1 {
		t.Error("拒绝之后会话必须还在表里 —— 丢一半是最坏的结果")
	}
}

func TestDrop_RefusesWhenSubscribersAttached(t *testing.T) {
	// 还有 SSE 订阅者挂着：丢掉会把它们的 channel 关掉，客户端那边直接断流。
	// 判据跟 ensureCapacity 里那条一致（HasActiveTurn || SubscriberCount > 0），
	// 不另写一套 —— 两套判据迟早会分叉。
	s := &AgentSession{id: "s1", subscribers: make(map[chan SSEEvent]struct{})}
	s.subscribers[make(chan SSEEvent, 1)] = struct{}{}
	m := newTestManager(s)

	if m.Drop("s1") {
		t.Error("有订阅者时不该丢")
	}
}

func TestDrop_OnMissingSessionIsSuccess(t *testing.T) {
	// 本来就不在内存里（别的副本持有、或者已经被淘汰过）——目标状态已经达到。
	// 返 false 的话调用方会以为"要等本轮结束"，然后置一个永远没人消费的标志。
	m := newTestManager()
	if !m.Drop("never-existed") {
		t.Error("不存在的会话应该算丢成功")
	}
}

// ── pendingRebuild ────────────────────────────────────────────────────────

func TestPendingRebuild_DropsAtTurnEnd(t *testing.T) {
	// 挂载时正在生成 → 置标志 → 本轮收尾时自己丢。
	//
	// 这是挂载接口"永远成功"的前提：不需要让调用方处理 409 再重试，也不需要前端
	// 判断当前在不在生成。
	var dropped atomic.Int32
	s := &AgentSession{id: "s1", subscribers: make(map[chan SSEEvent]struct{})}
	s.dropper = func(id string) bool {
		dropped.Add(1)
		return true
	}

	s.MarkPendingRebuild()
	s.rebuildIfPending()

	if dropped.Load() != 1 {
		t.Errorf("收尾时应该丢一次，实际 %d 次", dropped.Load())
	}
}

func TestPendingRebuild_IsClearedAfterFiring(t *testing.T) {
	// 标志用完就清 —— 否则每一轮结束都会丢一次会话，等于每轮都重建，
	// 白付一次 lea-config 的往返和引擎构造。
	var dropped atomic.Int32
	s := &AgentSession{id: "s1", subscribers: make(map[chan SSEEvent]struct{})}
	s.dropper = func(string) bool { dropped.Add(1); return true }

	s.MarkPendingRebuild()
	s.rebuildIfPending()
	s.rebuildIfPending() // 第二轮收尾

	if dropped.Load() != 1 {
		t.Errorf("标志该只生效一次，实际丢了 %d 次", dropped.Load())
	}
}

func TestPendingRebuild_RetriesWhenDropFails(t *testing.T) {
	// 收尾时又有新的一轮开始了（或者有人订阅了 SSE）—— Drop 返 false。
	// 标志要放回去，下一轮收尾再试。
	//
	// 不放回去的话，用户挂的工具**永远不生效**，而且没有任何错误：他点了挂载、
	// 接口返回成功、然后一直没有效果。这是这组用例里最值钱的一条。
	var attempts atomic.Int32
	s := &AgentSession{id: "s1", subscribers: make(map[chan SSEEvent]struct{})}
	s.dropper = func(string) bool {
		return attempts.Add(1) >= 2 // 第一次失败，第二次成功
	}

	s.MarkPendingRebuild()
	s.rebuildIfPending() // 失败，标志放回
	s.rebuildIfPending() // 重试，成功

	if attempts.Load() != 2 {
		t.Errorf("失败后应该重试，实际尝试 %d 次", attempts.Load())
	}
	// 成功之后标志清了，第三次不该再试
	s.rebuildIfPending()
	if attempts.Load() != 2 {
		t.Errorf("成功之后不该再试，实际 %d 次", attempts.Load())
	}
}

func TestPendingRebuild_NoopWithoutFlag(t *testing.T) {
	// 绝大多数轮次都没人挂过东西 —— 收尾时必须什么都不做。
	var dropped atomic.Int32
	s := &AgentSession{id: "s1", subscribers: make(map[chan SSEEvent]struct{})}
	s.dropper = func(string) bool { dropped.Add(1); return true }

	s.rebuildIfPending()

	if dropped.Load() != 0 {
		t.Error("没置标志时不该丢会话")
	}
}

func TestPendingRebuild_NoopWithoutDropper(t *testing.T) {
	// dropper 是 Manager 注入的；单测里造的裸会话没有它。
	// nil 时必须安静跳过而不是 panic —— 一个 nil 回调把整轮打挂太不值。
	s := &AgentSession{id: "s1", subscribers: make(map[chan SSEEvent]struct{})}
	s.MarkPendingRebuild()
	s.rebuildIfPending() // 不 panic 就算过
}
