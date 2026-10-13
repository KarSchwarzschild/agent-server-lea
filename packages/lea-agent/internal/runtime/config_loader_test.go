package runtime

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"

	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
)

// 这个文件盯的是**下发契约的消费侧**：lea-config 发什么键，这边就得读什么键。
//
// 生产侧有 tests/test_agent_config_delivery.py 钉着。两边都需要，因为这类 bug 的形状
// 是"两端各自都自洽，合起来对不上"——json.Decode 对不认识的键静默丢弃，所以只测一端
// 永远发现不了。
//
// 真实事故：ToolDef.InputSchema 的 tag 曾经是 `input_schema`（老 lea-server 的列名），
// 而 lea-config 发的是 `schema_json`。后果不是报错，是模型看到的每个用户自定义工具
// 都**没有参数**。

// leaConfigPayload 是 lea-config /internal/agent-config 真实响应的形状。
//
// 键名是从真实服务抓下来的，不是照着 Go 结构体写的 —— 照着结构体写会让这个测试
// 变成同义反复：tag 错了它也绿。
const leaConfigPayload = `{
  "agent": {"id": 2, "name": "a", "model": "qwen3-max", "kb_mode": "off", "memory_mode": "off"},
  "kb_ids": [],
  "skills": [],
  "tools": [
    {
      "id": 7,
      "name": "get_weather",
      "display_name": "天气",
      "description": "查天气",
      "execution_type": "api",
      "enabled": true,
      "schema_json": {
        "type": "object",
        "required": ["latitude", "longitude"],
        "properties": {
          "latitude":  {"type": "number", "description": "纬度"},
          "longitude": {"type": "number", "description": "经度"}
        }
      }
    }
  ],
  "team": null,
  "llm": {"llm_base_url": "http://x", "llm_api_key": "k", "model_context_window": 258048}
}`

func serveConfig(t *testing.T, body string) func() {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(body))
	}))
	old := configServiceURL
	configServiceURL = srv.URL
	return func() {
		configServiceURL = old
		srv.Close()
	}
}

func loadOneTool(t *testing.T) ToolDef {
	t.Helper()
	defer serveConfig(t, leaConfigPayload)()

	cfg, err := LoadAgentConfig(context.Background(), 2, "t1")
	if err != nil {
		t.Fatalf("LoadAgentConfig: %v", err)
	}
	if len(cfg.Tools) != 1 {
		t.Fatalf("工具条数 = %d，想要 1", len(cfg.Tools))
	}
	return cfg.Tools[0]
}

func TestToolSchemaSurvivesTheWire(t *testing.T) {
	td := loadOneTool(t)
	if td.InputSchema == nil {
		t.Fatal("InputSchema 是 nil —— tag 跟 lea-config 发的键对不上。" +
			"这不会报错，只会让模型看到一个没有参数的工具。")
	}
	if _, ok := td.InputSchema["properties"]; !ok {
		t.Fatalf("schema 里没有 properties: %#v", td.InputSchema)
	}
}

func TestRequiredParamsReachTheModel(t *testing.T) {
	// 走到模型真正看到的那一层，而不是停在结构体上。中间还隔着
	// jsonSchemaToToolSchema —— 它对 nil 返回一个**合法但空**的对象 schema，
	// 所以只断言"没炸"是不够的。
	td := loadOneTool(t)
	schema := (&DynamicTool{def: td}).InputSchema()

	if len(schema.Required) != 2 {
		t.Fatalf("required = %v，想要 [latitude longitude]", schema.Required)
	}
	for _, name := range []string{"latitude", "longitude"} {
		p, ok := schema.Properties[name]
		if !ok {
			t.Fatalf("参数 %q 没到模型面前，实际有: %v", name, keysOf(schema.Properties))
		}
		if p.Description == "" {
			t.Errorf("参数 %q 的描述丢了 —— 模型靠它判断该传什么", name)
		}
	}
}

// TestEmptySchemaIsIndistinguishableFromABrokenTag 说明为什么上面那两条必须用一个
// **有必填参数**的工具来测。
//
// 键名错了拿到的是空 schema；一个本来就没有参数的工具拿到的也是空 schema。两者长得
// 一模一样 —— 所以拿内置工具那种空 schema 去测，tag 写错了测试照样绿。
func TestEmptySchemaIsIndistinguishableFromABrokenTag(t *testing.T) {
	broken := jsonSchemaToToolSchema(nil)
	genuinelyEmpty := jsonSchemaToToolSchema(map[string]any{"type": "object"})

	if len(broken.Properties) != len(genuinelyEmpty.Properties) {
		t.Skip("两者已经能区分了，这条注释可以删")
	}
	if broken.Type != genuinelyEmpty.Type {
		t.Fatalf("nil → %v，空对象 → %v", broken.Type, genuinelyEmpty.Type)
	}
}

func TestDeadKeyIsIgnored(t *testing.T) {
	// 老 lea-server 的键名。今天没有任何来源会发它，但万一有人"为了兼容"把它加回
	// lea-config，这条会让那次改动立刻显形 —— 两组表达同一件事的键正是 migrations/006
	// 花一轮删掉的病。
	var payload map[string]any
	if err := json.Unmarshal([]byte(leaConfigPayload), &payload); err != nil {
		t.Fatal(err)
	}
	tools := payload["tools"].([]any)
	tool := tools[0].(map[string]any)
	tool["input_schema"] = map[string]any{"type": "object", "properties": map[string]any{"bogus": map[string]any{"type": "string"}}}
	mutated, _ := json.Marshal(payload)

	defer serveConfig(t, string(mutated))()
	cfg, err := LoadAgentConfig(context.Background(), 2, "t1")
	if err != nil {
		t.Fatalf("LoadAgentConfig: %v", err)
	}
	schema := (&DynamicTool{def: cfg.Tools[0]}).InputSchema()
	if _, leaked := schema.Properties["bogus"]; leaked {
		t.Fatal("读到了 input_schema —— 权威词汇只有一套，不该两个键都认")
	}
	if len(schema.Required) != 2 {
		t.Fatalf("schema_json 该照常生效，required = %v", schema.Required)
	}
}

func TestModelOverrideReachesConfigService(t *testing.T) {
	t.Helper()
	queries := make(chan url.Values, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		queries <- r.URL.Query()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(leaConfigPayload))
	}))
	t.Cleanup(srv.Close)
	old := configServiceURL
	configServiceURL = srv.URL
	t.Cleanup(func() { configServiceURL = old })

	if _, err := LoadAgentConfig(context.Background(), 2, "tenant with spaces", "glm-4 plus"); err != nil {
		t.Fatalf("LoadAgentConfig: %v", err)
	}
	query := <-queries
	if got := query.Get("tenant_id"); got != "tenant with spaces" {
		t.Fatalf("tenant_id = %q, want tenant with spaces", got)
	}
	if got := query.Get("model"); got != "glm-4 plus" {
		t.Fatalf("model = %q, want glm-4 plus", got)
	}
}

func keysOf(m map[string]tool.Schema) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
