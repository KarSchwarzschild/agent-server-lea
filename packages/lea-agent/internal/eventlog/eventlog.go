// Package eventlog 把一个会话的 SSE 事件写进 Redis Stream，让断线的客户端能从
// 断点续传，而不是只能干等整轮结束。
//
// ── 为什么需要它 ────────────────────────────────────────────
//
// 主链路 POST /chat/stream 把"发消息"和"收事件流"合并成一条连接，代价是**没法重连**：
// 你不能重新发起一个 POST 去接上原来那条（那会真的再问一遍）。所以连接一断，这一轮
// 已经生成的内容就看不到了 —— turn 本身还在后台跑（on_disconnect=continue），答案
// 最终会落库，但用户在那几分钟里对着一个空白页面。
//
// 有了事件日志，重连走 GET /chat/stream/:id?from=<seq>：先把 seq 之后的补上，
// 再接上直播。而且因为日志在 Redis 里，**补哪一段跟连到哪个 pod 无关**。
//
// 顺带修掉一个老 bug：两段式链路要求"先连流再发消息"，因为 broadcast 对没有订阅者
// 的事件直接丢弃。现在 GET 默认从头补，晚连也不会丢开头。
//
// ── 序号为什么自己发，不用 Redis 生成的 ──────────────────────
//
// Redis 的 XADD 会返回它生成的条目 ID，但那要等一次网络往返才知道。如果 SSE 的
// id: 字段要用它，每个 token 都得**同步**等一次 Redis —— 生成速度就被 Redis 的
// 延迟绑住了，Redis 一抖动整条流跟着卡。
//
// 所以序号由会话自己的原子计数器发：广播那一刻就知道 id，SSE 写入零阻塞，XADD 在
// 后台 goroutine 里流水线批量提交。序号直接当条目 ID 用（`<seq>-0`），于是续传就是
// 一次 XRANGE，不用扫描过滤。
//
// 代价是要保证序号严格递增。跨 pod 也得成立 —— pod A 跑完第一轮、pod B 接着跑第二轮，
// B 的计数器必须从 A 停下的地方接着数，否则 XADD 会因为 ID 变小而失败。所以每轮开始
// 时 seed 一次（见 LastSeq）。这是安全的，因为**执行权锁保证同一时刻只有一个 writer**。
//
// ── 降级 ────────────────────────────────────────────────────
//
// 写不动了（Redis 挂了、缓冲满了）**绝不能阻塞生成**。这时直接把整条流删掉：
// 流不存在 = "没有日志可用" = 客户端退回 /sessions/:id/messages。
//
// 删掉而不是留个残缺的流，是因为残缺的流会让客户端拿到**看起来完整但中间少了一段**
// 的正文 —— 那比拿不到更糟，因为它不知道自己少了东西。
package eventlog

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

const (
	// defaultMaxLen 是每条流保留的条目数上限。
	//
	// 逐 token 写的话一条 = 一个 token，所以这个数字直接就是"能回放多少个 token"。
	// 10000 覆盖得住很长的回答（正常回答几百到两千 token）。按每条约 100 字节估，
	// 单会话上限约 1 MB。
	//
	// 配小了的后果是静默的：长回答的开头被裁掉，续传时客户端拿到的正文缺了前半段。
	defaultMaxLen = 10000

	// defaultTTL 是流的存活时间。5 分钟对齐 OpenAI 的 background response 重放窗口。
	//
	// 覆盖：网络抖动、切 WiFi、地铁隧道、手机切后台。不覆盖：关了标签页半小时后回来 ——
	// 那时 turn 早就结束了，走 /messages 拉最终结果本来就是对的路。
	defaultTTL = 5 * time.Minute

	// writeBuffer 是异步写入的排队深度。按每秒 50 个事件估，1024 条约等于 20 秒的
	// 缓冲余量 —— 正常情况下永远填不满，填满就说明 Redis 真的不行了。
	writeBuffer = 1024

	// maxBatch 是一次 pipeline 提交的条目上限。攒批是这个设计省掉"每 token 一次
	// 网络往返"的地方：负载越高攒得越多，单条 XADD 的网络成本被自然摊薄。
	maxBatch = 128

	// appendBlockTimeout 是缓冲满时愿意等的时间。等超了就降级 ——
	// 让用户的回答为了写日志而卡住，是本末倒置。
	appendBlockTimeout = 250 * time.Millisecond
)

// ErrNoLog 表示这个会话没有可用的事件日志（从没写过、已过期、或写的时候降级删了）。
// 调用方应当据此让客户端退回 /sessions/:id/messages。
var ErrNoLog = errors.New("no event log for this session")

func keyFor(sessionID string) string { return "lea:agent:events:" + sessionID }

// entryID 把序号渲染成 Redis Stream 的条目 ID。
//
// 用 `<seq>-0` 而不是让 Redis 自己生成：这样续传的起点可以直接算出来
// （`<from+1>-0`），一次 XRANGE 就拿到该补的那段，不用把整条流拉回来过滤。
//
// Redis 只要求 ID 严格递增，不要求第一段真的是毫秒时间戳，所以这样用是合法的。
func entryID(seq uint64) string { return strconv.FormatUint(seq, 10) + "-0" }

// Entry 是日志里的一条事件。
type Entry struct {
	Seq uint64
	// Type 是 SSE 的 event 字段（delta / tool_event / done ...）。
	Type string
	// Data 是**已经渲染好**的 SSE data 字段内容。
	//
	// 存渲染后的字符串而不是原始对象，是为了让重放跟直播逐字节一致：渲染规则藏在
	// gin 的 sse 包里（map 走 json.Encoder、其余走 fmt.Sprint），在这里重新实现一遍
	// 就等于把那套规则复制了一份，哪天 gin 改了行为，直播和重放会悄悄分叉。
	Data string
}

// Log 是事件日志的入口。nil 合法 —— 未配 Redis 时所有写操作退化成 no-op，
// 读操作返回 ErrNoLog（客户端退回轮询，也就是接这个包之前的行为）。
type Log struct {
	rdb    *redis.Client
	maxLen int64
	ttl    time.Duration
}

func Open(url string, maxLen int64, ttl time.Duration) (*Log, error) {
	opt, err := redis.ParseURL(url)
	if err != nil {
		return nil, fmt.Errorf("parse REDIS_URL: %w", err)
	}
	rdb := redis.NewClient(opt)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := rdb.Ping(ctx).Err(); err != nil {
		_ = rdb.Close()
		return nil, fmt.Errorf("ping redis: %w", err)
	}
	return &Log{rdb: rdb, maxLen: maxLen, ttl: ttl}, nil
}

var (
	sharedOnce sync.Once
	sharedInst *Log
)

// Shared 返回进程级单例。没配 REDIS_URL 或连不上时返回 nil。
func Shared() *Log {
	sharedOnce.Do(func() {
		url := os.Getenv("REDIS_URL")
		if url == "" {
			log.Printf("event_log disabled (REDIS_URL 未配置) —— 断线重连将退回轮询 /messages")
			return
		}
		l, err := Open(url, int64(intFromEnv("LEA_AGENT_EVENTLOG_MAXLEN", defaultMaxLen)),
			durationFromEnv("LEA_AGENT_EVENTLOG_TTL", defaultTTL))
		if err != nil {
			log.Printf("event_log disabled: %v (断线重连将退回轮询 /messages)", err)
			return
		}
		sharedInst = l
		log.Printf("event_log enabled maxlen=%d ttl=%s", l.maxLen, l.ttl)
	})
	return sharedInst
}

// Enabled 报告日志是否可用，供路由层决定走续传还是走内存订阅。
func (l *Log) Enabled() bool { return l != nil && l.rdb != nil }

// LastSeq 返回这条流里最大的序号，没有流时返回 0。
//
// 每轮开始时用它 seed 计数器 —— pod A 跑完第一轮、pod B 接着跑第二轮时，B 必须
// 从 A 停下的地方接着数，否则 XADD 会因为 ID 不递增而全部失败（表现是这一轮完全
// 没有日志，而且只在多副本轮转时才出现）。
//
// 这里不需要额外的互斥：执行权锁已经保证了同一时刻只有一个 pod 在写这个会话。
func (l *Log) LastSeq(ctx context.Context, sessionID string) (uint64, error) {
	if !l.Enabled() {
		return 0, nil
	}
	msgs, err := l.rdb.XRevRangeN(ctx, keyFor(sessionID), "+", "-", 1).Result()
	if err != nil {
		return 0, fmt.Errorf("read last seq: %w", err)
	}
	if len(msgs) == 0 {
		return 0, nil
	}
	return parseSeq(msgs[0]), nil
}

// Range 取序号大于 after 的所有条目。after=0 就是从头取。
//
// 起点用 `<after+1>-0` 这个**闭区间**下界，而不是 Redis 6.2 才有的开区间语法
// （`(<id>`）—— 少一个对服务端版本的隐式要求，而这种要求不满足时的报错很难联想到
// 是版本问题。
func (l *Log) Range(ctx context.Context, sessionID string, after uint64) ([]Entry, error) {
	if !l.Enabled() {
		return nil, ErrNoLog
	}
	msgs, err := l.rdb.XRange(ctx, keyFor(sessionID), entryID(after+1), "+").Result()
	if err != nil {
		return nil, fmt.Errorf("range event log: %w", err)
	}
	if len(msgs) == 0 {
		// 空可能是"这个会话没有日志"，也可能是"已经全读完了、正等新的"。
		// 用 EXISTS 区分：前者要让客户端退回轮询，后者要接着等。
		n, err := l.rdb.Exists(ctx, keyFor(sessionID)).Result()
		if err != nil {
			return nil, fmt.Errorf("check event log: %w", err)
		}
		if n == 0 {
			return nil, ErrNoLog
		}
	}
	return toEntries(msgs), nil
}

// Wait 阻塞等序号大于 after 的新条目，直到有数据、超时或 ctx 取消。
// 超时返回空切片而不是错误 —— 调用方据此发一次心跳再接着等。
func (l *Log) Wait(ctx context.Context, sessionID string, after uint64, block time.Duration) ([]Entry, error) {
	if !l.Enabled() {
		return nil, ErrNoLog
	}
	res, err := l.rdb.XRead(ctx, &redis.XReadArgs{
		Streams: []string{keyFor(sessionID), entryID(after)},
		Block:   block,
	}).Result()
	if err != nil {
		if errors.Is(err, redis.Nil) {
			return nil, nil // 阻塞到期，没有新条目
		}
		if ctx.Err() != nil {
			return nil, ctx.Err() // 客户端断开
		}
		return nil, fmt.Errorf("wait event log: %w", err)
	}
	var out []Entry
	for _, s := range res {
		out = append(out, toEntries(s.Messages)...)
	}
	return out, nil
}

func toEntries(msgs []redis.XMessage) []Entry {
	out := make([]Entry, 0, len(msgs))
	for _, m := range msgs {
		out = append(out, Entry{
			Seq:  parseSeq(m),
			Type: stringField(m, "type"),
			Data: stringField(m, "data"),
		})
	}
	return out
}

// parseSeq 从条目里取序号。优先用显式字段，取不到再退回解析条目 ID 的前半段
// （两者是同一个值，字段只是为了让 XRANGE 出来的东西自己能看懂）。
func parseSeq(m redis.XMessage) uint64 {
	if v := stringField(m, "seq"); v != "" {
		if n, err := strconv.ParseUint(v, 10, 64); err == nil {
			return n
		}
	}
	id := m.ID
	for i := 0; i < len(id); i++ {
		if id[i] == '-' {
			n, _ := strconv.ParseUint(id[:i], 10, 64)
			return n
		}
	}
	return 0
}

func stringField(m redis.XMessage, key string) string {
	v, ok := m.Values[key]
	if !ok {
		return ""
	}
	s, _ := v.(string)
	return s
}

// ── 写入 ────────────────────────────────────────────────────

// Writer 是一个会话的异步写入器。Append 永不阻塞生成。
type Writer struct {
	log *Log
	key string
	ch  chan Entry

	degraded  atomic.Bool
	done      chan struct{}
	closeOnce sync.Once
}

// NewWriter 起一个写入器。日志未启用时返回 nil —— nil Writer 的所有方法都是 no-op，
// 调用点不需要判空。
func (l *Log) NewWriter(sessionID string) *Writer {
	if !l.Enabled() {
		return nil
	}
	w := &Writer{
		log:  l,
		key:  keyFor(sessionID),
		ch:   make(chan Entry, writeBuffer),
		done: make(chan struct{}),
	}
	go w.run()
	return w
}

// Reset 在新一轮开始时清掉降级标记。
//
// 降级如果是永久的，一次 Redis 抖动就会让这个会话**余生**都没有续传能力，而且
// 完全静默。按轮重置意味着影响范围最多是一轮。
func (w *Writer) Reset() {
	if w == nil {
		return
	}
	w.degraded.Store(false)
}

// Degraded 报告本轮的日志是否已经不完整。
func (w *Writer) Degraded() bool { return w != nil && w.degraded.Load() }

// Append 把一条事件排进队列。永不阻塞超过 appendBlockTimeout。
func (w *Writer) Append(e Entry) {
	if w == nil || w.degraded.Load() {
		return
	}
	select {
	case w.ch <- e:
		return
	default:
	}
	// 缓冲满 = Redis 跟不上生成速度。短暂等一下给它机会，再不行就降级。
	t := time.NewTimer(appendBlockTimeout)
	defer t.Stop()
	select {
	case w.ch <- e:
	case <-t.C:
		w.degrade(errors.New("写入缓冲已满，Redis 跟不上"))
	}
}

// run 消费队列并攒批提交。
func (w *Writer) run() {
	defer close(w.done)
	batch := make([]Entry, 0, maxBatch)
	for {
		e, ok := <-w.ch
		if !ok {
			return
		}
		batch = append(batch[:0], e)
		// 把此刻已经排上队的一并取走 —— 一次网络往返提交多条，这是"逐 token 写"
		// 能承受的原因：条目仍然是一个 token 一条，但往返次数按负载自动摊薄。
		draining := true
		for draining && len(batch) < maxBatch {
			select {
			case e2, ok := <-w.ch:
				if !ok {
					w.flush(batch)
					return
				}
				batch = append(batch, e2)
			default:
				draining = false
			}
		}
		w.flush(batch)
	}
}

func (w *Writer) flush(batch []Entry) {
	if len(batch) == 0 || w.degraded.Load() {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	pipe := w.log.rdb.Pipeline()
	for _, e := range batch {
		pipe.XAdd(ctx, &redis.XAddArgs{
			Stream: w.key,
			ID:     entryID(e.Seq),
			MaxLen: w.log.maxLen,
			Approx: true, // ~ 裁剪，Redis 按整节点裁，比精确裁剪便宜得多
			Values: map[string]any{"seq": e.Seq, "type": e.Type, "data": e.Data},
		})
	}
	// 每批都续一次 TTL：流的寿命应该从**最后一次写**算起，而不是从创建算起 ——
	// 否则一轮跑了六分钟的 turn，它的日志在还在写的时候就过期了。
	pipe.Expire(ctx, w.key, w.log.ttl)

	if _, err := pipe.Exec(ctx); err != nil {
		w.degrade(err)
	}
}

// degrade 放弃本轮的日志，并把已经写进去的部分删掉。
//
// 为什么是删而不是留着：留下来的是一条**中间缺了一段**的流，客户端续传时会拿到
// 看起来连续、实际少了一截的正文，而且没有任何迹象表明少了东西。删掉之后
// 客户端得到明确的"没有日志"，退回 /messages 拿完整结果 —— 慢一点，但是对的。
func (w *Writer) degrade(cause error) {
	if !w.degraded.CompareAndSwap(false, true) {
		return // 已经降级过了，别刷屏
	}
	log.Printf("event_log_degraded key=%s err=%v —— 本轮日志作废，客户端断线后将退回 /messages", w.key, cause)

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := w.log.rdb.Del(ctx, w.key).Err(); err != nil {
		// 删不掉就只能留着一条残缺的流。这是唯一会让客户端拿到不完整正文的路径，
		// 所以要喊得响一点。
		log.Printf("event_log_cleanup_failed key=%s err=%v —— 残缺的流仍在，续传可能返回不完整正文", w.key, err)
	}
}

// Close 停掉写入器。不等队列排空 —— 会话关闭时正在排队的事件已经无关紧要了
// （那一轮要么结束了、要么被掐了）。
func (w *Writer) Close() {
	if w == nil {
		return
	}
	w.closeOnce.Do(func() { close(w.ch) })
	<-w.done
}

func intFromEnv(key string, def int) int {
	v := os.Getenv(key)
	if v == "" {
		return def
	}
	n, err := strconv.Atoi(v)
	if err != nil || n <= 0 {
		log.Printf("%s=%q 解析失败，用默认值 %d", key, v, def)
		return def
	}
	return n
}

func durationFromEnv(key string, def time.Duration) time.Duration {
	v := os.Getenv(key)
	if v == "" {
		return def
	}
	d, err := time.ParseDuration(v)
	if err != nil || d <= 0 {
		log.Printf("%s=%q 解析失败，用默认值 %s", key, v, def)
		return def
	}
	return d
}
