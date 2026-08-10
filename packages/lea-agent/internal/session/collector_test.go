package session

import (
	"encoding/json"
	"strings"
	"testing"

	"gitlab.ouc-online.com.cn/aibase/agentloop/llm"
	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
)

func TestCollector_ToolCalls(t *testing.T) {
	c := newCollector()

	c.onToolEvent(tool.Event{
		Type: tool.EventTypeStarted, ToolName: "kb_search", ToolUseID: "tc-1",
		Input: json.RawMessage(`{"query":"policy"}`),
	})
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeCompleted, ToolName: "kb_search", ToolUseID: "tc-1",
		Message: "3 hits", DurationMs: 42,
	})

	extra := c.toExtra()
	if extra == nil {
		t.Fatal("extra empty")
	}
	tcs, ok := extra["tool_calls"].([]map[string]any)
	if !ok || len(tcs) != 1 {
		t.Fatalf("tool_calls not 1: %v", extra)
	}
	if tcs[0]["status"] != "success" || tcs[0]["tool"] != "kb_search" || tcs[0]["durationMs"] != 42 {
		t.Fatalf("bad tool_call: %v", tcs[0])
	}
	if _, isSandbox := extra["sandbox_calls"]; isSandbox {
		t.Fatal("kb_search should not appear in sandbox_calls")
	}
}

func TestCollector_SandboxCalls(t *testing.T) {
	c := newCollector()
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeStarted, ToolName: "code_interpreter", ToolUseID: "sb-1",
		Input: json.RawMessage(`{"language":"python","code":"print(1+1)"}`),
	})
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeCompleted, ToolName: "code_interpreter", ToolUseID: "sb-1",
		DurationMs: 120,
	})
	extra := c.toExtra()
	scs, ok := extra["sandbox_calls"].([]map[string]any)
	if !ok || len(scs) != 1 {
		t.Fatalf("sandbox_calls not 1: %v", extra)
	}
	if scs[0]["language"] != "python" || scs[0]["status"] != "success" {
		t.Fatalf("bad sandbox_call: %v", scs[0])
	}
	if _, hasTool := extra["tool_calls"]; hasTool {
		t.Fatal("code_interpreter should NOT appear in tool_calls")
	}
}

func TestCollector_SandboxArtifactsFromProgress(t *testing.T) {
	c := newCollector()
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeStarted, ToolName: "code_interpreter", ToolUseID: "sb-2",
		Input: json.RawMessage(`{"language":"python","code":"import pandas"}`),
	})
	// H5.C: code_interpreter 在返回前 emit 一条 Progress，Data 是解析后的 map
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeProgress, ToolName: "code_interpreter", ToolUseID: "sb-2",
		Data: map[string]any{
			"artifacts":   []any{"output.xlsx", "chart.png"},
			"stdout":      "done\n",
			"duration_ms": float64(88),
		},
	})
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeCompleted, ToolName: "code_interpreter", ToolUseID: "sb-2",
		Message: "completed", DurationMs: 88,
	})
	scs := c.toExtra()["sandbox_calls"].([]map[string]any)
	if len(scs) != 1 {
		t.Fatalf("want 1 sandbox_call, got %d", len(scs))
	}
	arts, ok := scs[0]["artifacts"].([]string)
	if !ok || len(arts) != 2 || arts[0] != "output.xlsx" || arts[1] != "chart.png" {
		t.Fatalf("bad artifacts: %v", scs[0]["artifacts"])
	}
	if scs[0]["stdoutPreview"] != "done\n" {
		t.Fatalf("stdoutPreview lost or overwritten: %v", scs[0]["stdoutPreview"])
	}
	if scs[0]["status"] != "success" {
		t.Fatalf("status not success: %v", scs[0]["status"])
	}
}

func TestCollector_SandboxFailedKeepsRealOutput(t *testing.T) {
	// 沙箱执行报错时 code_interpreter.Run 直接 return，Progress 压根没 emit。
	// 真实输出只在引擎 Failed 事件的 ev.Output 里（按 stream 分类），必须落库 ——
	// 之前这里只把字面 Message 当 stdout 写，traceback 全丢。
	c := newCollector()
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeStarted, ToolName: "code_interpreter", ToolUseID: "sb-3",
		Input: json.RawMessage(`{"language":"python","code":"subprocess.run(['node'])"}`),
	})
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeFailed, ToolName: "code_interpreter", ToolUseID: "sb-3",
		Message: "completed with error",
		Output: []tool.OutputLine{
			{Stream: tool.OutputStreamStdout, Text: "installing..."},
			{Stream: tool.OutputStreamStderr, Text: "Traceback (most recent call last):"},
			{Stream: tool.OutputStreamStderr, Text: "FileNotFoundError: 'node'"},
		},
	})
	sc := c.toExtra()["sandbox_calls"].([]map[string]any)[0]
	if sc["status"] != "error" {
		t.Fatalf("status not error: %v", sc["status"])
	}
	if sc["stdoutPreview"] != "installing..." {
		t.Fatalf("stdout lost: %v", sc["stdoutPreview"])
	}
	stderr, _ := sc["stderrPreview"].(string)
	if !strings.Contains(stderr, "FileNotFoundError") {
		t.Fatalf("stderr lost: %v", sc["stderrPreview"])
	}
	// 有真实输出时不该再拿引擎的状态描述冒充输出
	if _, has := sc["message"]; has {
		t.Fatalf("message should not be set when real output exists: %v", sc)
	}
}

func TestCollector_SandboxFailedNoOutputKeepsMessageSeparate(t *testing.T) {
	// 连 ev.Output 都空时，引擎的状态描述落到 message 字段，不冒充 stdout。
	c := newCollector()
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeStarted, ToolName: "code_interpreter", ToolUseID: "sb-4",
		Input: json.RawMessage(`{"language":"python","code":"x"}`),
	})
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeFailed, ToolName: "code_interpreter", ToolUseID: "sb-4",
		Message: "cancelled",
	})
	sc := c.toExtra()["sandbox_calls"].([]map[string]any)[0]
	if sc["message"] != "cancelled" {
		t.Fatalf("message fallback missing: %v", sc)
	}
	if _, has := sc["stdoutPreview"]; has {
		t.Fatalf("status text must not be written as stdout: %v", sc)
	}
}

func TestCollector_ToolFailedPrefersOutputOverMessage(t *testing.T) {
	// 通用工具分支同理：报错时 result 要是真实输出，不是 "completed with error"
	c := newCollector()
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeStarted, ToolName: "kb_search", ToolUseID: "t-9",
		Input: json.RawMessage(`{"query":"x"}`),
	})
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeFailed, ToolName: "kb_search", ToolUseID: "t-9",
		Message: "completed with error",
		Output: []tool.OutputLine{
			{Stream: tool.OutputStreamStderr, Text: "KB search failed: connection refused"},
		},
	})
	tc := c.toExtra()["tool_calls"].([]map[string]any)[0]
	result, _ := tc["result"].(string)
	if !strings.Contains(result, "connection refused") {
		t.Fatalf("real error lost, got: %v", tc["result"])
	}
}

func TestCollector_Reasoning(t *testing.T) {
	c := newCollector()
	c.onThinking("Let me ")
	c.onThinking("think about ")
	c.onThinking("this.")
	extra := c.toExtra()
	blocks, ok := extra["reasoning_blocks"].([]map[string]any)
	if !ok || len(blocks) != 1 {
		t.Fatalf("reasoning_blocks not 1: %v", extra)
	}
	if blocks[0]["reasoning"] != "Let me think about this." {
		t.Fatalf("bad reasoning: %v", blocks[0])
	}
}

func TestCollector_EmptyReturnsNil(t *testing.T) {
	c := newCollector()
	if c.toExtra() != nil {
		t.Fatal("empty collector should return nil extra")
	}
	// content-only 也应返回 nil（response_text 走另一条道，不进 extra）
	c.onDelta("hello")
	if c.toExtra() != nil {
		t.Fatal("delta-only should also return nil extra")
	}
	if c.responseText() != "hello" {
		t.Fatalf("responseText=%q", c.responseText())
	}
}

func TestCollector_SegmentsPreserveOrder(t *testing.T) {
	c := newCollector()

	// 模拟一次真实交错：delta → tool → delta → sandbox → delta
	c.onDelta("让我先搜一下。")
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeStarted, ToolName: "kb_search", ToolUseID: "tc-1",
		Input: json.RawMessage(`{"query":"climate"}`),
	})
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeCompleted, ToolName: "kb_search", ToolUseID: "tc-1",
		Message: "found",
	})
	c.onDelta("找到了，接下来跑一段代码。")
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeStarted, ToolName: "code_interpreter", ToolUseID: "sb-1",
		Input: json.RawMessage(`{"language":"python","code":"print(1)"}`),
	})
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeCompleted, ToolName: "code_interpreter", ToolUseID: "sb-1",
	})
	c.onDelta("完成了。")

	extra := c.toExtra()
	segs, ok := extra["segments"].([]map[string]any)
	if !ok {
		t.Fatalf("segments missing or wrong type: %v", extra)
	}
	// 期望顺序: text / tool[0] / text / sandbox[0] / text
	want := []struct {
		kind  string
		index int
	}{
		{"text", -1},
		{"tool", 0},
		{"text", -1},
		{"sandbox", 0},
		{"text", -1},
	}
	if len(segs) != len(want) {
		t.Fatalf("segment count = %d, want %d: %v", len(segs), len(want), segs)
	}
	for i, w := range want {
		got := segs[i]
		if got["kind"] != w.kind {
			t.Fatalf("seg[%d].kind = %v, want %v", i, got["kind"], w.kind)
		}
		if w.index >= 0 && got["index"] != w.index {
			t.Fatalf("seg[%d].index = %v, want %d", i, got["index"], w.index)
		}
	}
}

func TestCollector_SinglePlainTextNoSegments(t *testing.T) {
	c := newCollector()
	c.onDelta("hello")
	c.onDelta(" world")
	extra := c.toExtra()
	if extra != nil {
		t.Fatalf("pure text turn should produce nil extra, got: %v", extra)
	}
}

func TestCollector_FailedTool(t *testing.T) {
	c := newCollector()
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeStarted, ToolName: "web_search", ToolUseID: "tc-x",
	})
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeFailed, ToolName: "web_search", ToolUseID: "tc-x",
		Message: "timeout",
	})
	tcs := c.toExtra()["tool_calls"].([]map[string]any)
	if tcs[0]["status"] != "error" || tcs[0]["result"] != "timeout" {
		t.Fatalf("bad failure mapping: %v", tcs[0])
	}
}

// ── 用量（计费依据）──────────────────────────────────────────
//
// 这几条钉住"一笔都不能丢"。丢用量不像丢时间线那样有可见症状 —— 前端照常渲染，
// 只是 chat_messages.token_count 是 0，账对不上要到月底才发现。

func TestCollector_MainUsageAlwaysPersisted(t *testing.T) {
	// 一轮只调了模型、没派活、没调工具 —— 仍然要计费。
	c := newCollector()
	c.onDelta("hi")
	c.setMainUsage(1200, 45, 900, 1, nil)

	u, ok := c.toExtra()["usage"].(map[string]any)
	if !ok {
		t.Fatal("没有 usage —— 纯对话轮次的 token 也是要计费的")
	}
	if u["totalInputTokens"] != 1200 || u["totalOutputTokens"] != 45 {
		t.Fatalf("total 不对: %v", u)
	}
	main := u["main"].(map[string]any)
	if main["peakInputTokens"] != 900 || main["iterations"] != 1 {
		t.Fatalf("main 不对: %v", main)
	}
}

func TestCollector_AgentUsageIsNotDropped(t *testing.T) {
	// agent_usage 不是工具生命周期事件。onToolEvent 早先只认
	// started/progress/completed，这类事件流过就被丢掉 —— 于是所有子会话的开销
	// 都不落库。
	c := newCollector()
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeAgentUsage, ToolName: "ask_expert", ToolUseID: "ae-1",
		AgentName: "文档处理专家", InputTokens: 455680, OutputTokens: 3790, DurationMs: 157000,
	})
	c.setMainUsage(16518, 879, 6180, 3, nil)

	u := c.toExtra()["usage"].(map[string]any)
	agents, ok := u["agents"].([]map[string]any)
	if !ok || len(agents) != 1 {
		t.Fatalf("子会话用量丢了: %v", u)
	}
	if agents[0]["agentName"] != "文档处理专家" || agents[0]["inputTokens"] != 455680 {
		t.Fatalf("子会话字段不对: %v", agents[0])
	}
	// 全链路总量 = 主会话 + 子会话，不是只有主会话
	if u["totalInputTokens"] != 16518+455680 {
		t.Fatalf("total 没把子会话算进去: %v", u["totalInputTokens"])
	}
}

func TestCollector_RepeatedDispatchKeepsEveryEntry(t *testing.T) {
	// 同一个专家被派两次是两笔独立开销。合并或去重会让"派一次成功"和
	// "派两次其中一次失败重试"在账上长得一样。
	c := newCollector()
	for i, in := range []int{455680, 504458} {
		c.onToolEvent(tool.Event{
			Type: tool.EventTypeAgentUsage, ToolName: "ask_expert",
			ToolUseID: string(rune('a' + i)), AgentName: "文档处理专家",
			InputTokens: in, OutputTokens: 100,
		})
	}
	agents := c.toExtra()["usage"].(map[string]any)["agents"].([]map[string]any)
	if len(agents) != 2 {
		t.Fatalf("同名专家的两次派活被折叠了: %d 条", len(agents))
	}
}

func TestCollector_ZeroUsageStaysOut(t *testing.T) {
	// 全零意味着 setMainUsage 没被调过（没跑 turn / 上游没给 Usages）。落一堆 0
	// 会让"真花了 0"和"没测到"在库里无法区分。
	c := newCollector()
	c.onDelta("hello")
	if extra := c.toExtra(); extra != nil {
		t.Fatalf("没有用量时不该落 usage: %v", extra)
	}
}

func TestCollector_UsageTotalsMatchesExtra(t *testing.T) {
	// usageTotals 供 token_count 列用，toExtra 供细账用 —— 两者必须一致，
	// 否则列和 JSON 对不上，对账时不知道信哪个。
	c := newCollector()
	c.setMainUsage(1000, 100, 800, 2, nil)
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeAgentUsage, ToolName: "Task", ToolUseID: "t-1",
		AgentName: "general-purpose", InputTokens: 5000, OutputTokens: 500,
	})
	in, out := c.usageTotals()
	u := c.toExtra()["usage"].(map[string]any)
	if in != u["totalInputTokens"] || out != u["totalOutputTokens"] {
		t.Fatalf("usageTotals(%d,%d) 与 extra(%v,%v) 不一致",
			in, out, u["totalInputTokens"], u["totalOutputTokens"])
	}
}

// ── handoff 链的用量不能重复计 ────────────────────────────────
//
// 链没有"主会话"：每条 leg 都是独立会话，各自结束时通过 bridge 发 agent_usage。把链的
// 累计 usages 再填进 main 就是把每条 leg 计两次。
//
// run3 实测过这个 bug：main 87,995（三条 leg 之和）+ agents 555,538（含同样那三条）
// = 落库 643,533，真实是 555,538，虚高 16%。**虚高比缺失更糟** —— 缺失查得出来
// （token_count=0 很显眼），虚高看起来就是个正常数字，要到对账才发现。

func TestChainUsageDoesNotDoubleCountLegs(t *testing.T) {
	c := newCollector()
	// 三条 leg 各自报自己的用量（真实链路里由引擎的 bridge 发出）。
	for _, leg := range []struct {
		name string
		id   string
		in   int
		out  int
	}{
		{"调度员", "leg_1_调度员", 9_413, 457},
		{"文档处理专家", "leg_2_文档处理专家", 20_877, 580},
		{"验收员", "leg_3_验收员", 57_705, 672},
	} {
		c.onToolEvent(tool.Event{
			Type: tool.EventTypeAgentUsage, ToolName: "handoff_leg", ToolUseID: leg.id,
			AgentName: leg.name, InputTokens: leg.in, OutputTokens: leg.out,
		})
	}
	// 子代理挂在第二条 leg 下 —— 它是这条链最大的一笔。
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeAgentUsage, ToolName: "Task", ToolUseID: "call_b8de",
		ParentToolUseID: "leg_2_文档处理专家",
		AgentName:       "general-purpose", InputTokens: 467_543, OutputTokens: 7_723,
	})
	// 链的收尾：只记形状，不记 token。
	c.setChainUsage(15, 5_729, nil)

	u := c.toExtra()["usage"].(map[string]any)
	wantIn := 9_413 + 20_877 + 57_705 + 467_543
	if u["totalInputTokens"] != wantIn {
		t.Errorf("totalInputTokens = %v, want %d —— leg 被重复计入 main 了", u["totalInputTokens"], wantIn)
	}
	main := u["main"].(map[string]any)
	if main["inputTokens"] != 0 || main["outputTokens"] != 0 {
		t.Errorf("链没有主会话，main 的 token 必须是 0，got %v", main)
	}
	// 但轮数要在 —— 前端那个"打了多少次模型"的数字读它，而它不是加法。
	if main["iterations"] != 15 {
		t.Errorf("iterations = %v, want 15", main["iterations"])
	}
	// 零必须是自解释的：读数据的人要能分清"这是链"和"用量没测到"。
	if u["topology"] != "handoff" {
		t.Errorf("链要标 topology，否则 main 里的 0 跟'没测到'长得一样: %v", u)
	}
	// token_count 列和 JSON 细账必须一致。
	in, _ := c.usageTotals()
	if in != wantIn {
		t.Errorf("usageTotals=%d 与 extra=%d 不一致", in, wantIn)
	}
}

// 中途失败的那轮也要落用量 —— 花掉的钱不因为失败而退回。
//
// handoff 链最容易长这个形状：前面几棒全跑完了，某一棒预算耗尽，整条链返错。此前
// agent_session 的 err 分支不调 recordTurnUsage，于是那些已完成的 leg 一笔都不记。
// 这种丢失没有可见症状：前端照常显示错误，只有对账时才发现账单少一笔。
func TestChainUsagePersistsEvenWhenTheChainFailed(t *testing.T) {
	c := newCollector()
	// 两棒跑完了，第三棒预算耗尽 —— 前两棒的开销已经发生。
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeAgentUsage, ToolName: "handoff_leg", ToolUseID: "leg_1_调度员",
		AgentName: "调度员", InputTokens: 3_035, OutputTokens: 124,
	})
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeAgentUsage, ToolName: "handoff_leg", ToolUseID: "leg_2_文档处理专家",
		AgentName: "文档处理专家", InputTokens: 77_047, OutputTokens: 2_700,
	})
	c.setChainUsage(14, 6_088, nil)

	u, ok := c.toExtra()["usage"].(map[string]any)
	if !ok {
		t.Fatal("失败的链也必须落 usage —— 前面几棒的钱已经花了")
	}
	want := 3_035 + 77_047
	if u["totalInputTokens"] != want {
		t.Errorf("totalInputTokens = %v, want %d", u["totalInputTokens"], want)
	}
	in, _ := c.usageTotals()
	if in != want {
		t.Errorf("token_count 列要拿到同一个数: %d != %d", in, want)
	}
}

// 星型一个字没变：main 有 token，没有 topology 标记，total 是两者之和。
func TestStarUsageStillCountsMainPlusAgents(t *testing.T) {
	c := newCollector()
	c.setMainUsage(16_518, 879, 6_180, 3, nil)
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeAgentUsage, ToolName: "ask_expert", ToolUseID: "ae-1",
		AgentName: "文档处理专家", InputTokens: 455_680, OutputTokens: 3_790,
	})
	u := c.toExtra()["usage"].(map[string]any)
	if u["totalInputTokens"] != 16_518+455_680 {
		t.Errorf("星型 total 应当是 main+agents: %v", u["totalInputTokens"])
	}
	if u["main"].(map[string]any)["inputTokens"] != 16_518 {
		t.Errorf("星型的团长开销必须留在 main: %v", u["main"])
	}
	if _, has := u["topology"]; has {
		t.Errorf("星型不该带 topology 标记 —— 加字段会让前端以为拓扑变了: %v", u)
	}
}

// ── 逐轮用量 ────────────────────────────────────────────────
//
// 合计答不了"钱花在哪"：一个 turn 的累计输入是 N 轮历史的叠加，第 k 轮的输入已经
// 包含前 k-1 轮的全部内容，所以同一份载荷被重复计了很多次。实测这个缺口是 28 倍
// —— 工具调用载荷去重后 8.8 万字节（约 22k tok），而全链路累计 61.5 万 tok。
// 要判断"把 skill 正文挡在专家上下文外"省了多少，只能看逐轮的输入增长。

func TestPerRoundUsageRecordsEachCallAndDelta(t *testing.T) {
	rounds := perRoundUsage([]*llm.Usage{
		{InputTokens: 3_000, OutputTokens: 40},
		{InputTokens: 12_500, OutputTokens: 80},
		{InputTokens: 14_000, OutputTokens: 25},
	})
	if len(rounds) != 3 {
		t.Fatalf("want 3 rounds, got %d", len(rounds))
	}
	if rounds[0]["round"] != 1 || rounds[0]["inputTokens"] != 3_000 {
		t.Errorf("round 1 wrong: %v", rounds[0])
	}
	// 第一轮没有前一轮，不该有 delta —— 落一个 0 会被误读成"这轮没新增内容"。
	if _, has := rounds[0]["inputDelta"]; has {
		t.Errorf("first round must carry no delta: %v", rounds[0])
	}
	// delta 是这一轮真正新增的上下文，历史重传的部分不算在里面。9500 就是那次
	// 大载荷（skill 正文 / 一页 slides 的源码）进上下文的时刻。
	if rounds[1]["inputDelta"] != 9_500 {
		t.Errorf("round 2 delta = %v, want 9500", rounds[1]["inputDelta"])
	}
}

// 中途失败的轮次（provider 还没应答就断了）没有 usage。跳过而不是记成 0：
// 记 0 会让"这轮真没花钱"和"这轮没测到"在账上长得一样。
func TestPerRoundUsageSkipsNilRounds(t *testing.T) {
	rounds := perRoundUsage([]*llm.Usage{
		{InputTokens: 100, OutputTokens: 10},
		nil,
		{InputTokens: 300, OutputTokens: 20},
	})
	if len(rounds) != 2 {
		t.Fatalf("nil 轮次不该落条目，got %d", len(rounds))
	}
}

// 缓存命中的输入 token 计价不同，混进合计就算不出真实费用。
func TestPerRoundUsageKeepsCacheFieldsOnlyWhenSet(t *testing.T) {
	rounds := perRoundUsage([]*llm.Usage{
		{InputTokens: 1_000, OutputTokens: 10, CacheReadInputTokens: 800},
		{InputTokens: 2_000, OutputTokens: 10},
	})
	if rounds[0]["cacheReadInputTokens"] != 800 {
		t.Errorf("缓存读取没记下来: %v", rounds[0])
	}
	// cachedInputTokens 走另一路口径：它是 inputTokens 的**子集**，不是加项。
	// 两者必须分开落，否则下游把它们相加就会把同一批 token 计两次费。
	cached := perRoundUsage([]*llm.Usage{
		{InputTokens: 21_264, OutputTokens: 310, CachedInputTokens: 11_500},
		{InputTokens: 2_000, OutputTokens: 10},
	})
	if cached[0]["cachedInputTokens"] != 11_500 {
		t.Errorf("缓存命中量没记下来 —— 没有它就分不清'缓存没生效'和'测不出缓存': %v", cached[0])
	}
	if cached[0]["inputTokens"] != 21_264 {
		t.Errorf("inputTokens 不该被缓存量改动（它本来就含缓存）: %v", cached[0])
	}
	if _, has := cached[0]["cacheReadInputTokens"]; has {
		t.Errorf("两路口径混了，下游相加会重复计费: %v", cached[0])
	}
	if _, has := cached[1]["cachedInputTokens"]; has {
		t.Errorf("零值不该落: %v", cached[1])
	}
	// 没命中缓存时不落字段 —— 每轮都带两个 0 会把记录撑大一倍。
	if _, has := rounds[1]["cacheReadInputTokens"]; has {
		t.Errorf("零值不该落: %v", rounds[1])
	}
}

func TestExtraCarriesPerRoundBreakdown(t *testing.T) {
	c := newCollector()
	c.onDelta("x")
	c.setMainUsage(15_500, 75, 12_500, 2, perRoundUsage([]*llm.Usage{
		{InputTokens: 3_000, OutputTokens: 40},
		{InputTokens: 12_500, OutputTokens: 35},
	}))

	main := c.toExtra()["usage"].(map[string]any)["main"].(map[string]any)
	rs, ok := main["rounds"].([]map[string]any)
	if !ok || len(rs) != 2 {
		t.Fatalf("逐轮明细没落进 extra: %v", main)
	}
	// 峰值必须是单轮最大值，不是合计 —— 这两个数回答不同的问题。
	if main["peakInputTokens"] != 12_500 {
		t.Errorf("peak = %v, want 12500", main["peakInputTokens"])
	}
}

// 子会话的峰值和轮数要透传：委派把活挪进子会话，父会话峰值下降而两边合计都上升，
// 只看合计会把"这个改动值不值"看反。
// 三层链（团长 → 专家 → 子代理）的归属必须能从落库数据还原。
//
// 这一列此前恒等于 toolCallId —— 每条都是自己的父亲 —— 起因在引擎事件桥把
// agent_usage 的 ToolUseID 和 ParentToolUseID 填成了同一个值。后果是 t6 那轮的
// 278k（文档处理专家派的子代理）在库里看起来跟专家自己平级，判断不出是谁花的。
func TestAgentUsageKeepsTheDelegationChain(t *testing.T) {
	c := newCollector()
	// 团长直接派的专家：没有上一层。
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeAgentUsage, ToolName: "ask_expert", ToolUseID: "call_expert",
		AgentName: "文档处理专家", InputTokens: 19_396, OutputTokens: 466,
	})
	// 那个专家派下去的子代理：父亲是上面那次 ask_expert。
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeAgentUsage, ToolName: "Task", ToolUseID: "call_task",
		ParentToolUseID: "call_expert",
		AgentName:       "general-purpose", InputTokens: 278_085, OutputTokens: 4_594,
	})

	agents := c.toExtra()["usage"].(map[string]any)["agents"].([]map[string]any)
	if len(agents) != 2 {
		t.Fatalf("want 2 entries, got %d", len(agents))
	}
	// 直挂主会话的那条不该带 parentId：空值的意思是"挂在 turn 上"，
	// 落一个等于自己的值会让链无法还原。
	if _, has := agents[0]["parentId"]; has {
		t.Errorf("团长直派的专家不该有 parentId: %v", agents[0])
	}
	if agents[1]["parentId"] != "call_expert" {
		t.Errorf("子代理的 parentId = %v, want call_expert —— 没有它就不知道 278k 是哪个专家花的",
			agents[1]["parentId"])
	}
	if agents[1]["parentId"] == agents[1]["toolCallId"] {
		t.Error("parentId 不能等于 toolCallId（自己当自己的父亲）")
	}
}

// 用量事件由引擎沿途补 ToolUseID / ToolName，所以它必须在那道早退之前被接住。
// 丢计费没有可见症状：前端照常渲染，只有 token_count 是 0。
func TestAgentUsageSurvivesTheIdentityEarlyReturn(t *testing.T) {
	c := newCollector()
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeAgentUsage, AgentName: "文档处理专家",
		InputTokens: 20_318, OutputTokens: 712,
	})
	u, ok := c.toExtra()["usage"].(map[string]any)
	if !ok {
		t.Fatal("没有 ToolUseID/ToolName 的用量被早退吞掉了 —— 这是一整轮的计费")
	}
	if u["totalInputTokens"] != 20_318 {
		t.Errorf("total = %v, want 20318", u["totalInputTokens"])
	}
}

func TestAgentUsageCarriesPeakAndIterations(t *testing.T) {
	c := newCollector()
	c.onToolEvent(tool.Event{
		Type: tool.EventTypeAgentUsage, ToolName: "ask_expert", ToolUseID: "ae-1",
		AgentName: "文档处理专家", InputTokens: 20_318, OutputTokens: 900,
		PeakInputTokens: 8_400, Iterations: 4,
	})
	agents := c.toExtra()["usage"].(map[string]any)["agents"].([]map[string]any)
	if agents[0]["peakInputTokens"] != 8_400 || agents[0]["iterations"] != 4 {
		t.Fatalf("子会话峰值/轮数丢了: %v", agents[0])
	}
}
