package router

import (
	"context"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
)

func init() { gin.SetMode(gin.TestMode) }

// newPumpFixture 造一个带可取消 request ctx 的 gin.Context 和一个事件 channel。
func newPumpFixture() (*gin.Context, *httptest.ResponseRecorder, chan session.SSEEvent, context.CancelFunc) {
	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	ctx, cancel := context.WithCancel(context.Background())
	c.Request = httptest.NewRequest("POST", "/api/chat/stream", nil).WithContext(ctx)
	return c, rec, make(chan session.SSEEvent, 16), cancel
}

// sentEventTypes 从 SSE 响应体里抽出事件类型序列。不硬编码 gin 的分隔符格式，
// 只认 "event:" 前缀，免得 gin 换了空格写法测试就假红。
func sentEventTypes(body string) []string {
	var out []string
	for _, line := range strings.Split(body, "\n") {
		if rest, ok := strings.CutPrefix(strings.TrimSpace(line), "event:"); ok {
			out = append(out, strings.TrimSpace(rest))
		}
	}
	return out
}

// runPump 跑 pumpEvents 并等它返回，超时即失败（泵卡住是真 bug，不该让测试挂死）。
func runPump(t *testing.T, c *gin.Context, events chan session.SSEEvent, onGone func()) {
	t.Helper()
	done := make(chan struct{})
	go func() { defer close(done); pumpEvents(c, events, onGone) }()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("pumpEvents 没有返回")
	}
}

// 上一轮被 Send 掐断后仍会广播它自己的 done，而我们的订阅者在 Send 之前就挂上了。
// 泵必须把那条尾巴丢掉，否则本轮的流会在自己的 start 之前就关掉 —— 症状是用户
// 看到"流秒关、没答案"。
func TestPumpEvents_DiscardsPreviousTurnTail(t *testing.T) {
	c, rec, events, cancel := newPumpFixture()
	defer cancel()

	// 老 turn 的收尾
	events <- session.SSEEvent{Type: "result", Data: "旧轮的结果"}
	events <- session.SSEEvent{Type: "done"}
	// 本轮
	events <- session.SSEEvent{Type: "start"}
	events <- session.SSEEvent{Type: "delta", Data: "本轮"}
	events <- session.SSEEvent{Type: "done"}

	runPump(t, c, events, func() { t.Error("客户端没断开，不该走断线分支") })

	got := sentEventTypes(rec.Body.String())
	want := []string{"start", "delta", "done"}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("事件序列 = %v, 期望 %v（老轮的 result/done 应被丢弃）", got, want)
	}
	if strings.Contains(rec.Body.String(), "旧轮的结果") {
		t.Error("老轮的 result 正文泄漏到了本轮的流里")
	}
}

// start 之后的每个事件都要原样转发，一个都不能少。
func TestPumpEvents_ForwardsWholeTurn(t *testing.T) {
	c, rec, events, cancel := newPumpFixture()
	defer cancel()

	events <- session.SSEEvent{Type: "start"}
	events <- session.SSEEvent{Type: "thinking", Data: "想"}
	events <- session.SSEEvent{Type: "delta", Data: "答"}
	events <- session.SSEEvent{Type: "tool_event", Data: "工具"}
	events <- session.SSEEvent{Type: "result", Data: "结果"}
	events <- session.SSEEvent{Type: "done"}

	runPump(t, c, events, func() { t.Error("客户端没断开，不该走断线分支") })

	got := sentEventTypes(rec.Body.String())
	want := []string{"start", "thinking", "delta", "tool_event", "result", "done"}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("事件序列 = %v, 期望 %v", got, want)
	}
}

// done 是终止信号：收到就关流，之后 channel 里剩下的东西不该再写出去
// （下一轮的事件属于下一条连接）。
func TestPumpEvents_StopsAtDone(t *testing.T) {
	c, rec, events, cancel := newPumpFixture()
	defer cancel()

	events <- session.SSEEvent{Type: "start"}
	events <- session.SSEEvent{Type: "done"}
	events <- session.SSEEvent{Type: "delta", Data: "下一轮的内容"}

	runPump(t, c, events, func() { t.Error("客户端没断开，不该走断线分支") })

	if strings.Contains(rec.Body.String(), "下一轮的内容") {
		t.Error("done 之后仍在往流里写")
	}
}

// error 不是终止事件 —— done 在 result/error 之后无条件广播，所以泵得继续等 done，
// 否则客户端会漏掉收尾。
func TestPumpEvents_ErrorIsNotTerminal(t *testing.T) {
	c, rec, events, cancel := newPumpFixture()
	defer cancel()

	events <- session.SSEEvent{Type: "start"}
	events <- session.SSEEvent{Type: "error", Data: "炸了"}
	events <- session.SSEEvent{Type: "done"}

	runPump(t, c, events, func() { t.Error("客户端没断开，不该走断线分支") })

	got := sentEventTypes(rec.Body.String())
	want := []string{"start", "error", "done"}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("事件序列 = %v, 期望 %v", got, want)
	}
}

// 会话被淘汰/清理/删除时 Close 会关掉订阅 channel。泵得干净收场，不能 panic
// 也不能空转。
func TestPumpEvents_ReturnsWhenSessionClosed(t *testing.T) {
	c, _, events, cancel := newPumpFixture()
	defer cancel()

	events <- session.SSEEvent{Type: "start"}
	close(events)

	runPump(t, c, events, func() { t.Error("会话是被关的，不是客户端断的") })
}

// ── 断线策略 ─────────────────────────────────────────────────
//
// 默认必须是 continue：这一层分不清"关了标签页"和"地铁进隧道"，而把默认值定成
// cancel 意味着一次两秒的网络抖动就能弄死一个跑了三分钟的任务。
func TestShouldCancelOnDisconnect(t *testing.T) {
	cases := []struct {
		policy string
		cancel bool
		why    string
	}{
		{"", false, "没传就是默认，默认必须让 turn 跑完"},
		{"continue", false, "显式 continue"},
		{"cancel", true, "显式 cancel —— 唯一该掐断的取值"},
		{"CANCEL", false, "大小写不匹配就不算 cancel，宁可多烧 token 也不误杀"},
		{"true", false, "未知取值按 continue 处理"},
		{"stop", false, "未知取值按 continue 处理（不是 cancel 的近义词就认）"},
	}
	for _, c := range cases {
		if got := shouldCancelOnDisconnect(c.policy); got != c.cancel {
			t.Errorf("shouldCancelOnDisconnect(%q) = %v, want %v —— %s", c.policy, got, c.cancel, c.why)
		}
	}
}

// ── 心跳 ────────────────────────────────────────────────────
//
// runPumpEvery 跟 runPump 一样，但心跳间隔可指定 —— 生产值是 20s，测试等不了。
func runPumpEvery(t *testing.T, c *gin.Context, events chan session.SSEEvent, keepalive time.Duration, onGone func()) {
	t.Helper()
	done := make(chan struct{})
	go func() { defer close(done); pumpEventsEvery(c, events, keepalive, onGone) }()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("pumpEventsEvery 没有返回")
	}
}

// 静默期必须往连接里写东西。没有这个的话一次三分钟的沙箱执行会被 nginx 按
// proxy_read_timeout（默认 60s）掐掉连接，而 turn 其实还在后台跑完并落库了 ——
// 症状是"流断了但刷新有答案"，很容易误判成 turn 挂了。
func TestPumpEvents_SendsKeepaliveWhileSilent(t *testing.T) {
	c, rec, events, cancel := newPumpFixture()
	defer cancel()

	events <- session.SSEEvent{Type: "start"}
	go func() {
		time.Sleep(160 * time.Millisecond) // 让心跳打上几拍
		events <- session.SSEEvent{Type: "done"}
	}()

	runPumpEvery(t, c, events, 40*time.Millisecond, func() { t.Error("客户端没断开") })

	body := rec.Body.String()
	if !strings.Contains(body, ": keepalive") {
		t.Fatal("静默期没有发心跳 —— 中间设备会按空闲超时掐掉连接")
	}
	// 心跳必须是 SSE 注释，不能混进事件序列 —— 否则前端会收到一个它不认识的事件。
	got := sentEventTypes(body)
	want := []string{"start", "done"}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("心跳污染了事件序列: %v，期望 %v", got, want)
	}
}

// 心跳不能受 start 门槛约束。Send 到引擎吐第一个字之间要建会话、注入记忆、拉附件，
// 这段本身就可能是好几秒，一样会被判空闲。
func TestPumpEvents_KeepaliveFiresBeforeStart(t *testing.T) {
	c, rec, events, cancel := newPumpFixture()
	defer cancel()

	go func() {
		time.Sleep(120 * time.Millisecond) // 首字节迟迟不来
		events <- session.SSEEvent{Type: "start"}
		events <- session.SSEEvent{Type: "done"}
	}()

	runPumpEvery(t, c, events, 40*time.Millisecond, func() { t.Error("客户端没断开") })

	if !strings.Contains(rec.Body.String(), ": keepalive") {
		t.Fatal("start 之前不发心跳 —— 等首字节那段同样会被 LB 判空闲")
	}
}

// 活跃的流不需要心跳：每写一次真数据就 Reset 计时器。不 Reset 的话正常对话里
// 也会周期性掺进注释行，虽然无害但是噪音。
func TestPumpEvents_ActiveStreamNeedsNoKeepalive(t *testing.T) {
	c, rec, events, cancel := newPumpFixture()
	defer cancel()

	events <- session.SSEEvent{Type: "start"}
	go func() {
		for i := 0; i < 6; i++ {
			events <- session.SSEEvent{Type: "delta", Data: "x"}
			time.Sleep(10 * time.Millisecond)
		}
		events <- session.SSEEvent{Type: "done"}
	}()

	// 间隔远大于整轮耗时（约 60ms），所以一次都不该触发
	runPumpEvery(t, c, events, 500*time.Millisecond, func() { t.Error("客户端没断开") })

	if strings.Contains(rec.Body.String(), "keepalive") {
		t.Error("活跃的流插了心跳 —— tick.Reset 没生效")
	}
}

// 客户端断开时走断线策略（onClientGone），而不是默默返回 —— 那条日志是
// "turn 还在烧 token 但没人看"的唯一线索。
func TestPumpEvents_InvokesPolicyOnClientDisconnect(t *testing.T) {
	c, _, events, cancel := newPumpFixture()

	events <- session.SSEEvent{Type: "start"}
	gone := make(chan struct{})

	go func() {
		// 等 start 被消费掉再断，免得跟事件投递抢先后
		time.Sleep(50 * time.Millisecond)
		cancel()
	}()

	runPump(t, c, events, func() { close(gone) })

	select {
	case <-gone:
	default:
		t.Error("客户端断开了但没走 onClientGone")
	}
}

// gin 的路由冲突是注册时 panic，不是编译错误 —— POST /chat/stream 和
// GET /chat/stream/:session_id 共用路径前缀，值得用一个测试钉住，
// 免得下次改路由时在生产启动阶段才炸。
func TestRegisterRoutes_NoConflict(t *testing.T) {
	defer func() {
		if r := recover(); r != nil {
			t.Fatalf("路由注册 panic: %v", r)
		}
	}()
	mgr := session.NewManager()
	defer mgr.Close()
	RegisterRoutes(gin.New().Group("/api"), mgr)
}
