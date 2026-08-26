package router

import (
	"math"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"

	"github.com/gin-contrib/sse"
	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/eventlog"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
)

// ── 重放必须跟直播逐字节一致 ─────────────────────────────────
//
// 这是整套续传里最要紧、也最容易悄悄坏掉的性质。
//
// 直播走 c.SSEvent 风格（原始对象交给 gin 的 sse 编码器）；重放走的是入库前渲染好的
// 字符串。两条路只要有一点格式差异，前端在断线重连后就会崩 —— 它对 delta 是直接取
// 字符串、对 tool_event 是 JSON.parse，格式一变两边都不对。而且这只在断线时发生，
// 正常测试全都会过。
//
// 这里的做法是把同一个载荷两条路各跑一遍，逐字节比。

// renderLive 走直播那条路：原始对象 → gin 的 sse 编码器。
func renderLive(ev session.SSEEvent) string {
	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	writeEvent(c, ev)
	return rec.Body.String()
}

// renderReplayed 走重放那条路：渲染好的字符串 → gin 的 sse 编码器。
func renderReplayed(seq uint64, evType, rendered string) string {
	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)
	c.Render(-1, sse.Event{Id: strconv.FormatUint(seq, 10), Event: evType, Data: rendered})
	return rec.Body.String()
}

func TestReplay_IsByteIdenticalToLive(t *testing.T) {
	cases := []struct {
		name string
		typ  string
		data any
	}{
		{"裸文本 delta", "delta", "你好世界"},
		{"空字符串", "delta", ""},
		{"nil 载荷（start/done 用的）", "start", nil},
		{"多行文本", "delta", "第一行\n第二行\n第三行"},
		{"带引号和反斜杠", "delta", `他说 "a\b" 然后走了`},
		{"map 载荷（result 用的）", "result", map[string]any{"text": "答案", "iterations": 3, "stopped": false}},
		{"嵌套 map（tool_event 用的）", "tool_event", map[string]any{
			"type":     "completed",
			"toolName": "code_interpreter",
			"output":   []any{map[string]any{"stream": "stdout", "text": "done\n"}},
		}},
		{"含中文键值的 map", "tool_event", map[string]any{"消息": "完成了", "n": 1}},
		{"整数", "delta", 42},
		{"布尔", "delta", true},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			const seq = 7
			// session.RenderSSEData 是入库时用的渲染函数 —— 必须跟 gin 的规则一致，
			// 这条测试就是在钉这件事。
			rendered := session.RenderSSEData(tc.data)

			live := renderLive(session.SSEEvent{Type: tc.typ, Data: tc.data, Seq: seq})
			replayed := renderReplayed(seq, tc.typ, rendered)

			if live != replayed {
				t.Errorf("重放跟直播不一致 —— 断线重连后前端会拿到格式不同的载荷\n"+
					"直播: %q\n重放: %q\n渲染结果: %q", live, replayed, rendered)
			}
		})
	}
}

// id: 字段是续传的全部依据 —— 客户端把它报回来当 ?from=。没有它的话事件日志写了
// 也没人能用。
func TestWriteEvent_EmitsID(t *testing.T) {
	out := renderLive(session.SSEEvent{Type: "delta", Data: "x", Seq: 42})
	if !strings.Contains(out, "id:42") {
		t.Errorf("没写 id 字段：%q —— 客户端无从得知续传位置", out)
	}
}

// Seq==0 的事件不带 id。那些是没过 broadcast 直接写的（握手用的 session 事件），
// 不在日志里 —— 给个假 id 会让客户端从一个不存在的位置续传。
func TestWriteEvent_OmitsIDWhenUnsequenced(t *testing.T) {
	out := renderLive(session.SSEEvent{Type: "session", Data: map[string]any{"session_id": "abc"}})
	if strings.Contains(out, "id:") {
		t.Errorf("未编号的事件带了 id：%q —— 客户端会拿它当续传游标", out)
	}
}

// ── 续传游标解析 ────────────────────────────────────────────

func TestParseFrom(t *testing.T) {
	cases := []struct {
		in      string
		want    uint64
		wantErr bool
		why     string
	}{
		{"", 0, false, "不传就是从头补 —— 首次连接和过期重连都走这条"},
		{"0", 0, false, "显式 0 同上"},
		{"1", 1, false, ""},
		{"18446744073709551615", math.MaxUint64, false, "uint64 上界要能解析"},
		{"-1", 0, true, "负数无意义，报错比当 0 处理好 —— 当 0 会静默重发整轮"},
		{"abc", 0, true, "非数字"},
		{"1.5", 0, true, "小数"},
		{"1e3", 0, true, "科学计数法不认"},
	}
	for _, tc := range cases {
		got, err := parseFrom(tc.in)
		if tc.wantErr {
			if err == nil {
				t.Errorf("parseFrom(%q) 应报错 —— %s", tc.in, tc.why)
			}
			continue
		}
		if err != nil {
			t.Errorf("parseFrom(%q) 报错了: %v", tc.in, err)
			continue
		}
		if got != tc.want {
			t.Errorf("parseFrom(%q) = %d, want %d", tc.in, got, tc.want)
		}
	}
}

// ── 重放的终止条件 ──────────────────────────────────────────
//
// 见到 done 就停，别接着往下发。done 之后的条目属于**下一轮**，
// 而这条连接是为当前这轮建的。
func TestReplay_StopsAtDone(t *testing.T) {
	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)

	cursor := uint64(0)
	stopped := replay(c, []eventlog.Entry{
		{Seq: 1, Type: "start", Data: "<nil>"},
		{Seq: 2, Type: "delta", Data: "答案"},
		{Seq: 3, Type: "done", Data: "<nil>"},
		{Seq: 4, Type: "start", Data: "<nil>"}, // 下一轮的开头
	}, &cursor)

	if !stopped {
		t.Error("见到 done 应报告本轮结束")
	}
	if cursor != 3 {
		t.Errorf("游标 = %d, want 3 —— done 之后不该再推进", cursor)
	}
	if strings.Count(rec.Body.String(), "event:start") != 1 {
		t.Errorf("把下一轮的 start 也发出去了：\n%s", rec.Body.String())
	}
}

// 没见到 done 就返回 false，让调用方接上直播继续等。
func TestReplay_ContinuesWhenTurnUnfinished(t *testing.T) {
	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)

	cursor := uint64(0)
	if replay(c, []eventlog.Entry{
		{Seq: 1, Type: "start", Data: "<nil>"},
		{Seq: 2, Type: "delta", Data: "写到一半"},
	}, &cursor) {
		t.Error("没见到 done 却报告结束了 —— 客户端会在 turn 跑完之前被关掉流")
	}
	if cursor != 2 {
		t.Errorf("游标 = %d, want 2", cursor)
	}
}

// error 不是终止事件：done 在 result/error 之后无条件广播，所以重放也得接着等 done。
func TestReplay_ErrorIsNotTerminal(t *testing.T) {
	rec := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(rec)

	cursor := uint64(0)
	if replay(c, []eventlog.Entry{{Seq: 1, Type: "error", Data: "炸了"}}, &cursor) {
		t.Error("error 被当成终止事件了 —— 客户端会漏掉收尾的 done")
	}
}
