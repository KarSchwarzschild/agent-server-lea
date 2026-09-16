package router

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/runtime"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
)

// ── POST /api/sessions/create 可以不传 agent_id ────────────────────────

// minimalAgentConfig 是 /internal/agent-config 的最小可用响应。
// 建会话只需要它能解析出一个 agent，工具/技能/团队都不需要。
const minimalAgentConfig = `{
  "agent": {"id": 1, "name": "super-agent", "model": "qwen3-max",
            "kb_mode": "off", "memory_mode": "off"},
  "kb_ids": [], "skills": [], "tools": [], "team": null,
  "llm": {"llm_base_url": "http://x", "llm_api_key": "k",
          "model_context_window": 258048}
}`

// fakeConfigService 架一个假 lea-config，记下每次请求的路径。
//
// 记路径是这组测试的关键判据：不传 agent_id 时打的必须是 `/agent-config/0`
// （0 = 让 lea-config 兜底到 super-agent）。只看 HTTP 201 证明不了这件事 ——
// 参数被吞掉换成别的数字也一样返回 201。
func fakeConfigService(t *testing.T) (*[]string, func()) {
	t.Helper()
	var mu sync.Mutex
	var paths []string

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		paths = append(paths, r.URL.Path+"?"+r.URL.RawQuery)
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(minimalAgentConfig))
	}))
	restore := runtime.SetConfigServiceURLForTest(srv.URL)
	return &paths, func() {
		restore()
		srv.Close()
	}
}

// requestedAgentID 从记下的路径里找 /internal/agent-config/{id} 那一条。
func requestedAgentID(t *testing.T, paths []string) string {
	t.Helper()
	for _, p := range paths {
		if idx := strings.Index(p, "/internal/agent-config/"); idx >= 0 {
			rest := p[idx+len("/internal/agent-config/"):]
			if q := strings.Index(rest, "?"); q >= 0 {
				return rest[:q]
			}
			return rest
		}
	}
	t.Fatalf("没有一条请求打到 /internal/agent-config：%v", paths)
	return ""
}

// 最自然的调用形状 —— 用户进来直接开聊,前端 POST 一个空 body 就得到一个会话。
// 要的是租户的 super-agent（lea-config 在 agentID=0 时兜底），不需要前端记得传 `{}`。
func TestCreateSession_EmptyBodyUsesSuper(t *testing.T) {
	paths, cleanup := fakeConfigService(t)
	defer cleanup()

	mgr := session.NewManager()
	defer mgr.Close()

	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	c.Request = httptest.NewRequest("POST", "/api/sessions/create", nil)
	c.Set("tenant_id", "T1")
	c.Set("user_id", "U1")

	handleCreateSession(mgr)(c)

	if rec.Code != 201 {
		t.Fatalf("status = %d, want 201; body=%s", rec.Code, rec.Body.String())
	}
	var out map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("json 解析失败: %v", err)
	}
	sid, ok := out["session_id"].(string)
	if !ok || sid == "" {
		t.Fatalf("session_id 缺失或不是字符串: %+v", out)
	}
	if mgr.Get(sid) == nil {
		t.Error("session_id 返回了,但 Manager.Get 拿不到")
	}
	// 关键判据：打给 lea-config 的必须是 agent 0，那是"请兜底"的信号。
	if got := requestedAgentID(t, *paths); got != "0" {
		t.Errorf("请求的 agent_id = %q, want \"0\"（0 才触发 super-agent 兜底）", got)
	}
}

// `{}` 空对象也接受，跟空 body 等价。有些 HTTP 客户端对 POST 没 body 的行为不统一，
// 前端习惯写 `{}` 时不该拦。
func TestCreateSession_EmptyObjectUsesSuper(t *testing.T) {
	paths, cleanup := fakeConfigService(t)
	defer cleanup()

	mgr := session.NewManager()
	defer mgr.Close()

	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	c.Request = httptest.NewRequest("POST", "/api/sessions/create",
		bytes.NewBufferString("{}"))
	c.Request.Header.Set("Content-Type", "application/json")
	c.Set("tenant_id", "T1")
	c.Set("user_id", "U1")

	handleCreateSession(mgr)(c)

	if rec.Code != 201 {
		t.Fatalf("status = %d, want 201; body=%s", rec.Code, rec.Body.String())
	}
	if got := requestedAgentID(t, *paths); got != "0" {
		t.Errorf("请求的 agent_id = %q, want \"0\"", got)
	}
}

// 显式传 agent_id 时按传入的走，不被兜底覆盖 —— 选了专家智能体的那条路不能被改坏。
func TestCreateSession_ExplicitAgentIDHonored(t *testing.T) {
	paths, cleanup := fakeConfigService(t)
	defer cleanup()

	mgr := session.NewManager()
	defer mgr.Close()

	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	c.Request = httptest.NewRequest("POST", "/api/sessions/create",
		bytes.NewBufferString(`{"agent_id": 99}`))
	c.Request.Header.Set("Content-Type", "application/json")
	c.Set("tenant_id", "T1")
	c.Set("user_id", "U1")

	handleCreateSession(mgr)(c)

	if rec.Code != 201 {
		t.Fatalf("status = %d, want 201; body=%s", rec.Code, rec.Body.String())
	}
	if got := requestedAgentID(t, *paths); got != "99" {
		t.Errorf("请求的 agent_id = %q, want \"99\"（显式传的值被吞了）", got)
	}
}

// 显式传 0 跟不传等价 —— 前端老代码硬编码 0，那条路不能因为改成指针就断掉。
func TestCreateSession_ExplicitZeroStillFallsBack(t *testing.T) {
	paths, cleanup := fakeConfigService(t)
	defer cleanup()

	mgr := session.NewManager()
	defer mgr.Close()

	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	c.Request = httptest.NewRequest("POST", "/api/sessions/create",
		bytes.NewBufferString(`{"agent_id": 0}`))
	c.Request.Header.Set("Content-Type", "application/json")
	c.Set("tenant_id", "T1")
	c.Set("user_id", "U1")

	handleCreateSession(mgr)(c)

	if rec.Code != 201 {
		t.Fatalf("status = %d, want 201; body=%s", rec.Code, rec.Body.String())
	}
	if got := requestedAgentID(t, *paths); got != "0" {
		t.Errorf("请求的 agent_id = %q, want \"0\"", got)
	}
}

// 坏格式(不是 JSON)仍然 400,不该静默当成缺省。
//
// 这条防的是一个具体的退化：把 EOF 放过之后，如果连解析错误一起放过，那么一个拼错的
// 字段名（`{"agentid": 99}` 之类）会静默变成"悄悄用了 super-agent"—— 用户选的专家
// 没生效，而任何地方都不报错。
func TestCreateSession_MalformedJSON400(t *testing.T) {
	_, cleanup := fakeConfigService(t)
	defer cleanup()

	mgr := session.NewManager()
	defer mgr.Close()

	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	c.Request = httptest.NewRequest("POST", "/api/sessions/create",
		bytes.NewBufferString(`{agent_id:99`)) // 漏了引号和右括号
	c.Request.Header.Set("Content-Type", "application/json")
	c.Set("tenant_id", "T1")
	c.Set("user_id", "U1")

	handleCreateSession(mgr)(c)

	if rec.Code != 400 {
		t.Errorf("status = %d, want 400 (坏格式该拒绝)", rec.Code)
	}
}
