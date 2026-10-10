package persistence

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"
	"time"

	"gitlab.ouc-online.com.cn/aibase/agentloop/llm"
)

// fakeInflight 是 InflightStore 的内存实现，并且记下每次调用的动作。
//
// 记动作是这套测试的重点：追加和重锚写出来的**结果**在多数情形下是一样的，区别只在
// 路径上（写了多少字节、发了几次命令）。所以断言必须落在"走的是哪条路"，不能只看读回来
// 的内容对不对。
type fakeInflight struct {
	lists map[string][][]byte
	// reanchors / appends 分别数两种写入路径被走了几次。
	reanchors int
	appends   int
	// appendedElems 累计追加写出去的元素个数 —— 第二步省下来的就是它的量。
	appendedElems int
	// renews 数了几次"只续 TTL 不写内容"的空追加。
	renews int
	// lens 数问了几次 LLEN。
	lens int
	// failLen 为真时 LLEN 返回错误，用来验证真错误会传出去而不是被吞掉。
	failLen bool
}

func newFakeInflight() *fakeInflight {
	return &fakeInflight{lists: map[string][][]byte{}}
}

func (f *fakeInflight) ReanchorKey(_ context.Context, key string, messages [][]byte, _ time.Duration) error {
	f.reanchors++
	if len(messages) == 0 {
		delete(f.lists, key)
		return nil
	}
	copied := make([][]byte, len(messages))
	copy(copied, messages)
	f.lists[key] = copied
	return nil
}

func (f *fakeInflight) AppendToKey(_ context.Context, key string, messages [][]byte, _ time.Duration) error {
	// 空的 messages 是"历史没变，续一次 TTL"：真实现照样发 EXPIRE，但写出去的字节是 0，
	// 所以计入 appends 会让"追加了几次"和"写了多少"这两件事混在一起。只记非空的那次。
	if len(messages) == 0 {
		f.renews++
		return nil
	}
	f.appends++
	f.appendedElems += len(messages)
	f.lists[key] = append(f.lists[key], messages...)
	return nil
}

func (f *fakeInflight) Len(_ context.Context, key string) (int, error) {
	f.lens++
	if f.failLen {
		return 0, fmt.Errorf("redis down")
	}
	return len(f.lists[key]), nil
}

func (f *fakeInflight) Range(_ context.Context, key string) ([][]byte, error) {
	values, ok := f.lists[key]
	if !ok || len(values) == 0 {
		return nil, errInflightNotFound
	}
	return values, nil
}

func (f *fakeInflight) Del(_ context.Context, key string) error {
	delete(f.lists, key)
	return nil
}

// resetAnchors 清掉进程内的 anchor 缓存 —— 每个用例都从"本进程没写过这把 key"开始，
// 否则用例之间会互相看到上一条的 anchor。
func resetAnchors() {
	inflightAnchorsMu.Lock()
	inflightAnchors = map[string]*inflightAnchor{}
	inflightAnchorsMu.Unlock()
}

func userMsg(text string) llm.Message {
	return llm.Message{Role: llm.RoleUser, Content: []llm.ContentBlock{{Type: llm.ContentBlockTypeText, Text: text}}}
}

func assistantMsg(text string) llm.Message {
	return llm.Message{Role: llm.RoleAssistant, Content: []llm.ContentBlock{{Type: llm.ContentBlockTypeText, Text: text}}}
}

func writeSnapshot(t *testing.T, store InflightStore, messages []llm.Message) {
	t.Helper()
	if err := WriteInflightSnapshot(context.Background(), store, "t1", "s1", "run1", 1, messages); err != nil {
		t.Fatalf("write snapshot: %v", err)
	}
}

func TestInflightAppendsOnlyTheNewTail(t *testing.T) {
	resetAnchors()
	store := newFakeInflight()

	history := []llm.Message{userMsg("你好"), assistantMsg("在的")}
	writeSnapshot(t, store, history)

	// 第二次、第三次各只多一条 —— 这就是逐步落地的样子。
	history = append(history, userMsg("继续"))
	writeSnapshot(t, store, history)
	history = append(history, assistantMsg("好的"))
	writeSnapshot(t, store, history)

	if store.reanchors != 1 {
		t.Errorf("reanchors = %d, want 1（只有第一次需要整条写）", store.reanchors)
	}
	if store.appends != 2 {
		t.Errorf("appends = %d, want 2", store.appends)
	}
	// 三次写出去的元素总数应当是 2+1+1，而不是 2+3+4。
	if store.appendedElems != 2 {
		t.Errorf("appended elements = %d, want 2（重锚不计入追加）", store.appendedElems)
	}

	got := LoadInflightSnapshot(context.Background(), store, "t1", "s1", "run1", 1)
	if len(got) != 4 {
		t.Fatalf("载入 %d 条，want 4", len(got))
	}
	if text := llm.TextContent(got[3]); text != "好的" {
		t.Errorf("最后一条 = %q, want 好的", text)
	}
}

func TestInflightReanchorsWhenHistoryIsRewritten(t *testing.T) {
	resetAnchors()
	store := newFakeInflight()

	history := []llm.Message{userMsg("第一个问题"), assistantMsg("第一个回答")}
	writeSnapshot(t, store, history)
	history = append(history, userMsg("第二个问题"))
	writeSnapshot(t, store, history)

	// 压缩：前面两条被换成一条摘要。长度和内容都变了，而 anchor 记的是原来的下标 1 ——
	// 那里现在是一条 assistant，内容对不上，必须整条重锚而不是往后接。
	compacted := []llm.Message{
		{Role: llm.RoleUser, Content: []llm.ContentBlock{{Type: llm.ContentBlockTypeText, Text: "（摘要）"}}},
		userMsg("第二个问题"),
		assistantMsg("第二个回答"),
	}
	writeSnapshot(t, store, compacted)

	if store.reanchors != 2 {
		t.Errorf("reanchors = %d, want 2（压缩之后必须重锚）", store.reanchors)
	}

	got := LoadInflightSnapshot(context.Background(), store, "t1", "s1", "run1", 1)
	if len(got) != 3 {
		t.Fatalf("载入 %d 条，want 3 —— 追加进旧尾巴的话会多出被压掉的那几条", len(got))
	}
	if text := llm.TextContent(got[0]); text != "（摘要）" {
		t.Errorf("首条 = %q, want （摘要）—— 读回来的是压缩后的历史", text)
	}
}

// 长度相同但内容被换掉的写法也要能发现：靠条数判断会漏掉这种。
func TestInflightReanchorsWhenTailContentDiffers(t *testing.T) {
	resetAnchors()
	store := newFakeInflight()

	writeSnapshot(t, store, []llm.Message{userMsg("问题"), assistantMsg("答案 A")})
	writeSnapshot(t, store, []llm.Message{userMsg("问题"), assistantMsg("答案 B"), userMsg("追问")})

	if store.reanchors != 2 {
		t.Errorf("reanchors = %d, want 2（下标 1 的内容变了）", store.reanchors)
	}
	got := LoadInflightSnapshot(context.Background(), store, "t1", "s1", "run1", 1)
	if text := llm.TextContent(got[1]); text != "答案 B" {
		t.Errorf("第二条 = %q, want 答案 B", text)
	}
}

// Redis 上那把 key 被 TTL 收掉之后，本进程的 anchor 还在。这时追加会把新消息接到一把
// 空 list 上，读出来是一段没有开头的历史 —— 所以 LLEN 必须每次都问。
func TestInflightReanchorsWhenRedisKeyVanished(t *testing.T) {
	resetAnchors()
	store := newFakeInflight()

	history := []llm.Message{userMsg("问题"), assistantMsg("答案")}
	writeSnapshot(t, store, history)
	delete(store.lists, inflightKey("t1", "s1", "run1", 1))

	history = append(history, userMsg("追问"))
	writeSnapshot(t, store, history)

	if store.reanchors != 2 {
		t.Errorf("reanchors = %d, want 2（key 没了就不能追加）", store.reanchors)
	}
	got := LoadInflightSnapshot(context.Background(), store, "t1", "s1", "run1", 1)
	if len(got) != 3 {
		t.Errorf("载入 %d 条，want 3 —— 追加到空 key 上会丢掉前两条", len(got))
	}
}

// 换成新进程意味着 anchor 丢了，第一次写必须是整条 —— 这也是 attempt 递给新 worker 时
// 的行为：它写的是 attempt+1 那把新 key，从头开始。
func TestInflightFirstWriteInFreshProcessIsFull(t *testing.T) {
	resetAnchors()
	store := newFakeInflight()

	history := []llm.Message{userMsg("问题"), assistantMsg("答案"), userMsg("追问")}
	writeSnapshot(t, store, history)

	if store.reanchors != 1 || store.appends != 0 {
		t.Errorf("reanchors=%d appends=%d, want 1/0", store.reanchors, store.appends)
	}
	got := LoadInflightSnapshot(context.Background(), store, "t1", "s1", "run1", 1)
	if len(got) != 3 {
		t.Errorf("载入 %d 条，want 3", len(got))
	}
}

// 历史没变化时不该重写，也不该报错。
func TestInflightUnchangedHistoryIsNoop(t *testing.T) {
	resetAnchors()
	store := newFakeInflight()

	history := []llm.Message{userMsg("问题"), assistantMsg("答案")}
	writeSnapshot(t, store, history)
	writeSnapshot(t, store, history)

	if store.reanchors != 1 {
		t.Errorf("reanchors = %d, want 1", store.reanchors)
	}
	if store.appends != 0 {
		t.Errorf("appends = %d, want 0", store.appends)
	}
	if store.renews != 1 {
		t.Errorf("renews = %d, want 1（没变化就只续 TTL）", store.renews)
	}
}

// LLEN 的真错误要传出去（调用方记日志），不能当成"对不上"静默重锚 ——
// 那种情况下重锚会真的写一遍，而 Redis 显然正不可用。
func TestInflightPropagatesLenError(t *testing.T) {
	resetAnchors()
	store := newFakeInflight()

	history := []llm.Message{userMsg("问题"), assistantMsg("答案")}
	writeSnapshot(t, store, history)
	store.failLen = true

	history = append(history, userMsg("追问"))
	err := WriteInflightSnapshot(context.Background(), store, "t1", "s1", "run1", 1, history)
	if err == nil {
		t.Fatal("LLEN 失败时应当返回错误")
	}
}

// ClearInflight 之后 anchor 也要掉，否则同一把 key 被复用时会拿旧 anchor 去追。
func TestInflightClearDropsAnchor(t *testing.T) {
	resetAnchors()
	store := newFakeInflight()

	history := []llm.Message{userMsg("问题"), assistantMsg("答案")}
	writeSnapshot(t, store, history)
	if err := ClearInflightSnapshot(context.Background(), store, "t1", "s1", "run1", 1); err != nil {
		t.Fatalf("clear: %v", err)
	}
	if got := LoadInflightSnapshot(context.Background(), store, "t1", "s1", "run1", 1); got != nil {
		t.Errorf("清掉之后还能读到 %d 条", len(got))
	}

	writeSnapshot(t, store, history)
	if store.reanchors != 2 {
		t.Errorf("reanchors = %d, want 2（anchor 应当跟着清掉）", store.reanchors)
	}
}

// 尾部是没配对的 tool_use 时，读回来的历史要被裁到最后一个完整边界。
//
// 两个函数分开测：读回快照走 fake store（LoadInflightHistory 走的是进程内的全局 store，
// 在这里够不着），裁剪是纯函数，直接喂进去比绕一圈更清楚。
func TestInflightTrimCutsUnpairedToolUse(t *testing.T) {
	resetAnchors()
	store := newFakeInflight()

	history := []llm.Message{
		userMsg("改一下配置"),
		assistantMsg("好"),
		{Role: llm.RoleTool, Content: []llm.ContentBlock{{Type: llm.ContentBlockTypeToolResult, ToolUseID: "t1"}}},
		{Role: llm.RoleAssistant, Content: []llm.ContentBlock{{Type: llm.ContentBlockTypeToolUse, ID: "t2", Name: "Write"}}},
	}
	writeSnapshot(t, store, history)

	snapshot := LoadInflightSnapshot(context.Background(), store, "t1", "s1", "run1", 1)
	if len(snapshot) != 4 {
		t.Fatalf("快照读回 %d 条，want 4（写的什么读的什么，裁剪不在这里做）", len(snapshot))
	}

	// 走到恢复路径上真正会被用的那个出口。
	trimmed := trimToLastCompletePair(snapshot)
	if len(trimmed) != 3 {
		t.Fatalf("裁剪后 %d 条，want 3（最后那条没配对的 tool_use 要被切掉）", len(trimmed))
	}
	if trimmed[len(trimmed)-1].Role != llm.RoleTool {
		t.Errorf("末条角色 = %s, want tool", trimmed[len(trimmed)-1].Role)
	}
}

// 整条历史以 assistant 结尾（本步刚决策完、工具还没跑）时，裁到最后一条非 assistant 消息。
func TestInflightTrimCutsLoneAssistantTail(t *testing.T) {
	history := []llm.Message{
		userMsg("看下日志"),
		assistantMsg("好"),
		assistantMsg("我准备读文件"),
	}
	// 两条 assistant 都往前让，边界落在那条 user 上 —— 恢复时模型看到的是用户的原话，
	// 它上一批的决策整批丢掉，由它自己重新做。
	trimmed := trimToLastCompletePair(history)
	if len(trimmed) != 1 {
		t.Fatalf("裁剪后 %d 条，want 1（只留到那条 user）", len(trimmed))
	}
	if trimmed[len(trimmed)-1].Role != llm.RoleUser {
		t.Errorf("末条角色 = %s, want user", trimmed[len(trimmed)-1].Role)
	}
}

// 全是 assistant 时找不到边界，原样返回 —— 不是"裁到空"。
//
// 这个退化分支在真实数据里到不了：checkpoint 先落用户那条 prompt，所以历史里总有一条
// user 打底。传空的历史进去，也只能原样返回，否则就无从判断该留什么。
func TestInflightTrimWithoutBoundaryReturnsInput(t *testing.T) {
	if trimmed := trimToLastCompletePair([]llm.Message{assistantMsg("一"), assistantMsg("二")}); len(trimmed) != 2 {
		t.Errorf("无边界时应当原样返回 2 条，实际 %d 条", len(trimmed))
	}
	if trimmed := trimToLastCompletePair(nil); len(trimmed) != 0 {
		t.Errorf("空输入应当返回空，实际 %d 条", len(trimmed))
	}
}

// 逐条 RPUSH 的形状要求每条消息单独可解码 —— 一个坏元素就当整把 key 坏掉，宁可回落。
func TestInflightCorruptElementLoadsNothing(t *testing.T) {
	resetAnchors()
	store := newFakeInflight()

	key := inflightKey("t1", "s1", "run1", 1)
	good, err := json.Marshal(userMsg("问题"))
	if err != nil {
		t.Fatal(err)
	}
	store.lists[key] = [][]byte{good, []byte("{不是 JSON")}

	if got := LoadInflightSnapshot(context.Background(), store, "t1", "s1", "run1", 1); got != nil {
		t.Errorf("坏元素应当整把作废，却读回 %d 条", len(got))
	}
}

// 超过上限的历史不写出去，且报错要让调用方看见（引擎会记日志）。
func TestInflightRejectsOversizedSnapshot(t *testing.T) {
	resetAnchors()
	store := newFakeInflight()

	huge := make([]llm.Message, 0, 8)
	for i := 0; i < 8; i++ {
		huge = append(huge, userMsg(fmt.Sprintf("%d", i)+string(make([]byte, maxInflightBytes/4))))
	}
	err := WriteInflightSnapshot(context.Background(), store, "t1", "s1", "run1", 1, huge)
	if err == nil {
		t.Fatal("超限时应当返回错误")
	}
	if store.reanchors != 0 || store.appends != 0 {
		t.Errorf("超限时不该写出去：reanchors=%d appends=%d", store.reanchors, store.appends)
	}
}
