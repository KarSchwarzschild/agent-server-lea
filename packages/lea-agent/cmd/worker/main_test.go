package main

// worker 的可观测性端口。
//
// 这个进程在这之前不监听任何端口、compose 里 healthcheck 也是 disable，所以
// 它卡死的表现是容器 Running、日志不滚、用户消息没回复，而面板全绿。这里确认
// 那两个端点真的起得来、真的能答。

import (
	"io"
	"net"
	"net/http"
	"strconv"
	"strings"
	"testing"
	"time"
)

// freePort 要一个空闲端口。不写死端口号，否则本机正好占着就假失败。
func freePort(t *testing.T) string {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	return strconv.Itoa(l.Addr().(*net.TCPAddr).Port)
}

func TestObservabilityServerServesHealthAndMetrics(t *testing.T) {
	port := freePort(t)
	t.Setenv("LEA_AGENT_WORKER_METRICS_PORT", port)

	startObservabilityServer()

	base := "http://127.0.0.1:" + port
	waitUp(t, base+"/health")

	t.Run("health", func(t *testing.T) {
		res, err := http.Get(base + "/health")
		if err != nil {
			t.Fatal(err)
		}
		defer res.Body.Close()
		if res.StatusCode != http.StatusOK {
			t.Fatalf("status=%d", res.StatusCode)
		}
		body, _ := io.ReadAll(res.Body)
		if !strings.Contains(string(body), "lea-agent-worker") {
			t.Fatalf("body=%s", body)
		}
	})

	t.Run("metrics", func(t *testing.T) {
		res, err := http.Get(base + "/metrics")
		if err != nil {
			t.Fatal(err)
		}
		defer res.Body.Close()
		if res.StatusCode != http.StatusOK {
			t.Fatalf("status=%d", res.StatusCode)
		}
		body, _ := io.ReadAll(res.Body)
		// Go 运行时指标是 promhttp 默认就带的，能看到它们说明 registry 接上了。
		if !strings.Contains(string(body), "go_goroutines") {
			t.Fatalf("/metrics 里没有 go_goroutines，registry 没接上：%.200s", body)
		}
	})
}

func TestObservabilityPortIsConfigurable(t *testing.T) {
	port := freePort(t)
	t.Setenv("LEA_AGENT_WORKER_METRICS_PORT", port)
	startObservabilityServer()
	waitUp(t, "http://127.0.0.1:"+port+"/health")
}

func TestEnvFallback(t *testing.T) {
	if got := env("LEA_TEST_ABSENT_KEY_XYZ", "fallback"); got != "fallback" {
		t.Fatalf("got=%q", got)
	}
	t.Setenv("LEA_TEST_PRESENT_KEY_XYZ", "  value  ")
	if got := env("LEA_TEST_PRESENT_KEY_XYZ", "fallback"); got != "value" {
		t.Fatalf("got=%q（应该 trim 掉空格）", got)
	}
	// 只有空格的值要当成没设，否则 ":  " 会变成非法监听地址
	t.Setenv("LEA_TEST_BLANK_KEY_XYZ", "   ")
	if got := env("LEA_TEST_BLANK_KEY_XYZ", "fallback"); got != "fallback" {
		t.Fatalf("got=%q（全空格该回落到默认值）", got)
	}
}

func waitUp(t *testing.T, url string) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		res, err := http.Get(url)
		if err == nil {
			res.Body.Close()
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("%s 在 5s 内没起来", url)
}
