package metrics

// Temporal 指标适配器。
//
// 盯的是三件写错了之后**本地完全看不出来**的事：
//   1. 标签值跟标签名的对应关系（map 遍历顺序随机，不排序就会错位）
//   2. 同一个指标名带不同标签键集合时不能 panic
//   3. WithTags 不能每次都去注册新向量（注册风暴 + 撞名 panic）

import (
	"strings"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus/testutil"
	"github.com/prometheus/common/expfmt"
)

func TestTagsMapToCorrectLabelPositions(t *testing.T) {
	h := NewTemporalHandler()
	// 刻意用"字母序跟直觉相反"的一组：activity_type 排在 task_queue 前面。
	tagged := h.WithTags(map[string]string{
		"task_queue":    "lea-agent-runs",
		"activity_type": "Execute",
	})
	tagged.Counter("test_tag_positions").Inc(1)

	vec := h.store.counters[cacheKey("test_tag_positions", []string{"activity_type", "task_queue"})]
	if vec == nil {
		t.Fatal("向量没按排序后的标签键缓存")
	}
	got := testutil.ToFloat64(vec.WithLabelValues("Execute", "lea-agent-runs"))
	if got != 1 {
		t.Fatalf("按 (activity_type=Execute, task_queue=lea-agent-runs) 取到 %v，"+
			"说明标签值和标签名错位了", got)
	}
}

func TestSameMetricWithDifferentTagKeysDoesNotPanic(t *testing.T) {
	h := NewTemporalHandler()
	// SDK 真的会这样：同一个指标在不同调用点带的标签键集合不一样。
	h.WithTags(map[string]string{"poller_type": "activity_task"}).
		Counter("test_varying_keys").Inc(1)
	h.WithTags(map[string]string{"poller_type": "workflow_task", "task_queue": "q"}).
		Counter("test_varying_keys").Inc(1)

	if len(h.store.counters) != 2 {
		t.Fatalf("缓存里有 %d 个向量，应该是 2（标签键集合不同要分开存）", len(h.store.counters))
	}
}

func TestWithTagsReusesCachedVectors(t *testing.T) {
	h := NewTemporalHandler()
	for i := 0; i < 100; i++ {
		h.WithTags(map[string]string{"task_queue": "q", "workflow_type": "AgentRun"}).
			Counter("test_reuse").Inc(1)
	}
	if len(h.store.counters) != 1 {
		t.Fatalf("100 次 WithTags 建了 %d 个向量，应该只有 1 个 —— "+
			"每次都注册会是注册风暴", len(h.store.counters))
	}
	vec := h.store.counters[cacheKey("test_reuse", []string{"task_queue", "workflow_type"})]
	if got := testutil.ToFloat64(vec.WithLabelValues("q", "AgentRun")); got != 100 {
		t.Fatalf("计数=%v，应该是 100", got)
	}
}

func TestTagsInheritAndOverride(t *testing.T) {
	h := NewTemporalHandler()
	base := h.WithTags(map[string]string{"namespace": "default", "task_queue": "old"})
	// 同名键要被新值覆盖，其他键保留 —— 这是接口约定。
	derived := base.WithTags(map[string]string{"task_queue": "new"})
	derived.Counter("test_override").Inc(1)

	vec := h.store.counters[cacheKey("test_override", []string{"namespace", "task_queue"})]
	if vec == nil {
		t.Fatal("标签键集合不对")
	}
	if got := testutil.ToFloat64(vec.WithLabelValues("default", "new")); got != 1 {
		t.Fatalf("继承/覆盖不对，按 (default, new) 取到 %v", got)
	}
}

// Timer 要以秒为单位记录，跟 lea_* 那批指标和 Prometheus 的约定一致。
func TestTimerRecordsSeconds(t *testing.T) {
	h := NewTemporalHandler()
	h.WithTags(map[string]string{"task_queue": "q"}).
		Timer("test_timer_seconds").Record(1500 * time.Millisecond)

	vec := h.store.histograms[cacheKey("test_timer_seconds", []string{"task_queue"})]
	if vec == nil {
		t.Fatal("histogram 没建出来")
	}
	exposed, err := testutil.CollectAndFormat(vec, expfmt.TypeTextPlain, "test_timer_seconds")
	if err != nil {
		t.Fatal(err)
	}
	text := string(exposed)
	if len(text) == 0 {
		t.Fatal("指标文本为空，断言会假通过")
	}
	// 1.5s 应该落在 2.5 的桶里而不是 1 的桶里；记成毫秒的话 sum 会是 1500。
	if !strings.Contains(text, `test_timer_seconds_sum{task_queue="q"} 1.5`) {
		t.Fatalf("sum 不是 1.5 秒，可能记成了毫秒或纳秒：\n%s", text)
	}
}

// 桶上限要够高。worker 这边 activity 是 LLM 调用，几十秒很正常；
// 按 HTTP 那套（上限 10s）会把所有慢的情况挤进 +Inf，分位数就没意义了。
func TestHistogramBucketsCoverMinuteScale(t *testing.T) {
	h := NewTemporalHandler()
	h.WithTags(map[string]string{"task_queue": "q"}).
		Timer("test_buckets").Record(90 * time.Second)

	vec := h.store.histograms[cacheKey("test_buckets", []string{"task_queue"})]
	exposed, err := testutil.CollectAndFormat(vec, expfmt.TypeTextPlain, "test_buckets")
	if err != nil {
		t.Fatal(err)
	}
	text := string(exposed)
	// 90s 要被某个有限桶装下，不能只有 +Inf 收到它。
	if !strings.Contains(text, `le="120"`) {
		t.Fatalf("没有 120s 的桶，分钟级延迟会全挤在 +Inf：\n%s", text)
	}
	if !strings.Contains(text, `test_buckets_bucket{task_queue="q",le="120"} 1`) {
		t.Fatalf("90s 没落进 le=120 的桶：\n%s", text)
	}
}

// 两个 handler 实例注册同名指标时，第二个要复用第一个已注册的 collector，
// 不能各记一份。
//
// 这是真实场景：cmd/server 和 cmd/worker 是同一个二进制，测试里也会反复
// NewTemporalHandler()。各记一份的话 /metrics 里只能看到第一个的数，第二个
// 的全丢，而且完全静默。
//
// ⚠️ 必须让 Help 一致才会走到 AlreadyRegisteredError 分支 —— Prometheus 只在
// 名字**和 Help** 都相同时才认为是"重复注册"，Help 不同是另一种错误。写这条
// 测试时我先用空 Help 试，于是掉进了那个分支，发现它当时会静默返回一个游离
// 对象（指标永远不出数）。那个分支现在会打日志。
func TestDuplicateRegistrationReusesExistingCollector(t *testing.T) {
	name := "test_duplicate_registration"

	h1 := NewTemporalHandler()
	h1.WithTags(map[string]string{"task_queue": "q"}).Counter(name).Inc(2)

	// 另一个 handler 实例：缓存是独立的，所以会再次尝试注册同名指标
	h2 := NewTemporalHandler()
	h2.WithTags(map[string]string{"task_queue": "q"}).Counter(name).Inc(3)

	// 两次都该落在同一个 collector 上
	vec1 := h1.store.counters[cacheKey(name, []string{"task_queue"})]
	vec2 := h2.store.counters[cacheKey(name, []string{"task_queue"})]
	if vec1 != vec2 {
		t.Fatal("两个 handler 各持有一份 collector —— 第二个没复用 ExistingCollector，" +
			"它记的数不会出现在 /metrics 里")
	}
	if got := testutil.ToFloat64(vec1.WithLabelValues("q")); got != 5 {
		t.Fatalf("合计=%v，应该是 2+3=5", got)
	}
}
