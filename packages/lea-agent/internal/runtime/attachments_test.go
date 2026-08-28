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
	withConfigServer(t, 200, `{"tools":[],"skills":[],"kb_ids":[],"dropped":[
		{"kind":"mcp_server","ref_id":12,"reason":"mcp_server 是下一批"}
	]}`)

	cfg := &AgentConfig{}
	// 表上写了 kind='mcp_server'（persistence 层不过滤），这一版的 lea-config 也不认，
	// 放 dropped 里。这里**不报错** —— MergeAttachments 的容错性是"能用的留下，用不了
	// 的丢掉"，而不是"有一个用不了就全失败"。否则「加一种新资源」就等于让老会话打不开。
	got := MergeAttachments(context.Background(), cfg, "t1", "alice",
		[]AttachmentRef{{Kind: "mcp_server", RefID: 12}})

	if len(got.Tools) != 0 || len(got.KBIDs) != 0 || len(got.Skills) != 0 {
		t.Errorf("dropped 项不该合并进 cfg")
	}
}

// ── skill ─────────────────────────────────────────────────────────────────
//
// skill 撞名比工具更隐蔽：它是一段**注入进 prompt 的正文**，不是一个可调用的名字。
// 让挂载覆盖绑定的话，共享 agent 的行为会被悄悄改写 —— 模型不会说它换了参考资料，
// 输出看起来只是"表现不一样"。

func TestMergeAttachments_AddsSkill(t *testing.T) {
	withConfigServer(t, 200, `{
		"tools": [], "kb_ids": [], "dropped": [],
		"skills": [{"id":8,"name":"ppt_maker","display_name":"做 PPT",
		            "description":"按模板生成","system_prompt":"Step 1: 调研…",
		            "bundle_uri":"s3://b/ppt.zip"}]
	}`)

	cfg := &AgentConfig{}
	got := MergeAttachments(context.Background(), cfg, "t1", "alice", refs("skill", 8))

	if len(got.Skills) != 1 {
		t.Fatalf("want 1 skill, got %d", len(got.Skills))
	}
	s := got.Skills[0]
	// 这几个字段有一个解不出来，模型就拿到一个没正文/没目录的 skill —— 静默失效。
	if s.Name != "ppt_maker" {
		t.Errorf("name = %q", s.Name)
	}
	if s.SystemPrompt != "Step 1: 调研…" {
		t.Errorf("system_prompt 没解出来: %q", s.SystemPrompt)
	}
	if s.BundleURI != "s3://b/ppt.zip" {
		t.Errorf("bundle_uri 没解出来: %q —— ProvisionSessionSkills 会因此跳过投递", s.BundleURI)
	}
}

func TestMergeAttachments_KeepsBoundSkillWhenNameCollides(t *testing.T) {
	withConfigServer(t, 200, `{
		"tools": [], "kb_ids": [], "dropped": [],
		"skills": [{"name":"ppt_maker","system_prompt":"用户自己那份"}]
	}`)

	cfg := &AgentConfig{Skills: []SkillDef{
		{Name: "ppt_maker", SystemPrompt: "agent 绑定的那份"},
	}}
	got := MergeAttachments(context.Background(), cfg, "t1", "alice", refs("skill", 8))

	if len(got.Skills) != 1 {
		t.Fatalf("want 1 skill, got %d", len(got.Skills))
	}
	if got.Skills[0].SystemPrompt != "agent 绑定的那份" {
		t.Errorf("撞名时留错了：%q —— 共享 agent 的流程被用户那份悄悄替换了",
			got.Skills[0].SystemPrompt)
	}
}

func TestMergeAttachments_MergesAllThreeKinds(t *testing.T) {
	// 一次挂全三样。分别解析的话很容易漏掉某一种的合并（skills 那段就是后加的）。
	withConfigServer(t, 200, `{
		"tools": [{"name":"my__lookup","description":"查库存"}],
		"skills": [{"name":"ppt_maker","system_prompt":"Step 1"}],
		"kb_ids": [3, 7],
		"dropped": []
	}`)

	cfg := &AgentConfig{}
	got := MergeAttachments(context.Background(), cfg, "t1", "alice",
		[]AttachmentRef{{Kind: "tool", RefID: 1}, {Kind: "skill", RefID: 2}, {Kind: "kb", RefID: 3}})

	if len(got.Tools) != 1 || len(got.Skills) != 1 || len(got.KBIDs) != 2 {
		t.Errorf("三样没都合并进来: tools=%d skills=%d kbs=%d",
			len(got.Tools), len(got.Skills), len(got.KBIDs))
	}
}

func TestMergeAttachments_DeduplicatesSkillsByName(t *testing.T) {
	// lea-config 理论上不会返回重复，但同名的两个 skill（不同 id、不同租户层级）
	// 是可能的。引擎的 skill.Catalog 会列两条同名项，而 Skill 工具按名字解析只会命中
	// 一个 —— 模型看到的目录跟能拿到的正文对不上。
	withConfigServer(t, 200, `{
		"tools": [], "kb_ids": [], "dropped": [],
		"skills": [
			{"id":1,"name":"ppt_maker","system_prompt":"平台那份"},
			{"id":2,"name":"ppt_maker","system_prompt":"我自己那份"}
		]
	}`)

	cfg := &AgentConfig{}
	got := MergeAttachments(context.Background(), cfg, "t1", "alice", refs("skill", 1, 2))

	if len(got.Skills) != 1 {
		t.Fatalf("同名 skill 该去重，got %d", len(got.Skills))
	}
	if got.Skills[0].SystemPrompt != "平台那份" {
		t.Errorf("去重该保留先到的那份，got %q", got.Skills[0].SystemPrompt)
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

