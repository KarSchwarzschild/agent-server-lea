package persistence

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"hash/crc32"
	"log"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
	"gitlab.ouc-online.com.cn/aibase/agentloop/llm"
)

// inflightTTL 是子轮快照的存活时间。
//
// 这个数不需要覆盖一次会话的长度，只需要覆盖"一次尝试挂掉到下一次尝试接手"的时间。
// Temporal 的 RetryPolicy 是 InitialInterval 1s / MaximumAttempts 3，所以正常路径上
// 它只活几秒。取得宽松是要容下 Activity 的 StartToCloseTimeout（24h）被打断之后的
// 人工重放，同时不让一个再也不会被读的快照长期占着内存。
const inflightTTL = 6 * time.Hour

// inflightPrefix 与其余 Redis 用途（消息缓存、事件流、分布式锁）共用同一个实例，
// 靠前缀隔离，不新开 DB —— 运维上只有一个 Redis 连接要管。
const inflightPrefix = "agentloop:inflight:"

// 快照是整条工作历史，往返 JSON 一遍。上限防的是病态会话把一条命令撑到几十 MB
// （Redis 单 value 512MB，但真到那量级就该重新想这件事，而不是让它悄悄过去）。
const maxInflightBytes = 4 << 20

// InflightStore 是 Redis 侧的客户端接口。
//
// 收成接口而不是直接吃 *redis.Client，是为了让引擎侧的逻辑能在一个假实现上测 ——
// 真实调用方的失败模式（Redis 连不上、超时）恰恰是最需要覆盖的那几种。
//
// 快照在 Redis 里是一个 list，一条消息一个元素（见 WriteInflightSnapshot 上面那段
// 说明为什么不是一个大 JSON 字符串），所以接口是 list 的形状而不是 KV 的形状。
type InflightStore interface {
	// ReanchorKey 用 messages 整条重建 key：清空 + 逐条推入，一个事务里做完。
	// 任何"快照已经作废"的情形都走它。
	ReanchorKey(ctx context.Context, key string, messages [][]byte, ttl time.Duration) error
	// AppendToKey 往 key 尾部追加新元素。
	AppendToKey(ctx context.Context, key string, messages [][]byte, ttl time.Duration) error
	// Len 返回 key 当前有几个元素，0 表示没有。
	Len(ctx context.Context, key string) (int, error)
	// Range 读回整条快照。没有则返回 errInflightNotFound。
	Range(ctx context.Context, key string) ([][]byte, error)
	Del(ctx context.Context, key string) error
}

// redisInflight 是 InflightStore 的真实现。
type redisInflight struct {
	client *redis.Client
}

// reanchorScript 保证"清空 + 重建"是一个原子动作。
//
// 分开写成 DEL 再 RPUSH 会有个窗口：DEL 已经生效、RPUSH 还没到，此刻新 worker 接手
// 读到的是一把空 key —— 它会当成"没有快照"回落到库里那份，比读不到更糟的是这次回落
// 是静默的（见 errInflightNotFound 那段：没有快照是正常情况）。Lua 里跑就没有这个窗口。
var reanchorScript = redis.NewScript(`
redis.call('DEL', KEYS[1])
if #ARGV > 1 then
  redis.call('RPUSH', KEYS[1], unpack(ARGV, 2))
end
redis.call('EXPIRE', KEYS[1], ARGV[1])
return #ARGV - 1
`)

func (r redisInflight) ReanchorKey(ctx context.Context, key string, messages [][]byte, ttl time.Duration) error {
	args := make([]any, 0, len(messages)+1)
	args = append(args, int(ttl.Seconds()))
	for _, message := range messages {
		args = append(args, message)
	}
	return reanchorScript.Run(ctx, r.client, []string{key}, args...).Err()
}

// AppendToKey 用 RPUSH + EXPIRE 两步。
//
// 不套事务：这两步之间挂掉只意味着 TTL 停留在上一次续期的时刻，而快照本身是完整的。
// 反过来为了这点代价把每次追加都塞进 Lua，会让热路径多一次脚本调用开销。
//
// messages 为空表示"内容没变，只续 TTL"（WriteInflightSnapshot 的 no-op 出口）：此时
// 不发 RPUSH —— 空 RPUSH 虽然会被 Redis 当成空 list 立刻回收，但让一条空命令进 pipeline
// 只为省一个 if 不值得。EXPIRE 对不存在的 key 静默失败，正是这里想要的。
func (r redisInflight) AppendToKey(ctx context.Context, key string, messages [][]byte, ttl time.Duration) error {
	pipe := r.client.Pipeline()
	if len(messages) > 0 {
		args := make([]any, 0, len(messages))
		for _, message := range messages {
			args = append(args, message)
		}
		pipe.RPush(ctx, key, args...)
	}
	pipe.Expire(ctx, key, ttl)
	_, err := pipe.Exec(ctx)
	return err
}

func (r redisInflight) Len(ctx context.Context, key string) (int, error) {
	n, err := r.client.LLen(ctx, key).Result()
	if errors.Is(err, redis.Nil) {
		return 0, nil
	}
	return int(n), err
}

func (r redisInflight) Range(ctx context.Context, key string) ([][]byte, error) {
	values, err := r.client.LRange(ctx, key, 0, -1).Result()
	if err != nil {
		return nil, err
	}
	if len(values) == 0 {
		return nil, errInflightNotFound
	}
	out := make([][]byte, len(values))
	for i, value := range values {
		out[i] = []byte(value)
	}
	return out, nil
}

func (r redisInflight) Del(ctx context.Context, key string) error {
	return r.client.Del(ctx, key).Err()
}

// errInflightNotFound 表示这个 run 的该次尝试没有留下快照。
//
// 这是**正常情况**，不是错误：第一次尝试、没有开 Redis、快照已过期、上一轮是干净
// 结束的（那就 ClearInflight 删掉了），四条路都落在这里。所以调用方拿到它应当静默
// 回落到 CheckpointStore 的边界，而不是记一条错误日志。
var errInflightNotFound = errors.New("inflight snapshot not found")

// 全局客户端。进程内共享一个 —— 它不持有任何会话状态，key 里带着全部维度。
var (
	inflightMu     sync.RWMutex
	globalInflight InflightStore
)

// SetInflightStore 装上 Redis 客户端，供进程启动时调用一次。
//
// 与 runtime.InitCache 同一个模式：拿不到就不装，引擎侧探测到接口缺失就整个跳过，
// 退化成本次改动之前的行为（只有 CheckpointStore 的粗粒度恢复），而不是报错。
func SetInflightStore(client *redis.Client) {
	if client == nil {
		return
	}
	inflightMu.Lock()
	globalInflight = redisInflight{client: client}
	inflightMu.Unlock()
}

// inflightStore 返回已装上的客户端，未装则 nil。
func inflightStore() InflightStore {
	inflightMu.RLock()
	defer inflightMu.RUnlock()
	return globalInflight
}

// inflightKey 每个 (run, attempt) 一把钥匙。
//
// attempt 进 key 就是 fencing：旧 worker 挂之前那次写入落在 attempt=N 上，新 worker
// 读的是 N+1，两者永不互相覆盖，也就不需要比较并交换或者 Lua。代价是每次重试留一个
// 到期才清的孤儿 key，用 TTL 收掉。
func inflightKey(tenantID, sessionID, runID string, attempt int) string {
	return fmt.Sprintf("%s%s:%s:%s:%d", inflightPrefix, tenantID, sessionID, runID, attempt)
}

// inflightAnchor 记着"这条 Redis list 上我已经写到哪了"。
//
// 它是追加路径成立的前提：RPUSH 只能往后加，所以只有确认内存里这份历史是把上次写出去
// 的那份**原样接长**，追加才是对的。anchor 存条数和一份内容摘要 —— 摘要能发现"条数没变
// 但内容被换掉了"（压缩把前面几十条换成一条摘要、后面的索引整体前移，条数就可能相近），
// 只比条数是发现不了的。
type inflightAnchor struct {
	// count 上次写出后 list 里的元素个数。
	count int
	// digest 上次写出去的那批消息的内容摘要，用来识别"条数没变但内容变了"。
	digest uint32
	// mu 串行化同一把 key 的写入。同一个 run 的写入来自同一个 turn，正常不并发；
	// 但 WriteInflight 是 best-effort 且从引擎的多个出口调用，读-改-写不加锁就可能在
	// "读 Redis 长度"和"RPUSH"之间插进另一次写，两边算出来的增量重叠。
	mu sync.Mutex
}

// 每个 key 一份 anchor。进程内共享，因为追加语义是 key 的属性（这条 list 被写到哪了），
// 不是某个 engineStore 实例的属性。miss 只会导致一次多余的全量重写，不会写错。
var (
	inflightAnchorsMu sync.Mutex
	inflightAnchors   = map[string]*inflightAnchor{}
)

// anchorFor 取（必要时建）某个 key 的 anchor。
func anchorFor(key string) *inflightAnchor {
	inflightAnchorsMu.Lock()
	defer inflightAnchorsMu.Unlock()
	anchor, ok := inflightAnchors[key]
	if !ok {
		anchor = &inflightAnchor{}
		inflightAnchors[key] = anchor
	}
	return anchor
}

// dropAnchor 丢掉某个 key 的 anchor，让下一次写入重新全量。
//
// 清快照时一起清，免得 map 跟着会话数无界增长 —— 这把 key 已经不会再被写了。
func dropAnchor(key string) {
	inflightAnchorsMu.Lock()
	delete(inflightAnchors, key)
	inflightAnchorsMu.Unlock()
}

// digestMessages 是这一段消息的内容摘要。
//
// 逐条 JSON 之后过 CRC32，不加密、不需要抗碰撞：这里问的是"和上次写出去的是不是同一
// 批字节"，不是防谁伪造历史。摘要只在写入路径上算，走追加时算的是整条历史（读一遍，
// 不重新编码），走重锚时本来就要编码一遍，顺带算掉。
func digestMessages(encoded [][]byte) uint32 {
	checksum := crc32.NewIEEE()
	for _, raw := range encoded {
		_, _ = checksum.Write(raw)
		_, _ = checksum.Write([]byte{0}) // 分隔，免得两条的边界被拼成同一个流
	}
	return checksum.Sum32()
}

// remember 记下"这把 key 上现在有 count 条，内容摘要是 digest"。
//
// 摘要算的始终是**整条**已写出的历史，不是尾部：只有整条可比，才能回答"内存里这份是不是
// 把上次那份原样接长"。存尾部的摘要答不了这个问题，因为尾部一致而前缀被压缩替换掉的
// 情形恰好是必须拦下的那种。
func (a *inflightAnchor) remember(count int, digest uint32) {
	a.count = count
	a.digest = digest
}

// WriteInflightSnapshot 把当前工作历史同步到 Redis。
//
// ── 为什么是 list 而不是一个 JSON 字符串 ──────────────────────────────
//
// 引擎每一步都拍一次，所以这个函数是热路径。整条历史 marshal 一遍再 SET，等于把同一
// 批老消息反复序列化：一轮 40 步、历史涨到 80 条时，累计写出去的是 1.4MB 左右，而真正
// 新增的只有每步的那一两条、加起来不到 100KB。list 的形状让增量可以只推新增的那几条
// （RPUSH），把这一步降到 O(新增)。
//
// ── 什么时候不能追加 ────────────────────────────────────────────────
//
// 追加的前提是"历史只增不减"。这个前提会破：引擎的 contextControl 每轮都可能压缩，
// 压缩把前 N 条换成一条摘要（长度和内容都变），turn 提交时工作集被整体换掉，rewind
// 则是显式回退。这些情况下继续 RPUSH 会把新消息接在旧尾巴后面，读出来是**错位的
// 历史** —— 比读不到更糟，因为它不报错，只是内容错的。
//
// 所以这里的判据是：内存里那份必须是把已写出的那份原样接长（前缀逐条相同、条数是
// 增加）。成立就 RPUSH 增量，不成立就整条重锚。重锚是永远正确的那条路，追加只是它
// 在 append-only 情形下的快路径。
func WriteInflightSnapshot(ctx context.Context, store InflightStore, tenantID, sessionID, runID string, attempt int, messages []llm.Message) error {
	if store == nil || runID == "" || attempt <= 0 || len(messages) == 0 {
		return nil
	}
	key := inflightKey(tenantID, sessionID, runID, attempt)
	anchor := anchorFor(key)

	// 整条历史的量级检查放在最前面：追加路径下真正写出去的只是尾部那几条，但快照的
	// 上限说的是"一把 key 能存多少"，追加和重锚受同一个上限约束。
	if total := estimateSnapshotBytes(messages); total > maxInflightBytes {
		return fmt.Errorf("inflight snapshot too large: ~%d bytes (limit %d) session=%s run=%s",
			total, maxInflightBytes, sessionID, runID)
	}

	// 整条编码一次。走重锚时本来就要编；走追加时只有尾部那几条会推出去，编码出来的
	// 前面那段只用来算摘要，不会被写到网络上 —— 这是"追加"真正省下来的东西。
	encoded, err := encodeMessages(messages)
	if err != nil {
		return err
	}
	digest := digestMessages(encoded)

	anchor.mu.Lock()
	defer anchor.mu.Unlock()

	// 字节和上次写出去的一模一样：什么都没变，续一次 TTL 就够。
	if anchor.count == len(encoded) && anchor.digest == digest {
		return store.AppendToKey(ctx, key, nil, inflightTTL)
	}

	appendFrom := -1
	if anchor.count > 0 && anchor.count < len(encoded) && digestMessages(encoded[:anchor.count]) == anchor.digest {
		// 已写出的那段逐字节没变，而且内存里这份比它长 —— 这是追加唯一成立的条件。
		// 前面的摘要比对是必须的：压缩会把前 N 条换成一条摘要，此时条数可能相近甚至
		// 变少，只比长度或只比尾部都拦不住，接上去就是一段错位的对话。
		if stored, err := store.Len(ctx, key); err != nil {
			return err
		} else if stored == anchor.count {
			// Redis 里确实是这么多条。问这一步是因为 key 可能已经被 TTL 收掉、或者被
			// 上一次尝试的 ClearInflight 删掉，而 anchor 还在本进程里记着 —— 那样追加
			// 会写出一段没有开头的历史。
			appendFrom = anchor.count
		}
	}

	if appendFrom < 0 {
		if err := store.ReanchorKey(ctx, key, encoded, inflightTTL); err != nil {
			return err
		}
		anchor.remember(len(encoded), digest)
		return nil
	}

	if err := store.AppendToKey(ctx, key, encoded[appendFrom:], inflightTTL); err != nil {
		return err
	}
	// count 靠本地推，摘要就是刚算好的那份，不用再问一次 Redis。
	anchor.remember(len(encoded), digest)
	return nil
}

// encodeMessages 把消息逐条 marshal 成 list 元素。
func encodeMessages(messages []llm.Message) ([][]byte, error) {
	encoded := make([][]byte, len(messages))
	for i, message := range messages {
		raw, err := json.Marshal(message)
		if err != nil {
			return nil, fmt.Errorf("marshal inflight message: %w", err)
		}
		encoded[i] = raw
	}
	return encoded, nil
}

// estimateSnapshotBytes 估算整条历史落在 Redis 里的大致字节数。
//
// 逐条走内容块累加，而不是 marshal 一遍再量：走追加路径时这是唯一一处会碰到全部消息的
// 地方，而它只需要读一遍，不重新编码。估得偏大偏小都无所谓 —— 它守的是 maxInflightBytes
// 这个数量级的闸，不是一个精确账本。
func estimateSnapshotBytes(messages []llm.Message) int {
	total := 0
	for _, message := range messages {
		total += len(message.Role) + len(message.Name) + 64 // 角色、名字、JSON 外壳
		total += estimateBlocksBytes(message.Content)
	}
	return total
}

func estimateBlocksBytes(blocks []llm.ContentBlock) int {
	total := 0
	for _, block := range blocks {
		total += len(block.Text) + len(block.Input) + len(block.ID) + len(block.Name) +
			len(block.ToolUseID) + len(block.Signature) + 96
		total += estimateBlocksBytes(block.Content)
		if block.Source != nil {
			total += len(block.Source.Data) + len(block.Source.URL) + len(block.Source.MediaType)
		}
	}
	return total
}

// LoadInflightSnapshot 读上一次尝试留下的快照。
//
// 返回值语义：nil 表示没有快照，调用方按正常路径走。真错误（Redis 不可达）也返回 nil
// 并记日志 —— 恢复路径不该因为缓存层的问题而让整个重试失败。
func LoadInflightSnapshot(ctx context.Context, store InflightStore, tenantID, sessionID, runID string, attempt int) []llm.Message {
	if store == nil || runID == "" || attempt <= 0 {
		return nil
	}
	values, err := store.Range(ctx, inflightKey(tenantID, sessionID, runID, attempt))
	if err != nil {
		if !errors.Is(err, errInflightNotFound) {
			log.Printf("inflight_load_failed session=%s run=%s attempt=%d err=%v —— 回落到 checkpoint 边界",
				sessionID, runID, attempt, err)
		}
		return nil
	}
	messages := make([]llm.Message, 0, len(values))
	for _, raw := range values {
		var message llm.Message
		if err := json.Unmarshal(raw, &message); err != nil {
			// 一条坏了就当整把 key 坏了：快照是逐条 RPUSH 上去的，读出来顺序错一条，
			// 接在后面的就全是错位的对话 —— 那比没有快照更危险（不报错，内容错的）。
			log.Printf("inflight_decode_failed session=%s run=%s attempt=%d err=%v",
				sessionID, runID, attempt, err)
			return nil
		}
		messages = append(messages, message)
	}
	return messages
}

// ClearInflightSnapshot 在 turn 干净收尾后删掉快照。
func ClearInflightSnapshot(ctx context.Context, store InflightStore, tenantID, sessionID, runID string, attempt int) error {
	if store == nil || runID == "" || attempt <= 0 {
		return nil
	}
	key := inflightKey(tenantID, sessionID, runID, attempt)
	dropAnchor(key)
	return store.Del(ctx, key)
}

// trimToLastCompletePair 把快照截到最后一条"配对完整"的消息。
//
// 快照是每一步之后整条拍的，所以尾部可能是半截：模型返回了一批 tool_use，worker 在
// 第二个工具跑完、第三个跑到一半时挂了 —— 此时 messages 以一条 assistant（含 3 个
// tool_use）结尾，没有对应的 tool_result。
//
// 这种历史**不能直接喂给模型**：Anthropic 协议要求每个 tool_use 都有 tool_result，
// 缺一个就是 400，整轮失败。而这一轮恰恰是恢复路径，失败意味着卡死。
//
// 截断点选在最后一条"非 assistant 消息"之后。为什么不是"补齐缺失的 tool_result"：
// 少一个工具的结果，模型会以为它没跑过而重跑，是有副作用的操作做两遍；而截掉那一批
// 之后模型重新决策，它看到的是自己上一批的意图，选择权还在它手上。
//
// 注意截图不改变 turn 本身 —— 用户那条 prompt 一定在历史里（CheckpointPromptCommitted
// 先落），所以截出来的历史总有东西可续。
func trimToLastCompletePair(messages []llm.Message) []llm.Message {
	for i := len(messages) - 1; i >= 0; i-- {
		switch messages[i].Role {
		case llm.RoleAssistant:
			// 里面可能有没配对的 tool_use，继续往前找。
			continue
		case llm.RoleTool:
			// tool_result 在这里，但只有整批齐全才算数：模型一次请求多个工具时，
			// 引擎要么全跑完落一条 tool 消息，要么一个都没有。所以出现 RoleTool
			// 就是这条 assistant 的配对完整的标志。
			return messages[:i+1]
		default:
			// user / system：这里就是干净的边界。
			return messages[:i+1]
		}
	}
	return messages
}

// LoadInflightHistory 读某个 run 上一次尝试的快照，裁到安全边界。
//
// 返回 nil 表示没有可用快照，调用方应当回落到 LoadHistory。这条路径**不查库**：
// 有快照说明上一次尝试已经走过了 checkpoint，快照一定比库里那份新。
//
// attempt 传的是**上一次**的编号（当前是 N 就传 N-1）—— 写的时候用的就是发起那次
// 尝试的编号，读也必须对齐，否则永远读不到东西。
func LoadInflightHistory(ctx context.Context, tenantID, sessionID, runID string, attempt int) []llm.Message {
	return inflightSnapshotFor(ctx, tenantID, sessionID, runID, attempt)
}

// inflightSnapshotFor 读某个 run 上一次尝试的快照并裁剪到安全边界。
func inflightSnapshotFor(ctx context.Context, tenantID, sessionID, runID string, attempt int) []llm.Message {
	snapshot := LoadInflightSnapshot(ctx, inflightStore(), tenantID, sessionID, runID, attempt)
	if snapshot == nil {
		return nil
	}
	trimmed := trimToLastCompletePair(snapshot)
	if len(trimmed) == 0 {
		return nil
	}
	if len(trimmed) != len(snapshot) {
		log.Printf("inflight_trimmed session=%s run=%s attempt=%d msgs=%d->%d —— 尾部是没配对的 tool_use",
			sessionID, runID, attempt, len(snapshot), len(trimmed))
	}
	return trimmed
}
