package runtime

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// 会话级挂载合并进 cfg 的那一跳。
//
// 这是"用户在单个会话里临时挂自己的工具"的落点。盯三件事，全都是**静默**失效的：
//
//	撞名     agent 已绑同名时保留绑定那份 —— 否则用户能在会话里悄悄替换掉共享
//	         agent 的工具行为，而作者一无所知
//	容错     lea-config 挂了/超时只是少几个工具，不能让整段对话打不开
//	去重     同一个工具既被绑又被挂是正常的，重名会让引擎装配直接失败

func withConfigServer(t *testing.T, status int, body string) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
	restore := configServiceURL
	configServiceURL = srv.URL
	t.Cleanup(func() {
		configServiceURL = restore
		srv.Close()
	})
}

func toolNames(cfg *AgentConfig) []string {
	out := make([]string, 0, len(cfg.Tools))
	for _, t := range cfg.Tools {
		out = append(out, t.Name)
	}
	return out
}

func refs(kind string, ids ...int) []AttachmentRef {
	out := make([]AttachmentRef, 0, len(ids))
	for _, id := range ids {
		out = append(out, AttachmentRef{Kind: kind, RefID: id})
	}
	return out
}

// ── 合并 ──────────────────────────────────────────────────────────────────

func TestMergeAttachments_AddsToolWithSchema(t *testing.T) {
	withConfigServer(t, 200, `{
		"tools": [{"name":"my__stock_lookup","description":"查库存",
		           "schema_json":{"type":"object","required":["warehouse_ref"]},
		           "execution_type":"api"}],
		"kb_ids": [], "dropped": []
	}`)

	cfg := &AgentConfig{Tools: []ToolDef{{Name: "get_weather"}}}
	got := MergeAttachments(context.Background(), cfg, "t1", "alice", refs("tool", 45))

	if len(got.Tools) != 2 {
		t.Fatalf("want 2 tools, got %v", toolNames(got))
	}
	// schema 必须活着到这里 —— 丢了的话模型看到一个空对象 schema，参数全靠猜。
	// 这是 schema_json 那个 tag bug 的下游形状。
	last := got.Tools[1]
	if last.InputSchema == nil {
		t.Fatal("挂载工具的 schema 丢了")
	}
	if _, ok := last.InputSchema["required"]; !ok {
		t.Errorf("schema 内容不完整: %v", last.InputSchema)
	}
}

func TestMergeAttachments_AddsKBIDs(t *testing.T) {
	withConfigServer(t, 200, `{"tools":[],"kb_ids":[7,9],"dropped":[]}`)

	cfg := &AgentConfig{KBIDs: []int{3}}
	got := MergeAttachments(context.Background(), cfg, "t1", "alice", refs("kb", 7, 9))

	if len(got.KBIDs) != 3 {
		t.Fatalf("want [3 7 9], got %v", got.KBIDs)
	}
}

func TestMergeAttachments_DeduplicatesKBIDs(t *testing.T) {
	withConfigServer(t, 200, `{"tools":[],"kb_ids":[7,9],"dropped":[]}`)

	cfg := &AgentConfig{KBIDs: []int{3, 7}} // agent 已绑 7，挂载又返回 7
	got := MergeAttachments(context.Background(), cfg, "t1", "alice", refs("kb", 7, 9))

	// 7 不该出现两次 —— lea-rag/kb_search.py 按 kb_ids 查，重复会让同一个库的结果
	// 参与两次相关性排序，排挤掉其他来源的结果。
	if len(got.KBIDs) != 3 {
		t.Fatalf("want 3 kb_ids, got %v", got.KBIDs)
	}
	seen := make(map[int]int)
	for _, id := range got.KBIDs {
		seen[id]++
	}
	for id, count := range seen {
		if count > 1 {
			t.Errorf("kb_id %d 出现 %d 次", id, count)
		}
	}
}

func TestMergeAttachments_KeepsBindingWhenNameCollides(t *testing.T) {
	withConfigServer(t, 200, `{
		"tools": [{"name":"lookup","description":"会话里挂的","execution_type":"api"}],
		"kb_ids": [], "dropped": []
	}`)

	cfg := &AgentConfig{Tools: []ToolDef{
		{Name: "lookup", Description: "agent 绑定的"},
	}}
	got := MergeAttachments(context.Background(), cfg, "t1", "alice", refs("tool", 45))

	// 只有 1 个 lookup —— agent 那份。挂载那个撞名了，被丢掉。
	if len(got.Tools) != 1 {
		t.Fatalf("want 1 tool, got %v", toolNames(got))
	}
	if got.Tools[0].Description != "agent 绑定的" {
		t.Errorf("撞名时留错了：%s", got.Tools[0].Description)
	}
}

func TestMergeAttachments_FallsBackOnConfigError(t *testing.T) {
	withConfigServer(t, 503, `{}`)

	cfg := &AgentConfig{Tools: []ToolDef{{Name: "original"}}}
	got := MergeAttachments(context.Background(), cfg, "t1", "alice", refs("tool", 45))

	// lea-config 挂了 → 返回 cfg 原样，不让整段对话打不开。
	if len(got.Tools) != 1 || got.Tools[0].Name != "original" {
		t.Errorf("want fallback to original cfg, got %v", toolNames(got))
	}
}

func TestMergeAttachments_IgnoresUnknownKind(t *testing.T) {
	withConfigServer(t, 200, `{"tools":[],"kb_ids":[],"dropped":[
		{"kind":"skill","ref_id":12,"reason":"skill 是下一批"}
	]}`)

	cfg := &AgentConfig{}
	// 表上写了 kind='skill'（persistence 层不过滤），lea-config 也不认，放 dropped 里。
	// 这里**不报错** —— MergeAttachments 容错性是"能用的留下，用不了的丢掉"，
	// 而不是"有一个用不了就全失败"。否则「加一种新资源」就等于让老会话打不开。
	got := MergeAttachments(context.Background(), cfg, "t1", "alice",
		[]AttachmentRef{{Kind: "skill", RefID: 12}})

	if len(got.Tools) != 0 || len(got.KBIDs) != 0 {
		t.Errorf("dropped 项不该合并进 cfg")
	}
}

func TestMergeAttachments_EmptyRefsReturnsOriginal(t *testing.T) {
	cfg := &AgentConfig{Tools: []ToolDef{{Name: "a"}}}
	got := MergeAttachments(context.Background(), cfg, "t1", "alice", nil)

	if len(got.Tools) != 1 || got.Tools[0].Name != "a" {
		t.Error("空挂载应该直接返回原 cfg")
	}
}

// ── 调用 lea-config 的真实形状 ──────────────────────────────────────────

func TestMergeAttachments_SendsCorrectPayload(t *testing.T) {
	var captured map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/internal/resolve-attachments" {
			t.Errorf("wrong path: %s", r.URL.Path)
		}
		if r.Method != "POST" {
			t.Errorf("wrong method: %s", r.Method)
		}
		_ = json.NewDecoder(r.Body).Decode(&captured)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(200)
		_, _ = w.Write([]byte(`{"tools":[],"kb_ids":[],"dropped":[]}`))
	}))
	defer srv.Close()
	restore := configServiceURL
	configServiceURL = srv.URL
	defer func() { configServiceURL = restore }()

	MergeAttachments(context.Background(), &AgentConfig{}, "tenant-x", "bob",
		[]AttachmentRef{{Kind: "tool", RefID: 7}, {Kind: "kb", RefID: 9}})

	if captured["tenant_id"] != "tenant-x" || captured["user_id"] != "bob" {
		t.Errorf("身份没传对: %v", captured)
	}
	items, ok := captured["items"].([]any)
	if !ok || len(items) != 2 {
		t.Fatalf("items 形状不对: %v", captured["items"])
	}
	first := items[0].(map[string]any)
	if first["kind"] != "tool" || first["ref_id"].(float64) != 7 {
		t.Errorf("第一项不对: %v", first)
	}
}

