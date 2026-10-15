package main

// 这些测试盯的是**本地看不出来**的性质。
//
// 指标这东西的坑在于：写错了功能照常，接口照常返回，/metrics 里也确实有数，
// 要等 Prometheus 跑几天内存爆了、或者线上 SSE 卡住了才发现。所以这里专门测
// 三件事：endpoint 标签是不是路由模板（基数）、真实路径会不会泄进标签（可远程
// 触发的基数炸弹）、包了一层 ResponseWriter 之后 Flush 还在不在（SSE）。

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/prometheus/common/expfmt"
)

// 固定一条有路径参数的真实路由。routes() 里是 mux.HandleFunc("/api/sessions/", ...)，
// 所以 /api/sessions/<任何东西> 都该归到这一条上。
const sessionsPattern = "/api/sessions/"

func observeOnly(t *testing.T) http.Handler {
	t.Helper()
	g := &gateway{}
	mux := g.routes()
	// next 用一个最简 handler：这里只关心 observe 怎么打标签，不要让真的
	// agentRoute/configRoute 跑起来（它们需要 internalToken 和租户服务）。
	return g.observe(mux, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
}

// 50 个不同的 session id 只能产生 1 条时间序列。
//
// 这是整个文件最重要的一条：如果 endpoint 标签用了 r.URL.Path，这里会变成 50。
func TestObserveCollapsesPathParamsIntoOneSeries(t *testing.T) {
	httpRequestsTotal.Reset()
	h := observeOnly(t)

	for i := 0; i < 50; i++ {
		req := httptest.NewRequest(http.MethodGet, "/api/sessions/session-"+string(rune('a'+i%26))+string(rune('0'+i/26)), nil)
		h.ServeHTTP(httptest.NewRecorder(), req)
	}

	if got := testutil.CollectAndCount(httpRequestsTotal); got != 1 {
		t.Fatalf("50 个不同 session id 产生了 %d 条时间序列，应该只有 1 条 —— endpoint 标签很可能用了真实路径", got)
	}
	if got := testutil.ToFloat64(httpRequestsTotal.WithLabelValues(sessionsPattern, http.MethodGet, "2xx")); got != 50 {
		t.Fatalf("endpoint=%q 的计数是 %v，应该是 50", sessionsPattern, got)
	}
}

// 没匹配上的路径全归到 "unmatched"，**而且真实路径不能出现在标签里**。
//
// 这条比上一条更要紧：网关是公网入口，404 的路径完全由调用方决定。回落到真实
// 路径的话，外面写个循环打 /api/<随机> 就能无限长时间序列 —— 可以远程打爆
// Prometheus 的内存。所以这里不只断言条数，还要断言暴露出来的文本里不含那些路径。
func TestObserveNeverLeaksUnmatchedPathIntoLabel(t *testing.T) {
	httpRequestsTotal.Reset()
	h := observeOnly(t)

	probes := []string{
		"/api/does-not-exist-1",
		"/api/does-not-exist-2",
		"/wp-login.php",
		"/.env",
	}
	for _, p := range probes {
		h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, p, nil))
	}

	if got := testutil.CollectAndCount(httpRequestsTotal); got != 1 {
		t.Fatalf("4 个未匹配路径产生了 %d 条时间序列，应该合并成 1 条", got)
	}

	// 直接检查暴露出去的指标文本，确认扫描者给的路径没有被原样记下来。
	// ⚠️ metricNames 不能省：CollectAndFormat 按名字过滤，一个都不传会返回空字节
	// 串且 err=nil —— 那样下面的"路径没泄漏"断言会永远通过，等于没测。
	exposed, err := testutil.CollectAndFormat(
		httpRequestsTotal, expfmt.TypeTextPlain, "lea_gateway_http_requests_total",
	)
	if err != nil {
		t.Fatal(err)
	}
	if len(exposed) == 0 {
		t.Fatal("指标文本是空的 —— 断言会假通过，检查 metricNames 参数")
	}
	for _, p := range probes {
		if strings.Contains(string(exposed), p) {
			t.Fatalf("未匹配的真实路径 %q 泄进了指标标签：\n%s", p, exposed)
		}
	}
	if !strings.Contains(string(exposed), `endpoint="unmatched"`) {
		t.Fatalf("未匹配请求没有记成 unmatched：\n%s", exposed)
	}
}

// 401 也要被统计到 —— observe 挂在 auth 外面就是为了这个。
// 鉴权整体挂掉是最该看见的故障，统计放在 auth 里面正好会漏掉它。
func TestObserveCountsAuthRejections(t *testing.T) {
	httpRequestsTotal.Reset()
	authRejectionsTotal.Reset()

	g, _ := testGateway(t)
	mux := g.routes()
	h := g.observe(mux, g.auth(mux))

	// 不带 Authorization，必然被 auth 拦掉
	req := httptest.NewRequest(http.MethodGet, "/api/sessions/abc", nil)
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)

	if res.Code != http.StatusUnauthorized {
		t.Fatalf("status=%d，应该是 401", res.Code)
	}
	if got := testutil.ToFloat64(httpRequestsTotal.WithLabelValues(sessionsPattern, http.MethodGet, "4xx")); got != 1 {
		t.Fatalf("401 没有被 observe 统计到（got=%v）—— observe 可能被放到 auth 里面了", got)
	}
	if got := testutil.ToFloat64(authRejectionsTotal.WithLabelValues("missing_token")); got != 1 {
		t.Fatalf("missing_token 计数=%v，应该是 1", got)
	}
}

// statusWriter 必须保住 Flusher。
//
// /api/chat/ 是 SSE 长流，ReverseProxy 靠类型断言拿 Flusher 逐块推。包一层之后
// 断言失败的话，流式回复会退化成"全部缓冲到结束才一次性吐出来" —— 不报错，只是
// 前端看起来卡住，极难定位到中间件上。
func TestStatusWriterPreservesFlusher(t *testing.T) {
	rec := httptest.NewRecorder()
	sw := &statusWriter{ResponseWriter: rec}

	f, ok := interface{}(sw).(http.Flusher)
	if !ok {
		t.Fatal("statusWriter 没实现 http.Flusher —— SSE 会退化成缓冲输出")
	}
	f.Flush()
	if !rec.Flushed {
		t.Fatal("Flush 没有转发到底层 ResponseWriter")
	}
	if sw.status != http.StatusOK {
		t.Fatalf("Flush 提交响应后 status=%d，应该是 200", sw.status)
	}
}

// 直接 Write 不先 WriteHeader 时，状态码要记成 200 而不是 0/unknown。
func TestStatusWriterDefaultsToOKOnBareWrite(t *testing.T) {
	rec := httptest.NewRecorder()
	sw := &statusWriter{ResponseWriter: rec}
	if _, err := sw.Write([]byte("hi")); err != nil {
		t.Fatal(err)
	}
	if sw.status != http.StatusOK {
		t.Fatalf("status=%d，应该是 200", sw.status)
	}
}

// net/http 只接受第一次 WriteHeader；包装器记录的状态必须遵守同样的规则。
func TestStatusWriterKeepsFirstStatus(t *testing.T) {
	rec := httptest.NewRecorder()
	sw := &statusWriter{ResponseWriter: rec}
	sw.WriteHeader(http.StatusCreated)
	sw.WriteHeader(http.StatusInternalServerError)

	if sw.status != http.StatusCreated {
		t.Fatalf("status=%d，应该保留第一次写入的 201", sw.status)
	}
	if rec.Code != http.StatusCreated {
		t.Fatalf("底层 ResponseWriter status=%d，应该是 201", rec.Code)
	}
}

// /metrics 不能出现在业务路由表里。
//
// 网关是公网入口（compose 里 8840:8080 发布到宿主），业务端口上放行 /metrics
// 等于把请求量、错误率、全部端点名公开到互联网。指标走独立端口，见 metrics.go。
func TestMetricsIsNotExposedOnPublicRouteTable(t *testing.T) {
	g := &gateway{}
	mux := g.routes()

	_, pattern := mux.Handler(httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if pattern != "" {
		t.Fatalf("/metrics 匹配到了业务路由 %q —— 它应该只在独立的指标端口上", pattern)
	}
}
