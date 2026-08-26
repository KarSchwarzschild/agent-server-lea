package persistence

import (
	"strings"
	"testing"

	"gitlab.ouc-online.com.cn/aibase/agentloop/llm"
)

// msg 造一条最简消息；role 决定孤儿头怎么处理，文本决定它值多少 token。
func msg(role llm.Role, text string) llm.Message {
	return llm.Message{
		Role:    role,
		Content: []llm.ContentBlock{{Type: llm.ContentBlockTypeText, Text: text}},
	}
}

// cost 按 recentWithinBudget 用的同一把尺算这些消息值多少 token。
//
// 测试里的预算一律用它表达，不写字面量 —— 写死数字的话，估算法（CJK 1 token/字，
// 其余 4 字符 1 token）哪天调了，这些测试会变成"红得莫名其妙"，而不是指出真问题。
func cost(msgs ...llm.Message) int {
	n := 0
	for _, m := range msgs {
		for _, b := range m.Content {
			n += llm.EstimateBlockTokens(b)
		}
	}
	return n
}

func texts(msgs []llm.Message) []string {
	out := make([]string, 0, len(msgs))
	for _, m := range msgs {
		for _, b := range m.Content {
			if b.Type == llm.ContentBlockTypeText {
				out = append(out, b.Text)
			}
		}
	}
	return out
}

func eq(t *testing.T, got []string, want ...string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("消息序列 = %v, 期望 %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("消息序列 = %v, 期望 %v", got, want)
		}
	}
}

const bigBudget = 1_000_000

// 行是 newest-first 进来的，出去必须是 oldest-first —— 顺序反了模型会读到倒放的对话。
func TestRecentWithinBudget_ReversesToChronological(t *testing.T) {
	got := recentWithinBudget([]llm.Message{
		msg(llm.RoleAssistant, "答2"),
		msg(llm.RoleUser, "问2"),
		msg(llm.RoleAssistant, "答1"),
		msg(llm.RoleUser, "问1"),
	}, bigBudget)
	eq(t, texts(got), "问1", "答1", "问2", "答2")
}

// ── 这条是整次改动要修的 bug ──────────────────────────────────────────
//
// 又长又轻的会话必须**全部**载入。
//
// 换掉的那版判据是"最近 20 轮"，于是一个 50 轮闲聊、总共才 1.5 万 token 的会话，
// 每一轮都只看得见最近 20 轮 —— 模型窗口 12.8 万装得下，但它看不见前 30 轮。
// 表现是"模型忘了前面说过的话"，而且跟跨不跨 pod 无关：LoadHistory 的结果会被
// ReplaceHistory 整体替换成工作历史，所以每轮都砍一次。
//
// 预算判据下这件事不会发生：没压缩过的会话按定义在压缩触发线以下，预算高于触发线，
// 所以装得下。
func TestRecentWithinBudget_LongButLightHistoryLoadsWhole(t *testing.T) {
	var newestFirst []llm.Message
	for i := 50; i >= 1; i-- {
		newestFirst = append(newestFirst,
			msg(llm.RoleAssistant, "答"),
			msg(llm.RoleUser, "问"),
		)
	}

	got := recentWithinBudget(newestFirst, bigBudget)

	if len(got) != 100 {
		t.Fatalf("50 轮短对话只载回 %d 条，期望 100 条全部载入", len(got))
	}
	if texts(got)[0] != "问" || len(texts(got)) != 100 {
		t.Fatalf("载回的形状不对: %d 条", len(texts(got)))
	}
}

// 预算不够时留**最近**那几轮。留成开头几轮的话，用户刚说的话恰好被丢掉。
func TestRecentWithinBudget_CapKeepsTheRecentEnd(t *testing.T) {
	turn1 := []llm.Message{msg(llm.RoleUser, "问1"), msg(llm.RoleAssistant, "答1")}
	turn2 := []llm.Message{msg(llm.RoleUser, "问2"), msg(llm.RoleAssistant, "答2")}
	turn3 := []llm.Message{msg(llm.RoleUser, "问3"), msg(llm.RoleAssistant, "答3")}

	// 预算刚好装下后两轮，第一轮会把它撑破。
	budget := cost(append(append([]llm.Message{}, turn2...), turn3...)...)

	got := recentWithinBudget([]llm.Message{
		turn3[1], turn3[0], turn2[1], turn2[0], turn1[1], turn1[0],
	}, budget)
	eq(t, texts(got), "问2", "答2", "问3", "答3")
}

// ── 一个**有意的**语义变化，钉在这里 ──────────────────────────────────
//
// 工具结果现在**消耗预算**了。轮数判据下它不消耗（一轮 = 一条 user 消息 + 后面所有
// 东西，工具往返不算新轮），所以一次读进来 8 万 token 的 Read 结果，在"20 轮"这个
// 判据里跟一句"嗯"一样贵 —— 那正是轮数判据最主要的毛病。
//
// 换句话说：这不是回归，这是换判据的**目的**。
func TestRecentWithinBudget_ToolResultsConsumeBudget(t *testing.T) {
	small := msg(llm.RoleTool, "小结果")
	large := msg(llm.RoleTool, strings.Repeat("大", 5_000))

	build := func(toolResult llm.Message) []llm.Message {
		return []llm.Message{
			msg(llm.RoleAssistant, "答2"), msg(llm.RoleUser, "问2"),
			msg(llm.RoleAssistant, "答1"), toolResult,
			msg(llm.RoleAssistant, "调工具"), msg(llm.RoleUser, "问1"),
		}
	}

	budget := cost(build(small)...) // 刚好装下"小结果"那一版的全部

	withSmall := recentWithinBudget(build(small), budget)
	withLarge := recentWithinBudget(build(large), budget)

	if len(withSmall) != 6 {
		t.Fatalf("小结果那版应当全部载入，实际 %d 条", len(withSmall))
	}
	if len(withLarge) >= len(withSmall) {
		t.Errorf("大工具结果没有吃掉预算：large=%d 条 vs small=%d 条 —— "+
			"说明预算没把工具结果算进去", len(withLarge), len(withSmall))
	}
}

// 截断落在一轮中间时，剩下的开头是个没有提问的 assistant —— 必须丢掉。
// 留着的话 provider 可能直接拒，模型也会把它当成自己没人问就说的话。
//
// 换判据之后这条更要紧：轮数判据是在 user 消息边界上停的，切点天然对齐；
// 预算判据可以停在任何一条消息上，所以半个轮次从例外变成了常态。
func TestRecentWithinBudget_DropsOrphanHead(t *testing.T) {
	got := recentWithinBudget([]llm.Message{
		msg(llm.RoleAssistant, "答2"),
		msg(llm.RoleUser, "问2"),
		msg(llm.RoleAssistant, "答1的后半"), // 它的提问在粗保护之外
	}, bigBudget)
	eq(t, texts(got), "问2", "答2")
}

// 预算刚好卡在一轮中间时，同样要丢掉那半轮的头。
func TestRecentWithinBudget_CutInsideTurnDropsThePartialHead(t *testing.T) {
	all := []llm.Message{
		msg(llm.RoleAssistant, "答2"),
		msg(llm.RoleUser, "问2"),
		msg(llm.RoleAssistant, "答1"),
		msg(llm.RoleUser, "问1"),
	}
	// 装得下最后一轮 + "答1"，装不下"问1" —— 于是"答1"成了孤儿头。
	budget := cost(all[0], all[1], all[2])

	got := recentWithinBudget(all, budget)
	eq(t, texts(got), "问2", "答2")
}

// 全是孤儿（整段都没有 user）时返回 nil，而不是一段模型会当成自言自语的历史。
func TestRecentWithinBudget_AllOrphansIsNil(t *testing.T) {
	if got := recentWithinBudget([]llm.Message{
		msg(llm.RoleAssistant, "答"),
		msg(llm.RoleAssistant, "答"),
	}, bigBudget); got != nil {
		t.Fatalf("全孤儿应返回 nil，实际 %v", texts(got))
	}
}

// 空会话返回 nil —— 新会话走的就是这条，不能变成一段空消息喂给模型。
func TestRecentWithinBudget_EmptyIsNil(t *testing.T) {
	if got := recentWithinBudget(nil, bigBudget); got != nil {
		t.Fatalf("空输入应返回 nil，实际 %v", got)
	}
}

// 预算比单条消息还小时，仍然要留下那一条。
//
// 返回空历史更糟：模型会面对一个没有任何上下文的追问，然后一本正经地answer错。
// 超一点预算是可恢复的（引擎的 contextControl 会在发出去之前再压一道），
// 丢光上下文不可恢复。
func TestRecentWithinBudget_KeepsAtLeastOneMessage(t *testing.T) {
	got := recentWithinBudget([]llm.Message{
		msg(llm.RoleUser, strings.Repeat("很长的一句话", 100)),
	}, 1)

	if len(got) != 1 {
		t.Fatalf("预算不足时应至少留一条，实际 %d 条", len(got))
	}
}

// 非正数预算走兜底，不是"无上限"也不是"什么都不留"。
//
// 0 表示"调用方没配"，而这个包里 0 曾经就是引擎那边"压缩整个关掉"的意思 ——
// 同一个零值在两层里含义相反，所以这里显式钉住。
func TestRecentWithinBudget_NonPositiveBudgetUsesFallback(t *testing.T) {
	var newestFirst []llm.Message
	for i := 0; i < 200; i++ {
		newestFirst = append(newestFirst,
			msg(llm.RoleAssistant, strings.Repeat("答", 500)),
			msg(llm.RoleUser, strings.Repeat("问", 500)),
		)
	}

	for _, budget := range []int{0, -1} {
		got := recentWithinBudget(newestFirst, budget)
		if got == nil {
			t.Fatalf("预算 %d：不该什么都不留", budget)
		}
		if c := cost(got...); c > fallbackLoadBudget*2 {
			t.Errorf("预算 %d：载回 %d token，远超兜底 %d —— 没走兜底？",
				budget, c, fallbackLoadBudget)
		}
	}
}
