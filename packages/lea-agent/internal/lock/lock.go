// Package lock 提供"每会话一把"的分布式互斥锁，保证同一时刻全集群只有一个
// pod 在跑某个会话的 turn。
//
// 为什么需要它：会话对象和它的 SSE 订阅者都活在**某一个进程的堆上**，所以正常
// 部署必须让同一 session 的请求粘到同一 pod（否则 POST /send 在 A 干活、
// GET /stream 在 B 订阅，用户一个字都收不到）。粘性路由管住了常态，但发版、
// OOM、节点排水、扩缩容都会让某个会话的"家"从 A 挪到 B —— 而 A 可能还在跑这
// 一轮。那个窗口里两个 pod 会同时 RunTurn、同时写 agent_messages，写出来的历史
// 是两个写者交错的产物，表现为"模型忘了刚说过的话"。
//
// 这把锁就是那个窗口的兜底。它不替代粘性路由，是粘性路由漏下来的那部分的安全网。
//
// 没配 REDIS_URL 时整个包退化成 no-op —— 单实例部署（今天的现状）不需要锁，
// 也不该因为少个中间件就起不来。降级是显式的：Shared() 返回 nil，nil 接收者的
// Acquire 永远成功，于是调用点不用分叉。
package lock

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"os"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

// ErrHeld 表示这个会话正被别的 pod（或本 pod 的上一轮）占着。
// 调用方应当映射成 409，而不是 500 —— 这是可预期的并发，不是故障。
var ErrHeld = errors.New("session is locked by another runner")

// defaultTTL 是锁的存活时间。选 60s 是因为它要同时满足两头：
//   - 够长：一轮 turn 可能跑几分钟（引擎实测 20+ 轮），心跳必须有充足余量续上；
//   - 够短：pod 被 SIGKILL 时没人来解锁，只能等它自己过期，用户在这段时间里
//     发消息会一直吃 409。60s 是"用户能忍"和"心跳不会误判"之间的折中。
const defaultTTL = 60 * time.Second

// keyFor 是锁键的唯一构造处。抽出来是因为 Acquire 和 IsHeld 都要拼它，而拼错的
// 后果完全静默 —— IsHeld 永远返 false，跨 pod 的停止请求一条也发不出去，日志里
// 看不出任何异常。
func keyFor(sessionID string) string { return "lea:agent:turn:" + sessionID }

// releaseScript 只删自己那把锁。
//
// 不能直接 DEL：如果本 pod 卡顿导致锁已经过期、并且被 B 抢走了，这时本 pod
// 收尾时的裸 DEL 会把 B 正在用的锁删掉，于是 C 又能进来 —— 一次卡顿变成互斥
// 彻底失效。比对 value 再删是 Redis 分布式锁的标准做法。
var releaseScript = redis.NewScript(`
if redis.call("GET", KEYS[1]) == ARGV[1] then
	return redis.call("DEL", KEYS[1])
end
return 0
`)

// renewScript 同理：只续自己那把。锁已经易主时续期必须失败，
// 否则本 pod 会不断延长别人的锁。
var renewScript = redis.NewScript(`
if redis.call("GET", KEYS[1]) == ARGV[1] then
	return redis.call("PEXPIRE", KEYS[1], ARGV[2])
end
return 0
`)

// Locker 持有 Redis 连接。零值不可用，用 Shared() 取进程级单例、或 Open 自建。
type Locker struct {
	rdb *redis.Client
	ttl time.Duration
}

// renewEvery 是心跳周期：TTL 的三分之一。连丢两次续期才会真的过期，单次 Redis
// 抖动不至于让一轮跑到一半的 turn 把锁丢了。
//
// 按 ttl 推导而不是写成常量，是为了让测试能把 TTL 调到几百毫秒来验证续期和过期，
// 否则测一次心跳得等 20 秒。
func (l *Locker) renewEvery() time.Duration { return l.ttl / 3 }

// Open 连上 Redis 并返回一个 Locker。连不上返回错误 —— 由调用方决定是拒绝启动
// 还是降级；Shared 选的是降级。
func Open(url string, ttl time.Duration) (*Locker, error) {
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
	return &Locker{rdb: rdb, ttl: ttl}, nil
}

var (
	sharedOnce sync.Once
	sharedInst *Locker
)

// Shared 返回进程级单例。没配 REDIS_URL 或连不上时返回 nil —— nil 是合法的
// Locker，所有方法都在 nil 上安全退化成 no-op，调用点不需要判空。
func Shared() *Locker {
	sharedOnce.Do(func() {
		url := os.Getenv("REDIS_URL")
		if url == "" {
			log.Printf("session_lock disabled (REDIS_URL 未配置) —— 单实例部署下无需互斥")
			return
		}
		l, err := Open(url, defaultTTL)
		if err != nil {
			// 起不来就退化成 no-op，而不是让整个服务起不来。单实例下锁本来就是
			// 多余的；多副本下这是真问题，所以照常打日志而不是静默。
			log.Printf("session_lock disabled: %v (多副本部署下会失去互斥保护)", err)
			return
		}
		sharedInst = l
		log.Printf("session_lock enabled ttl=%s renew=%s", l.ttl, l.renewEvery())
	})
	return sharedInst
}

// Handle 是一次成功加锁的凭证。必须 Release，否则锁要等 TTL 自然过期。
// 零值 Handle 是 no-op 句柄（未启用锁时返回的就是它），Release 什么也不做。
type Handle struct {
	l     *Locker
	key   string
	owner string

	stopOnce sync.Once
	stop     chan struct{} // 关闭它让心跳 goroutine 退出
	done     chan struct{} // 心跳退出后关闭，Release 等它，避免续期与解锁交错
}

// Acquire 抢 sessionID 这把锁。抢到返回 Handle；已被占用返回 ErrHeld；
// Redis 出错返回原始错误（调用方自行决定放行还是拒绝）。
//
// nil 接收者永远成功并返回 no-op 句柄 —— 未启用锁时调用点无需分叉。
func (l *Locker) Acquire(ctx context.Context, sessionID string) (*Handle, error) {
	if l == nil || l.rdb == nil {
		return &Handle{}, nil
	}
	owner, err := newOwner()
	if err != nil {
		return nil, err
	}
	key := keyFor(sessionID)

	// SET NX PX：不存在才写，同时带上过期时间。两步（SETNX + EXPIRE）做不得 ——
	// 中间宕机就留下一把永不过期的锁，这个会话从此再也发不出消息。
	ok, err := l.rdb.SetNX(ctx, key, owner, l.ttl).Result()
	if err != nil {
		return nil, fmt.Errorf("acquire session lock: %w", err)
	}
	if !ok {
		return nil, ErrHeld
	}

	h := &Handle{
		l:     l,
		key:   key,
		owner: owner,
		stop:  make(chan struct{}),
		done:  make(chan struct{}),
	}
	go h.heartbeat()
	return h, nil
}

// IsHeld 回答"全集群此刻有没有 pod 在跑这个会话的 turn"。
//
// 这把锁顺带就是一张路由表：它的存在与否是"有没有 turn 在跑"的唯一权威判据，而
// 单个 pod 的内存只知道自己的那部分。跨 pod 的停止请求要靠它区分两种情况 ——
// "这一轮在别的 pod 上跑着，把信号转过去" 和 "全集群都没在跑，如实返 404"。
// 没有它就只能乐观地假装转发成功，把"停止按钮没反应"变成静默故障。
//
// nil 接收者返回 false：单实例部署下本 pod 的内存就是全部真相，查不到就是真没有。
//
// 注意这是一次**时点**查询，返回之后 turn 随时可能自己结束。调用方不能把它当
// 保证用，只能当"值不值得把信号发出去"的依据 —— 判断错的代价是发了一条没人
// 理的消息，而不是错误的状态。
func (l *Locker) IsHeld(ctx context.Context, sessionID string) (bool, error) {
	if l == nil || l.rdb == nil {
		return false, nil
	}
	n, err := l.rdb.Exists(ctx, keyFor(sessionID)).Result()
	if err != nil {
		return false, fmt.Errorf("check session lock: %w", err)
	}
	return n > 0, nil
}

// heartbeat 在持锁期间周期性续期，让 TTL 只在**持锁进程真的死了**的时候到期。
// 没有它，TTL 就得配得比最长的一轮还长，而那样 pod 崩掉之后锁要闲置那么久。
func (h *Handle) heartbeat() {
	defer close(h.done)
	t := time.NewTicker(h.l.renewEvery())
	defer t.Stop()
	for {
		select {
		case <-h.stop:
			return
		case <-t.C:
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			n, err := renewScript.Run(ctx, h.l.rdb, []string{h.key}, h.owner, h.l.ttl.Milliseconds()).Int()
			cancel()
			switch {
			case err != nil:
				// 网络抖动，下个周期再试。TTL 留了三次机会。
				log.Printf("session_lock renew_failed key=%s err=%v", h.key, err)
			case n == 0:
				// 锁已经不是我们的了（过期后被别人抢走）。继续跑这一轮是危险的 ——
				// 另一个 pod 此刻正在写同一段历史。喊出来，让日志里能查到。
				log.Printf("session_lock LOST key=%s owner=%s —— 锁已易主，这一轮可能与另一个 runner 交错", h.key, h.owner)
				return
			}
		}
	}
}

// Release 解锁。可重复调用；nil 或零值句柄上是 no-op，所以 defer h.Release()
// 不必先判空。
func (h *Handle) Release() {
	if h == nil || h.l == nil {
		return
	}
	h.stopOnce.Do(func() { close(h.stop) })
	<-h.done // 等心跳退干净，免得续期和解锁交错

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := releaseScript.Run(ctx, h.l.rdb, []string{h.key}, h.owner).Err(); err != nil {
		// 解锁失败不影响正确性 —— TTL 到了自然会放。只是这个会话在那之前发不了消息。
		log.Printf("session_lock release_failed key=%s err=%v (将等待 TTL 过期)", h.key, err)
	}
}

// newOwner 生成一次加锁的唯一标识：主机名+pid 便于在日志里定位是哪个 pod 占着，
// 随机后缀保证同一进程先后两次加锁不会互相误认（否则前一次的心跳可能续上后一次的锁）。
func newOwner() (string, error) {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", fmt.Errorf("generate lock owner: %w", err)
	}
	host, _ := os.Hostname()
	if host == "" {
		host = "unknown"
	}
	return fmt.Sprintf("%s/%d/%s", host, os.Getpid(), hex.EncodeToString(b[:])), nil
}
