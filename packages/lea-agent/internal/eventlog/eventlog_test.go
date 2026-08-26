package eventlog

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"testing"
	"time"
)

// TEST_REDIS_URL='redis://127.0.0.1:6379/0' go test ./internal/eventlog/
func testLog(t *testing.T) *Log {
	t.Helper()
	url := os.Getenv("TEST_REDIS_URL")
	if url == "" {
		t.Skip("TEST_REDIS_URL 未设置，跳过（需要真 Redis）")
	}
	l, err := Open(url, defaultMaxLen, 2*time.Minute)
	if err != nil {
		t.Fatalf("连 Redis 失败: %v", err)
	}
	t.Cleanup(func() { _ = l.rdb.Close() })
	return l
}

func uniqueSession(t *testing.T) string {
	t.Helper()
	var b [6]byte
	if _, err := rand.Read(b[:]); err != nil {
		t.Fatal(err)
	}
	id := "evlog-" + hex.EncodeToString(b[:])
	return id
}

// writeAll 同步写完一批（Append 是异步的，Close 会等队列排空）。
func writeAll(t *testing.T, l *Log, sessionID string, entries ...Entry) {
	t.Helper()
	w := l.NewWriter(sessionID)
	for _, e := range entries {
		w.Append(e)
	}
	w.Close()
	if w.Degraded() {
		t.Fatal("写入降级了 —— 后续断言无意义")
	}
	t.Cleanup(func() { _ = l.rdb.Del(context.Background(), keyFor(sessionID)).Err() })
}

func TestRange_ReplaysEverythingFromScratch(t *testing.T) {
	l := testLog(t)
	id := uniqueSession(t)

	writeAll(t, l, id,
		Entry{Seq: 1, Type: "start", Data: "<nil>"},
		Entry{Seq: 2, Type: "delta", Data: "你"},
		Entry{Seq: 3, Type: "delta", Data: "好"},
		Entry{Seq: 4, Type: "done", Data: "<nil>"},
	)

	got, err := l.Range(context.Background(), id, 0)
	if err != nil {
		t.Fatalf("Range 失败: %v", err)
	}
	if len(got) != 4 {
		t.Fatalf("从头补应拿到 4 条，实际 %d", len(got))
	}
	// 顺序和内容都不能变 —— 顺序错了正文就乱了
	want := []string{"<nil>", "你", "好", "<nil>"}
	for i, e := range got {
		if e.Seq != uint64(i+1) {
			t.Errorf("第 %d 条 Seq = %d, want %d", i, e.Seq, i+1)
		}
		if e.Data != want[i] {
			t.Errorf("第 %d 条 Data = %q, want %q", i, e.Data, want[i])
		}
	}
}

// 续传的核心：from=N 只补 N 之后的，不能重发也不能漏。
//
// 多补一条 = 客户端正文里出现重复片段；少补一条 = 正文缺字。两者都是静默的。
func TestRange_ResumesAfterCursorExactly(t *testing.T) {
	l := testLog(t)
	id := uniqueSession(t)

	writeAll(t, l, id,
		Entry{Seq: 1, Type: "start", Data: "<nil>"},
		Entry{Seq: 2, Type: "delta", Data: "A"},
		Entry{Seq: 3, Type: "delta", Data: "B"},
		Entry{Seq: 4, Type: "delta", Data: "C"},
	)

	got, err := l.Range(context.Background(), id, 2)
	if err != nil {
		t.Fatalf("Range 失败: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("from=2 应补 2 条（seq 3、4），实际 %d 条: %+v", len(got), got)
	}
	if got[0].Seq != 3 || got[0].Data != "B" {
		t.Errorf("第一条应是 seq=3 \"B\"，实际 seq=%d %q —— 边界差一就会重复或缺字",
			got[0].Seq, got[0].Data)
	}
	if got[1].Seq != 4 {
		t.Errorf("第二条应是 seq=4，实际 %d", got[1].Seq)
	}
}

// 已经补到最新时返回空，而不是 ErrNoLog —— 后者会让客户端误以为日志没了、
// 白白退回轮询，而其实它只需要接着等。
func TestRange_CaughtUpIsNotAnError(t *testing.T) {
	l := testLog(t)
	id := uniqueSession(t)

	writeAll(t, l, id, Entry{Seq: 1, Type: "start", Data: "<nil>"})

	got, err := l.Range(context.Background(), id, 1)
	if err != nil {
		t.Fatalf("已追平时不该报错，实际 %v", err)
	}
	if len(got) != 0 {
		t.Errorf("已追平应返回空，实际 %d 条", len(got))
	}
}

// 没有日志（从没写过 / 已过期 / 降级删了）必须能跟"已追平"区分开 ——
// 前者要让客户端退回 /messages，后者要接着等。
func TestRange_MissingLogIsDistinguishable(t *testing.T) {
	l := testLog(t)

	_, err := l.Range(context.Background(), uniqueSession(t), 0)
	if !errors.Is(err, ErrNoLog) {
		t.Errorf("不存在的日志应返 ErrNoLog，实际 %v —— 客户端会分不清该等还是该退回轮询", err)
	}
}

// Wait 要能等到新条目。这是"补齐之后接上直播"的那一步。
func TestWait_DeliversNewEntries(t *testing.T) {
	l := testLog(t)
	id := uniqueSession(t)
	t.Cleanup(func() { _ = l.rdb.Del(context.Background(), keyFor(id)).Err() })

	w := l.NewWriter(id)
	defer w.Close()
	w.Append(Entry{Seq: 1, Type: "start", Data: "<nil>"})

	go func() {
		time.Sleep(150 * time.Millisecond)
		w.Append(Entry{Seq: 2, Type: "delta", Data: "迟到的"})
	}()

	got, err := l.Wait(context.Background(), id, 1, 3*time.Second)
	if err != nil {
		t.Fatalf("Wait 失败: %v", err)
	}
	if len(got) != 1 || got[0].Data != "迟到的" {
		t.Fatalf("应等到 seq=2，实际 %+v", got)
	}
}

// 阻塞到期返回空切片而不是错误 —— 调用方据此发心跳再接着等。
// 返回错误的话那条流会被当成故障关掉，用户看到"流莫名断了"。
func TestWait_TimeoutIsNotAnError(t *testing.T) {
	l := testLog(t)
	id := uniqueSession(t)

	writeAll(t, l, id, Entry{Seq: 1, Type: "start", Data: "<nil>"})

	start := time.Now()
	got, err := l.Wait(context.Background(), id, 1, 300*time.Millisecond)
	if err != nil {
		t.Fatalf("阻塞到期不该报错，实际 %v", err)
	}
	if len(got) != 0 {
		t.Errorf("没有新条目应返回空，实际 %+v", got)
	}
	if elapsed := time.Since(start); elapsed < 250*time.Millisecond {
		t.Errorf("提前返回了（%s）—— 会变成忙轮询", elapsed)
	}
}

// ── 跨 pod 序号接续 ─────────────────────────────────────────
//
// 这是整个设计里最容易在本地测不出来的一条：序号是**进程内**的计数器，而会话会在
// pod 之间轮转。pod B 不 seed 的话它从 1 开始重数，XADD 因为 ID 不递增而全部失败，
// 表现是那一轮完全没有日志 —— 单副本下永远复现不了。
func TestLastSeq_LetsAnotherPodContinueNumbering(t *testing.T) {
	l := testLog(t)
	id := uniqueSession(t)

	// pod A 跑完第一轮
	writeAll(t, l, id,
		Entry{Seq: 1, Type: "start", Data: "<nil>"},
		Entry{Seq: 2, Type: "delta", Data: "第一轮"},
		Entry{Seq: 3, Type: "done", Data: "<nil>"},
	)

	last, err := l.LastSeq(context.Background(), id)
	if err != nil {
		t.Fatalf("LastSeq 失败: %v", err)
	}
	if last != 3 {
		t.Fatalf("LastSeq = %d, want 3 —— pod B 会从错误的位置接着数", last)
	}

	// pod B 从 last 接着写第二轮
	w := l.NewWriter(id)
	w.Append(Entry{Seq: last + 1, Type: "start", Data: "<nil>"})
	w.Append(Entry{Seq: last + 2, Type: "delta", Data: "第二轮"})
	w.Close()
	if w.Degraded() {
		t.Fatal("pod B 的写入降级了 —— 序号接续没生效")
	}

	got, err := l.Range(context.Background(), id, 3)
	if err != nil {
		t.Fatalf("Range 失败: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("第二轮应有 2 条，实际 %d", len(got))
	}
	if got[1].Data != "第二轮" {
		t.Errorf("第二轮内容 = %q", got[1].Data)
	}
}

func TestLastSeq_EmptyLogIsZero(t *testing.T) {
	l := testLog(t)
	last, err := l.LastSeq(context.Background(), uniqueSession(t))
	if err != nil {
		t.Fatalf("空日志不该报错: %v", err)
	}
	if last != 0 {
		t.Errorf("LastSeq = %d, want 0", last)
	}
}

// 序号倒退（没 seed 的 pod B）必须让写入降级，而不是静默丢事件。
//
// Redis 会拒绝 ID 不递增的 XADD，我们要保证这个错误被看见 —— 否则表现是
// "日志里莫名少了一整轮"，而日志本身看起来是好的。
func TestWriter_RewindingSeqDegrades(t *testing.T) {
	l := testLog(t)
	id := uniqueSession(t)

	writeAll(t, l, id,
		Entry{Seq: 1, Type: "start", Data: "<nil>"},
		Entry{Seq: 5, Type: "delta", Data: "跳到 5"},
	)

	// 模拟一个没 seed 的 pod：从 2 开始写，而流顶端已经是 5
	w := l.NewWriter(id)
	w.Append(Entry{Seq: 2, Type: "delta", Data: "倒退"})
	w.Close()

	if !w.Degraded() {
		t.Error("序号倒退没有触发降级 —— 会静默丢掉这一轮的日志")
	}
	// 降级会把整条流删掉，于是客户端拿到明确的"没有日志"而不是残缺的正文
	if _, err := l.Range(context.Background(), id, 0); !errors.Is(err, ErrNoLog) {
		t.Errorf("降级后应返 ErrNoLog（流已删），实际 %v —— 残缺的流会让客户端拿到缺字的正文", err)
	}
}

// ── 降级与容量 ──────────────────────────────────────────────

// Reset 清掉降级标记，让下一轮重新有机会写。
// 降级如果是永久的，一次 Redis 抖动会让这个会话余生都没有续传能力，且完全静默。
func TestWriter_ResetClearsDegradation(t *testing.T) {
	l := testLog(t)
	id := uniqueSession(t)
	t.Cleanup(func() { _ = l.rdb.Del(context.Background(), keyFor(id)).Err() })

	w := l.NewWriter(id)
	defer w.Close()
	w.degrade(errors.New("模拟故障"))
	if !w.Degraded() {
		t.Fatal("degrade 没置位")
	}

	w.Reset()
	if w.Degraded() {
		t.Error("Reset 之后仍是降级态 —— 这个会话此后永远没有续传能力")
	}
}

// 降级期间 Append 直接丢弃，不能反过来阻塞生成。
func TestWriter_DegradedAppendDoesNotBlock(t *testing.T) {
	l := testLog(t)
	id := uniqueSession(t)

	w := l.NewWriter(id)
	defer w.Close()
	w.degrade(errors.New("模拟故障"))

	done := make(chan struct{})
	go func() {
		defer close(done)
		for i := 0; i < writeBuffer*3; i++ {
			w.Append(Entry{Seq: uint64(i + 1), Type: "delta", Data: "x"})
		}
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("降级后 Append 阻塞了 —— 写日志绝不能拖住生成")
	}
}

// MAXLEN 裁剪：超出上限时**裁掉旧的**，保留最新。
//
// 方向错了的话长回答的结尾会被丢掉，而结尾恰好是用户最关心的部分。
// 注意用的是近似裁剪（~），所以只断言"没有无限增长"和"最新的还在"。
func TestWriter_TrimsOldestNotNewest(t *testing.T) {
	url := os.Getenv("TEST_REDIS_URL")
	if url == "" {
		t.Skip("TEST_REDIS_URL 未设置，跳过")
	}
	l, err := Open(url, 100, time.Minute) // 故意配小
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = l.rdb.Close() })

	id := uniqueSession(t)
	t.Cleanup(func() { _ = l.rdb.Del(context.Background(), keyFor(id)).Err() })

	w := l.NewWriter(id)
	const total = 1000
	for i := 1; i <= total; i++ {
		w.Append(Entry{Seq: uint64(i), Type: "delta", Data: fmt.Sprintf("tok%d", i)})
	}
	w.Close()
	if w.Degraded() {
		t.Fatal("写入降级了")
	}

	n, err := l.rdb.XLen(context.Background(), keyFor(id)).Result()
	if err != nil {
		t.Fatal(err)
	}
	if n >= total {
		t.Errorf("XLen = %d，MAXLEN 没生效 —— 单个会话会无上限吃内存", n)
	}

	// 最后一条必须还在
	got, err := l.Range(context.Background(), id, total-1)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].Data != fmt.Sprintf("tok%d", total) {
		t.Errorf("最新的条目被裁掉了：%+v —— 裁剪方向反了，回答的结尾会丢", got)
	}
}

// TTL 必须挂上，否则 Redis 会被跑过的会话慢慢填满 —— 而且是只增不减。
func TestWriter_SetsTTL(t *testing.T) {
	l := testLog(t)
	id := uniqueSession(t)

	writeAll(t, l, id, Entry{Seq: 1, Type: "start", Data: "<nil>"})

	ttl, err := l.rdb.TTL(context.Background(), keyFor(id)).Result()
	if err != nil {
		t.Fatal(err)
	}
	if ttl <= 0 {
		t.Fatalf("TTL = %s —— 没设过期，Redis 会被历史会话填满", ttl)
	}
	if ttl > 2*time.Minute {
		t.Errorf("TTL = %s，超过了配置值", ttl)
	}
}

// ── 未启用时的行为（不需要 Redis）────────────────────────────

func TestNilLog_WriterIsNil(t *testing.T) {
	var l *Log
	if w := l.NewWriter("x"); w != nil {
		t.Error("未启用时应返回 nil Writer")
	}
	// nil Writer 的方法必须全部安全 —— 调用点不判空
	var w *Writer
	w.Append(Entry{Seq: 1, Type: "delta", Data: "x"})
	w.Reset()
	w.Close()
	if w.Degraded() {
		t.Error("nil Writer 不该报告降级")
	}
}

// 读操作返 ErrNoLog：客户端退回轮询 /messages，也就是接这个包之前的行为。
func TestNilLog_ReadsReturnNoLog(t *testing.T) {
	var l *Log
	if l.Enabled() {
		t.Error("nil Log 不该报告已启用")
	}
	if _, err := l.Range(context.Background(), "x", 0); !errors.Is(err, ErrNoLog) {
		t.Errorf("Range = %v, want ErrNoLog", err)
	}
	if _, err := l.Wait(context.Background(), "x", 0, time.Second); !errors.Is(err, ErrNoLog) {
		t.Errorf("Wait = %v, want ErrNoLog", err)
	}
	// LastSeq 例外：返 0 而不是错误，让 seed 逻辑不用分叉
	if n, err := l.LastSeq(context.Background(), "x"); err != nil || n != 0 {
		t.Errorf("LastSeq = %d, %v; want 0, nil", n, err)
	}
}
