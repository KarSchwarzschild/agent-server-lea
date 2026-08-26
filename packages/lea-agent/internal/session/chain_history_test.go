package session

import (
	"encoding/json"
	"testing"

	engine "gitlab.ouc-online.com.cn/aibase/agentloop"
	"gitlab.ouc-online.com.cn/aibase/agentloop/llm"
)

func chainMsg(role llm.Role, text string) llm.Message {
	return llm.Message{Role: role, Content: []llm.ContentBlock{{Type: llm.ContentBlockTypeText, Text: text}}}
}

func chainTexts(msgs []llm.Message) []string {
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

// 只落这条链新产生的那段。前面那截是启动时从 agent_messages 读出来的，已经在表里 ——
// 再写一遍的话下一轮加载会读到两份，模型看见自己把同一段话说了两遍。
func TestFreshChainMessages_SkipsWhatWasAlreadyStored(t *testing.T) {
	chain := &engine.ChainResult{
		History: []llm.Message{
			chainMsg(llm.RoleUser, "上一轮的问"),
			chainMsg(llm.RoleAssistant, "上一轮的答"),
			chainMsg(llm.RoleUser, "这一轮的问"),
			chainMsg(llm.RoleAssistant, "这一轮的答"),
		},
		PriorLen: 2,
	}
	got, ok := freshChainMessages(chain)
	if !ok {
		t.Fatal("正常的 PriorLen 不该被判为不可信")
	}
	if len(got) != 2 || chainTexts(got)[0] != "这一轮的问" {
		t.Fatalf("只该落新那段，实际 %v", chainTexts(got))
	}
}

// 全新会话：PriorLen=0，整段都要落。这是 handoff 团第一次被问到时走的路。
func TestFreshChainMessages_NewSessionKeepsEverything(t *testing.T) {
	chain := &engine.ChainResult{
		History:  []llm.Message{chainMsg(llm.RoleUser, "问"), chainMsg(llm.RoleAssistant, "答")},
		PriorLen: 0,
	}
	got, ok := freshChainMessages(chain)
	if !ok || len(got) != 2 {
		t.Fatalf("新会话该整段落库，实际 %v ok=%v", chainTexts(got), ok)
	}
}

// PriorLen 越界要报不可信，而不是切下去 panic。这段跑在落库的收尾 goroutine 里，
// panic 掉的是整个 turn，而这一轮的回答其实已经给用户了。
//
// 越界能怎么发生：某一棒触发了压缩，committed 的历史比它 resume 进来的还短。引擎那边
// 夹过一次，这里是第二道 —— 两处都对时它什么也不做，正是想要的。
func TestFreshChainMessages_OutOfRangePriorIsRefusedNotPanicked(t *testing.T) {
	chain := &engine.ChainResult{
		History:  []llm.Message{chainMsg(llm.RoleUser, "问")},
		PriorLen: 5, // 压缩过后 History 缩短了
	}
	if _, ok := freshChainMessages(chain); ok {
		t.Fatal("PriorLen 超出 History 长度时必须判为不可信")
	}
	if _, ok := freshChainMessages(&engine.ChainResult{PriorLen: -1}); ok {
		t.Fatal("负数 PriorLen 必须判为不可信")
	}
}

// 链没跑起来（chainCfg 建了但 RunChain 直接返错）时 lastChain 是 nil，
// 落库路径要安静跳过而不是崩。
func TestFreshChainMessages_NilIsQuietlyEmpty(t *testing.T) {
	got, ok := freshChainMessages(nil)
	if !ok || len(got) != 0 {
		t.Fatalf("nil 该返回空且可信，实际 %v ok=%v", got, ok)
	}
}

// ── AskUser 提取 ─────────────────────────────────────────────

func askUserBlock(question string, options []string) llm.ContentBlock {
	in, _ := json.Marshal(map[string]any{"question": question, "options": options})
	return llm.ContentBlock{
		Type:  llm.ContentBlockTypeToolUse,
		ID:    "toolu_ask",
		Name:  "AskUser",
		Input: in,
	}
}

func TestFindAskUserToolUse_FindsLastInHistory(t *testing.T) {
	history := []llm.Message{
		{Role: llm.RoleUser, Content: []llm.ContentBlock{{Type: llm.ContentBlockTypeText, Text: "问"}}},
		{Role: llm.RoleAssistant, Content: []llm.ContentBlock{askUserBlock("第一个问题", nil)}},
		{Role: llm.RoleUser, Content: []llm.ContentBlock{{Type: llm.ContentBlockTypeToolResult}}},
		{Role: llm.RoleAssistant, Content: []llm.ContentBlock{
			{Type: llm.ContentBlockTypeText, Text: "好的"},
			askUserBlock("最终问题", []string{"A", "B"}),
		}},
	}
	block, ok := findAskUserToolUse(history)
	if !ok {
		t.Fatal("历史里有 AskUser tool_use，必须找到")
	}
	if block.Name != "AskUser" {
		t.Fatalf("找到的不是 AskUser: %s", block.Name)
	}
	// 必须找到最后一个而不是第一个
	q, opts := extractAskUserPayload(llm.Message{Content: []llm.ContentBlock{block}})
	if q != "最终问题" {
		t.Errorf("question = %q, want %q", q, "最终问题")
	}
	if len(opts) != 2 || opts[0] != "A" {
		t.Errorf("options = %v, want [A B]", opts)
	}
}

func TestFindAskUserToolUse_EmptyHistory(t *testing.T) {
	_, ok := findAskUserToolUse(nil)
	if ok {
		t.Fatal("空历史不该找到 AskUser")
	}
}

func TestExtractAskUserPayload_NoToolUse(t *testing.T) {
	msg := llm.Message{Content: []llm.ContentBlock{{Type: llm.ContentBlockTypeText, Text: "hello"}}}
	q, opts := extractAskUserPayload(msg)
	if q != "" || opts != nil {
		t.Fatalf("纯文本消息不该抽出追问: q=%q opts=%v", q, opts)
	}
}
