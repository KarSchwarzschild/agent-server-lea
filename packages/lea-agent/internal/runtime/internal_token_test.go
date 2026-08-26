package runtime

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// stubRT 记录它实际收到的请求，用来断言 Transport 做了什么。
type stubRT struct {
	got *http.Request
}

func (s *stubRT) RoundTrip(req *http.Request) (*http.Response, error) {
	s.got = req
	return &http.Response{
		StatusCode: 200,
		Body:       http.NoBody,
		Header:     make(http.Header),
		Request:    req,
	}, nil
}

func TestInternalToken_AddsHeader(t *testing.T) {
	stub := &stubRT{}
	tr := &internalTokenTransport{token: "s3cret", base: stub}

	req := httptest.NewRequest("POST", "http://lea-tools:8400/tools/execute", nil)
	if _, err := tr.RoundTrip(req); err != nil {
		t.Fatal(err)
	}
	if got := stub.got.Header.Get(InternalTokenHeader); got != "s3cret" {
		t.Errorf("下游收到的 token = %q, want %q", got, "s3cret")
	}
}

// RoundTripper 的契约：不得修改传入的 Request。
//
// 违反了的后果很隐蔽 —— 调用方复用同一个 Request 做重试或日志时会看到被改过的
// 版本。更实际的坑是：如果直接改 req.Header（不 Clone），而 Header map 是跟别处
// 共享的，就会串到别的请求上。这条测试钉住 Clone 没被"优化"掉。
func TestInternalToken_DoesNotMutateOriginal(t *testing.T) {
	stub := &stubRT{}
	tr := &internalTokenTransport{token: "s3cret", base: stub}

	req := httptest.NewRequest("GET", "http://lea-tools:8400/health", nil)
	if _, err := tr.RoundTrip(req); err != nil {
		t.Fatal(err)
	}
	if got := req.Header.Get(InternalTokenHeader); got != "" {
		t.Errorf("原始 request 被改了（%q）—— 违反 RoundTripper 契约", got)
	}
}

// token 没配时原样透传。这是"上线顺序无关"的那一半：调用方还没配密钥时，
// 请求不能因为多了个空头而被服务端拒掉。
func TestInternalToken_PassesThroughWhenUnset(t *testing.T) {
	stub := &stubRT{}
	tr := &internalTokenTransport{token: "", base: stub}

	req := httptest.NewRequest("GET", "http://lea-tools:8400/health", nil)
	if _, err := tr.RoundTrip(req); err != nil {
		t.Fatal(err)
	}
	if stub.got == nil {
		t.Fatal("请求没被转发下去")
	}
	if _, ok := stub.got.Header[InternalTokenHeader]; ok {
		t.Error("token 未配置时不该带这个头 —— 空值会被服务端当成无效 token 拒掉")
	}
}

// base 为 nil 时退回 http.DefaultTransport，别 panic。
// 五个 client 里有的没显式设过 Transport，那时 c.Transport 就是 nil。
func TestInternalToken_NilBaseUsesDefault(t *testing.T) {
	tr := &internalTokenTransport{token: "x", base: nil}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get(InternalTokenHeader) != "x" {
			t.Errorf("服务端没收到 token")
		}
	}))
	defer srv.Close()

	req, err := http.NewRequest("GET", srv.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	resp, err := tr.RoundTrip(req)
	if err != nil {
		t.Fatalf("base 为 nil 时应退回 DefaultTransport: %v", err)
	}
	_ = resp.Body.Close()
}
