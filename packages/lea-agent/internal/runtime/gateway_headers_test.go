package runtime

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

// 身份头必须真的发出去。
//
// ExecuteRemoteTool 的签名里一直有 userID，但曾经**从来没设过这个头** —— 参数收下了，
// 到了最后一跳被丢掉。网关侧因此只能按 (租户, 名字) 找工具，而工具名是用户内唯一的
// （migrations/007），同一个租户里 alice 和 bob 可以各有一个 weather。
//
// 这条测试存在的理由是它覆盖的那段**没有别的东西覆盖**：网关侧的 e2e 是 httpx 直接
// 打 lea-tools，验证的是"网关收到 X-User-ID 之后做得对"；引擎会不会发这个头，在那条
// 链路上完全测不到。删掉 gateway.go 里那三行，所有 Python 测试仍然全绿，症状要到
// 生产上才出现，而且表现为"我的私有工具时灵时不灵"。
func TestExecuteRemoteTool_SendsIdentityHeaders(t *testing.T) {
	var got http.Header
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Clone()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"success","result":{"ok":true}}`))
	}))
	defer srv.Close()

	restore := toolsGatewayURL
	toolsGatewayURL = srv.URL
	defer func() { toolsGatewayURL = restore }()

	_, err := ExecuteRemoteTool(context.Background(), "weather",
		map[string]any{"city": "Beijing"}, "tenant-a", "alice")
	if err != nil {
		t.Fatal(err)
	}

	if v := got.Get("X-Tenant-ID"); v != "tenant-a" {
		t.Errorf("X-Tenant-ID = %q, want %q", v, "tenant-a")
	}
	if v := got.Get("X-User-ID"); v != "alice" {
		t.Errorf("X-User-ID = %q, want %q —— 身份没发出去，网关只能按租户解析工具", v, "alice")
	}
}

// userID 为空时**不设这个头**，而不是设一个空字符串。
//
// 网关把"没有 X-User-ID"当成 fail-closed（只解析平台工具，见 visible_tools）。
// 设成空串的话语义变了：那是一个"有身份但匹配不到任何人"的值，比缺席更容易被误读，
// 而且一旦哪天网关改成必填校验，空串会先通过校验再在 SQL 层静默命中不了任何行。
func TestExecuteRemoteTool_OmitsEmptyUserID(t *testing.T) {
	var got http.Header
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Clone()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"success","result":{}}`))
	}))
	defer srv.Close()

	restore := toolsGatewayURL
	toolsGatewayURL = srv.URL
	defer func() { toolsGatewayURL = restore }()

	if _, err := ExecuteRemoteTool(context.Background(), "web_search",
		map[string]any{}, "tenant-a", ""); err != nil {
		t.Fatal(err)
	}

	if _, ok := got["X-User-Id"]; ok {
		t.Errorf("userID 为空时不该带 X-User-ID，实际带了 %q", got.Get("X-User-ID"))
	}
}
