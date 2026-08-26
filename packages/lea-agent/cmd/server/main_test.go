package main

import (
	"io"
	"net"
	"net/http"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
)

func init() { gin.SetMode(gin.TestMode) }

// 起一个真监听的服务器 —— 优雅退出的行为全在 net/http 的连接管理上，
// httptest.ResponseRecorder 测不出"Shutdown 会不会等在途请求"。
func drainFixture(t *testing.T, mgr *session.Manager, draining *atomic.Bool) (*http.Server, string) {
	t.Helper()

	r := gin.New()
	r.GET("/health", healthHandler(mgr, draining))
	// 模拟一个还在跑的 turn 的 SSE 连接：请求已经被接受，响应要过一会儿才写完。
	r.GET("/slow", func(c *gin.Context) {
		time.Sleep(600 * time.Millisecond)
		c.String(200, "finished")
	})

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("监听失败: %v", err)
	}
	srv := &http.Server{Handler: r}
	go func() { _ = srv.Serve(ln) }()

	return srv, "http://" + ln.Addr().String()
}

// 排空的两条核心契约：
//  1. 先让探针变红（编排层据此停止转发新流量），再关监听；
//  2. 已经在途的请求要放它跑完，而不是当场掐断 —— 那是"跑到一半的 turn"，
//     掐掉它等于丢掉答案（persistTurn 在 turn goroutine 的末尾）。
func TestDrain_FlipsHealthBeforeClosingListener(t *testing.T) {
	t.Setenv("LEA_AGENT_PRESTOP_DELAY", "400ms")
	t.Setenv("LEA_AGENT_DRAIN_TIMEOUT", "10s")

	var draining atomic.Bool
	mgr := session.NewManager()
	srv, base := drainFixture(t, mgr, &draining)

	// 排空之前是健康的
	if code, _ := get(t, base+"/health"); code != 200 {
		t.Fatalf("排空前 /health 应为 200，实际 %d", code)
	}

	// 一个在途请求：drain 开始时它还没写完
	type res struct {
		code int
		body string
	}
	inflight := make(chan res, 1)
	go func() {
		code, body := get(t, base+"/slow")
		inflight <- res{code, body}
	}()
	time.Sleep(150 * time.Millisecond) // 确保它真的进了服务器

	drained := make(chan struct{})
	go func() { defer close(drained); drain(srv, mgr, &draining) }()

	// preStop 窗口里探针必须已经红了，而监听还开着（这正是那段空档的意义：
	// 让编排层有时间摘 endpoint，而不是让已经路由过来的请求撞到拒连）。
	time.Sleep(150 * time.Millisecond)
	code, _ := get(t, base+"/health")
	if code != 503 {
		t.Errorf("preStop 窗口里 /health 应为 503（让 k8s 摘掉本 pod），实际 %d", code)
	}

	select {
	case <-drained:
	case <-time.After(10 * time.Second):
		t.Fatal("drain 没有返回")
	}

	// 在途那条必须善终
	select {
	case r := <-inflight:
		if r.code != 200 || r.body != "finished" {
			t.Errorf("在途请求被掐断了: code=%d body=%q —— 对应的是丢掉一个跑到一半的 turn", r.code, r.body)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("在途请求既没完成也没失败")
	}
}

// 排空结束后监听必须真的关掉，否则 pod 会在"已宣告不健康"的状态下继续收请求。
func TestDrain_StopsAcceptingAfterwards(t *testing.T) {
	t.Setenv("LEA_AGENT_PRESTOP_DELAY", "0")
	t.Setenv("LEA_AGENT_DRAIN_TIMEOUT", "5s")

	var draining atomic.Bool
	mgr := session.NewManager()
	srv, base := drainFixture(t, mgr, &draining)

	drain(srv, mgr, &draining)

	if _, err := http.Get(base + "/health"); err == nil {
		t.Error("排空之后仍能连上 —— 监听没关")
	}
}

// preStop 可以配成 0（比如 pod spec 里已经用 preStop hook 做了同样的事），
// 那时排空不该白等。
func TestDrain_ZeroPreStopDoesNotWait(t *testing.T) {
	t.Setenv("LEA_AGENT_PRESTOP_DELAY", "0")
	t.Setenv("LEA_AGENT_DRAIN_TIMEOUT", "5s")

	var draining atomic.Bool
	mgr := session.NewManager()
	srv, _ := drainFixture(t, mgr, &draining)

	start := time.Now()
	drain(srv, mgr, &draining)
	if elapsed := time.Since(start); elapsed > time.Second {
		t.Errorf("preStop=0 且无在跑 turn 时应立刻退出，实际 %s", elapsed)
	}
}

func TestDurationFromEnv(t *testing.T) {
	t.Run("未设置用默认值", func(t *testing.T) {
		if got := durationFromEnv("LEA_AGENT_NOT_SET_XYZ", 7*time.Second); got != 7*time.Second {
			t.Errorf("= %s, want 7s", got)
		}
	})
	t.Run("能解析", func(t *testing.T) {
		t.Setenv("LEA_AGENT_TEST_DUR", "90s")
		if got := durationFromEnv("LEA_AGENT_TEST_DUR", time.Second); got != 90*time.Second {
			t.Errorf("= %s, want 90s", got)
		}
	})
	// 配错了要退回默认值而不是变成 0 —— 0 会让排空立刻放弃，把"配置写错"
	// 悄悄变成"没有优雅退出"。
	t.Run("垃圾值退回默认", func(t *testing.T) {
		t.Setenv("LEA_AGENT_TEST_DUR", "三分钟")
		if got := durationFromEnv("LEA_AGENT_TEST_DUR", 3*time.Minute); got != 3*time.Minute {
			t.Errorf("= %s, want 3m", got)
		}
	})
	// 0 是合法的（preStop 允许配 0），不能跟"解析失败"混为一谈。
	t.Run("零值是合法的", func(t *testing.T) {
		t.Setenv("LEA_AGENT_TEST_DUR", "0")
		if got := durationFromEnv("LEA_AGENT_TEST_DUR", 5*time.Second); got != 0 {
			t.Errorf("= %s, want 0", got)
		}
	})
}

func get(t *testing.T, url string) (int, string) {
	t.Helper()
	resp, err := http.Get(url)
	if err != nil {
		return 0, err.Error()
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(b)
}
