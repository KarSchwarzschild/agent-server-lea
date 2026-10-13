package session

import (
	"context"
	"testing"

	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/runtime"
)

// 会话级模型切换 —— GetOrCreate 里"请求带的模型跟这份会话建起来时用的不一样"那条分支。
//
// ## 为什么这条分支需要单独盯着
//
// 模型跟工具数组一样，是在 engine.Build() 那一刻冻进引擎的，事后改不了。所以"换模型"
// 走的是跟挂载同一个机制：丢掉内存里那份引擎对象，下一条消息重建时用新模型。
//
// 难点在于这条分支把三件事叠在一起：**判断模型变了**（要跟 s.cfg.Model 比，而
// s.cfg.Model 是 lea-config 回填过的最终值，不是请求里那个字符串）、**Drop 可能失败**
// （这一轮还在跑，或者 SSE 订阅者还挂着），以及**失败后的降级**（保留旧会话跑完这一轮，
// 把重建推到下一次请求）。三者任一错掉的表现都是"用户选了模型但没生效"，而那在界面上
// 看不出来 —— 回答照样出来，只是出自另一个模型。
//
// 这里只测缓存命中路径。Drop 成功的那条会往下走完整重建，而重建要打 lea-config 的
// HTTP（LoadAgentConfig），归端到端冒烟 —— 跟 manager_test.go 的取舍一致。

func sessionWithModel(id, model string) *AgentSession {
	return &AgentSession{
		id:          id,
		tenantID:    "t1",
		userID:      "u1",
		cfg:         &runtime.AgentConfig{Model: model},
		subscribers: make(map[chan SSEEvent]struct{}),
	}
}

func (s *AgentSession) pendingRebuildForTest() bool {
	s.rebuildMu.Lock()
	defer s.rebuildMu.Unlock()
	return s.pendingRebuild
}

func TestModelSwitch_SameModelReusesSession(t *testing.T) {
	// 每条消息都带着模型名来（前端 sendMessageStream 总会带），所以"模型没变"是
	// 最热的那条路。它必须是纯缓存命中：在这里误判成变了，等于每条消息重建一次
	// 引擎，用户侧表现为每句话都要多等一个 lea-config 往返。
	s := sessionWithModel("s1", "qwen-plus")
	m := newTestManager(s)

	got, err := m.GetOrCreate(context.Background(), Params{
		SessionID: "s1", TenantID: "t1", UserID: "u1", Model: "qwen-plus",
	})
	if err != nil {
		t.Fatalf("GetOrCreate: %v", err)
	}
	if got != s {
		t.Fatal("模型没变时应该复用内存里那份会话")
	}
	if m.Count() != 1 {
		t.Errorf("会话不该被丢掉，count=%d", m.Count())
	}
	if s.pendingRebuildForTest() {
		t.Error("模型没变却标了待重建 —— 下一轮会白重建一次")
	}
}

func TestModelSwitch_EmptyModelDoesNotRebuild(t *testing.T) {
	// 请求不带模型 = "沿用这个会话现在的模型"，不是"切回 Agent 默认"。老客户端
	// （以及任何不带 model 字段的内部调用）走的就是这条，误判成变了会把人家的
	// 会话无端重建。
	s := sessionWithModel("s1", "glm-4-plus")
	m := newTestManager(s)

	for _, model := range []string{"", "   "} {
		got, err := m.GetOrCreate(context.Background(), Params{
			SessionID: "s1", TenantID: "t1", UserID: "u1", Model: model,
		})
		if err != nil {
			t.Fatalf("model=%q: %v", model, err)
		}
		if got != s {
			t.Fatalf("model=%q: 空模型应该复用会话", model)
		}
		if s.pendingRebuildForTest() {
			t.Fatalf("model=%q: 空模型不该触发重建", model)
		}
	}
}

func TestModelSwitch_BusySessionDefersRebuildAndKeepsOldModel(t *testing.T) {
	// 这一轮还在跑的时候切模型：**不能**把引擎掐掉换新的 —— 用户正等着这一轮的
	// 结果。于是保留旧会话跑完，标记待重建，下一次请求再换。
	//
	// 代价是这一轮仍然跑在旧模型上，而界面上选择器已经显示新模型了。下一个请求
	// 会带着同一个新模型重建并纠正，所以是自愈的；这条用例锁住的是"不自愈的那种
	// 坏法"：会话被丢了一半（返回 nil / 从表里消失），或者标志没置上导致永远不换。
	s := sessionWithModel("s1", "qwen-plus")
	s.turnCancel = func() {} // HasActiveTurn() 看的就是这个非 nil
	m := newTestManager(s)

	got, err := m.GetOrCreate(context.Background(), Params{
		SessionID: "s1", TenantID: "t1", UserID: "u1", Model: "glm-4-plus",
	})
	if err != nil {
		t.Fatalf("GetOrCreate: %v", err)
	}
	if got != s {
		t.Fatal("Drop 不掉的时候必须把旧会话原样还回去 —— 返回 nil 会让这一轮直接失败")
	}
	if m.Count() != 1 {
		t.Errorf("拒绝重建之后会话必须还在表里，count=%d", m.Count())
	}
	if !s.pendingRebuildForTest() {
		t.Error("没标待重建 —— 这一轮收尾时不会丢，模型就再也换不过来了")
	}
	if s.cfg.Model != "qwen-plus" {
		t.Errorf("这一轮该继续用旧模型，cfg.Model=%q", s.cfg.Model)
	}
}

func TestModelSwitch_SubscribersAlsoDeferRebuild(t *testing.T) {
	// turn 已经跑完、但 SSE 订阅者还挂着（前端没断流）时切模型。判据跟 Drop 里
	// 那条一致（HasActiveTurn || SubscriberCount > 0），这里确认模型变更这条路
	// 用的是同一个判据，而不是只看 turn。
	s := sessionWithModel("s1", "qwen-plus")
	s.subscribers[make(chan SSEEvent, 1)] = struct{}{}
	m := newTestManager(s)

	got, err := m.GetOrCreate(context.Background(), Params{
		SessionID: "s1", TenantID: "t1", UserID: "u1", Model: "glm-4-plus",
	})
	if err != nil {
		t.Fatalf("GetOrCreate: %v", err)
	}
	if got != s {
		t.Fatal("有订阅者挂着时应该原样返回旧会话 —— 丢掉会把它们的 channel 关掉，客户端断流")
	}
	if !s.pendingRebuildForTest() {
		t.Error("没标待重建，模型换不过来")
	}
}

func TestModelSwitch_DropsIdleSessionBeforeRebuilding(t *testing.T) {
	// 空闲会话切模型：Drop 成功，会话从表里消失，然后落到完整重建。
	//
	// 重建本身要打 lea-config 的 HTTP，这里测不了（也不该在单测里打），所以只断言
	// **丢掉这一半**发生了。如果哪天有人把这条分支改成"不 Drop 直接复用"，模型就
	// 永远不生效 —— 而那正是这条用例要拦的。
	s := sessionWithModel("s1", "qwen-plus")
	m := newTestManager(s)

	_, _ = m.GetOrCreate(context.Background(), Params{
		SessionID: "s1", TenantID: "t1", UserID: "u1", Model: "glm-4-plus",
	})

	if m.Count() != 0 {
		t.Errorf("空闲会话切模型时应该被丢掉以便重建，count=%d", m.Count())
	}
}

func TestModelSwitch_StillRefusesOtherUsersSession(t *testing.T) {
	// 模型变更的判断插在归属校验**之后**。插错位置的后果很实际：带上一个别人的
	// session_id 和一个不同的模型名，就能让对方的会话被丢掉重建 —— 一个不需要任何
	// 权限的拒绝服务。
	s := sessionWithModel("s1", "qwen-plus")
	m := newTestManager(s)

	got, err := m.GetOrCreate(context.Background(), Params{
		SessionID: "s1", TenantID: "t1", UserID: "u2", Model: "glm-4-plus",
	})
	if err == nil {
		t.Fatal("别人的会话必须被拒")
	}
	if got != nil {
		t.Fatal("返回了会话就等于泄露")
	}
	if m.Count() != 1 {
		t.Errorf("被拒的请求不该动到受害会话，count=%d", m.Count())
	}
	if s.pendingRebuildForTest() {
		t.Error("被拒的请求把受害会话标成了待重建 —— 等于越权让它重建")
	}
}
