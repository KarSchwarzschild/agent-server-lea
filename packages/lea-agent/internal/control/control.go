// Package control 是 pod 之间的控制面信道：一个 pod 想让**另一个** pod 对某个
// 会话做点什么时，消息走这里。
//
// 目前只有一件事：停掉正在跑的 turn。
//
// 为什么需要它：掐断 turn 靠的是 context.CancelFunc，那是个函数指针，活在跑这轮
// 的那个进程的堆上。POST /chat/stop/:id 是一条**独立的 HTTP 请求**，多副本下会
// 落到任意副本，而只有其中一个副本手里有那个 cancel。所以"停止"在多副本下是抛
// 硬币：命中就停，没命中返 404 而 turn 继续烧 token。
//
// 这不是靠粘性路由能解决的那一类问题 —— 就算路由粘住了，stop 和 stream 也是两条
// 不同的请求，而 session_id 在 stop 这条的 **URL 里**、在 stream 那条的 **body 里**，
// 想让它们哈希到同一个后端得让 nginx 读请求体，做不到。
//
// ── 形状选择 ────────────────────────────────────────────────
//
// 用**一个全局频道**而不是每会话一个频道（`lea:agent:stop:<sid>`）：
//
//   - 全局：每个 pod 收到每一条停止请求，本地 map 里查不到就丢掉。多余的投递量是
//     "人点停止按钮的频率 × 副本数"，也就是每秒个位数。订阅只建一次。
//   - 每会话：投递精确，但每开一轮 turn 都要 SUBSCRIBE、收尾要 UNSUBSCRIBE，多
//     一批要维护的状态和一批新的失败模式（订阅漏了 = 这个会话停不掉，且静默）。
//
// 在这个量级上精确投递买不到任何东西。真要换的临界点是"停止请求的 QPS × 副本数"
// 到了让 Redis 出口带宽或各 pod 的解析开销可观的程度 —— 离得很远。
//
// ── 一个必须知道的限制 ──────────────────────────────────────
//
// **Redis pub/sub 没有投递保证。** 消息只发给"此刻正连着的订阅者"：目标 pod 的
// 订阅连接刚好在重连窗口里，这条消息就没了，没有重试、没有堆积、没人报错。
// go-redis 会自动重连，但断开期间发出的消息不会补投。
//
// 对"停止"来说这个代价可以接受 —— 用户会再点一次，而且 SSE 流仍然在正常流字，
// 用户看得见没停下来。要做到至少一次投递得换 Redis Stream（消费者组 + ACK），
// 那是事件日志那件事顺带能拿到的东西，不值得为 stop 单独引入。
package control

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

// stopChannel 是全局停止频道。带 lea:agent: 前缀是为了跟同一个 Redis 实例上
// 别的用途（锁键、将来的事件流）分开，运维在 MONITOR 里能一眼看出是谁。
const stopChannel = "lea:agent:control:stop"

// ErrUnavailable 表示控制面不可用（没配 REDIS_URL，或连不上）。
//
// 注意这里**故意不沿用 internal/lock 那套"nil 接收者退化成成功"的写法**。锁那边
// nil 当成功是对的：单实例不需要互斥，放行即正确。这里 nil 当成功是错的 ——
// 消息发进了空气，调用方却以为远端会照办，于是它会去等一个永远不会发生的让位。
// 把"送不出去"如实报出来，调用方才能立刻拒绝而不是白等五秒。
var ErrUnavailable = fmt.Errorf("control bus unavailable")

// stopMessage 是频道里的载荷。
//
// 用 JSON 而不是裸 session_id：多一个 origin 字段，出问题时日志里能对上是哪个
// pod 发起的。一条消息大概 60 字节，这个量级上可读性比字节数值钱。
type stopMessage struct {
	SessionID string `json:"session_id"`
	Origin    string `json:"origin"`
}

// Bus 是控制面的连接。nil 是合法值（未启用），但写操作会返回 ErrUnavailable。
type Bus struct {
	rdb    *redis.Client
	origin string
}

// Open 连上 Redis。连不上返回错误 —— 由调用方决定拒绝启动还是降级。
func Open(url string) (*Bus, error) {
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
	host, _ := os.Hostname()
	if host == "" {
		host = "unknown"
	}
	return &Bus{rdb: rdb, origin: fmt.Sprintf("%s/%d", host, os.Getpid())}, nil
}

var (
	sharedOnce sync.Once
	sharedInst *Bus
)

// Shared 返回进程级单例。没配 REDIS_URL 或连不上时返回 nil。
//
// 这里另开一个 redis.Client 而不是复用 internal/lock 的：pub/sub 订阅在 go-redis
// 里本来就要独占一条连接（它得一直挂在那儿读），共用客户端省不下什么，而分开之后
// 两个包各自独立，锁的连接池不会被订阅占住。
func Shared() *Bus {
	sharedOnce.Do(func() {
		url := os.Getenv("REDIS_URL")
		if url == "" {
			log.Printf("control_bus disabled (REDIS_URL 未配置) —— 单实例下 stop 本来就一定命中")
			return
		}
		b, err := Open(url)
		if err != nil {
			// 单实例下这没有影响；多副本下意味着"停止"退回抛硬币，所以照常喊出来。
			log.Printf("control_bus disabled: %v (多副本下跨 pod 的停止将失效)", err)
			return
		}
		sharedInst = b
		log.Printf("control_bus enabled origin=%s channel=%s", b.origin, stopChannel)
	})
	return sharedInst
}

// PublishStop 请集群里持有该会话的那个 pod 停掉它正在跑的 turn。
//
// 本 pod 自己的订阅者也会收到这条 —— 它在自己的 map 里查得到会话就会照办，
// 查不到就丢掉。所以不需要按 origin 过滤自己：重复调 Stop 是幂等的
// （第二次返回 false），而少一个过滤分支就少一处能写错的地方。
func (b *Bus) PublishStop(ctx context.Context, sessionID string) error {
	if b == nil || b.rdb == nil {
		return ErrUnavailable
	}
	payload, err := json.Marshal(stopMessage{SessionID: sessionID, Origin: b.origin})
	if err != nil {
		return fmt.Errorf("marshal stop message: %w", err)
	}
	// Publish 的返回值是收到消息的订阅者数量。0 意味着**全集群没有一个 pod 在听** ——
	// 那是配置层面的问题（比如所有 pod 的订阅都断了），值得留一行日志；但不当错误返回，
	// 因为订阅者数量本身有竞态，而调用方接下来会去看锁到底放没放，那是更可靠的判据。
	n, err := b.rdb.Publish(ctx, stopChannel, payload).Result()
	if err != nil {
		return fmt.Errorf("publish stop: %w", err)
	}
	if n == 0 {
		log.Printf("control_bus_no_listeners session=%s —— 没有 pod 在监听停止频道，这条请求不会被执行", sessionID)
	}
	return nil
}

// SubscribeStop 起一个后台 goroutine 消费停止请求，返回退订函数。
//
// handler 在订阅 goroutine 里同步调用，所以它必须快 —— 会话查表 + cancel() 都是
// 纳秒级的，没问题；将来要往里加会阻塞的东西（比如等落库）就得自己起 goroutine，
// 否则一条消息会挡住后面所有的。
//
// nil 接收者返回一个 no-op 退订函数，调用点不需要判空。这跟 PublishStop 的
// 处理不一致，是有意的：订阅失败的后果是"收不到别人的停止请求"，而单实例下
// 根本没有别人；发布失败的后果是调用方等一个不会来的响应。
func (b *Bus) SubscribeStop(handler func(sessionID string)) (stop func()) {
	if b == nil || b.rdb == nil {
		return func() {}
	}

	ctx, cancel := context.WithCancel(context.Background())
	// Subscribe 立刻返回，订阅在后台建立；go-redis 会在连接断开时自动重连
	// （重连窗口里的消息会丢，见包注释）。
	pubsub := b.rdb.Subscribe(ctx, stopChannel)

	var once sync.Once
	done := make(chan struct{})

	go func() {
		defer close(done)
		ch := pubsub.Channel()
		for {
			select {
			case <-ctx.Done():
				return
			case msg, ok := <-ch:
				if !ok {
					// go-redis 关掉了 channel（pubsub.Close 之后）。
					return
				}
				var m stopMessage
				if err := json.Unmarshal([]byte(msg.Payload), &m); err != nil {
					log.Printf("control_bus_bad_payload channel=%s payload=%q err=%v", msg.Channel, msg.Payload, err)
					continue
				}
				if m.SessionID == "" {
					continue
				}
				handler(m.SessionID)
			}
		}
	}()

	return func() {
		once.Do(func() {
			cancel()
			_ = pubsub.Close()
			<-done
		})
	}
}

// Close 关掉底层连接。只有进程退出时该调 —— Shared() 是单例，谁都别顺手关它。
func (b *Bus) Close() error {
	if b == nil || b.rdb == nil {
		return nil
	}
	return b.rdb.Close()
}
