package session

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log"
	"os"
	"strconv"
	"sync"
	"time"

	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/control"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/lock"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/runtime"
)

type Params struct {
	SessionID string
	AgentID   int
	TenantID  string
	UserID    string
}

type SSEEvent struct {
	Type string
	Data any
	// Seq 是这条事件在会话里的单调序号，由 broadcast 统一发放。它同时是 SSE 的
	// id: 字段和断线续传的游标 —— 客户端报告"我收到了 N"，服务端就从 N+1 补起。
	Seq uint64
}

// 会话驻留策略。内存里的 AgentSession 不是轻量对象 —— 每个持有一个 agentloop
// engine（LLM 连接 + 工具集）、可能一个远端沙箱、以及完整对话历史。原来这个 map
// 无上限、无过期、无淘汰，只增不减：单节点跑够久就是内存泄漏，跟多节点无关。
//
// 两道闸：空闲超时清理（常态），容量上限兜底（异常流量）。
const (
	defaultSessionIdleTTL = 30 * time.Minute
	defaultMaxSessions    = 500

	// 扫描周期的上下限。周期硬编码成固定值的话，TTL 一旦短于它就形同虚设
	// （设 TTL=30s 仍要等一整个周期才清），所以按 TTL/4 自适应再夹在这两端之间。
	minSweepInterval = 5 * time.Second
	maxSweepInterval = 2 * time.Minute
)

// sweepIntervalFor 按 TTL 推导扫描周期：TTL/4 保证过期会话最多多活 25% 的 TTL。
func sweepIntervalFor(ttl time.Duration) time.Duration {
	iv := ttl / 4
	if iv < minSweepInterval {
		return minSweepInterval
	}
	if iv > maxSweepInterval {
		return maxSweepInterval
	}
	return iv
}

// ErrForbidden 表示 session 存在但不归调用方所有。单独立一个 sentinel 而不是
// 复用 "not found"，是为了让 handler 能返 403 —— 但对外的措辞仍要小心，
// 别把"这个 id 存在"泄露出去。
var ErrForbidden = errors.New("forbidden")

// ErrUnverifiable —— 拿到了一个 session_id，但**无法确认它属于调用方**（库不可达）。
//
// 跟 ErrForbidden 分开是因为该做的事不同：403 是"这不是你的"，客户端不该重试；
// 这个是"我不知道"，客户端该稍后重试（handler 映射成 503）。
// 合成一个的话，一次数据库抖动会让用户看到"无权访问自己的会话"。
var ErrUnverifiable = errors.New("cannot verify session ownership")

// ErrCapacity 表示已达会话上限且没有可淘汰的空闲会话。返回 503 让客户端重试，
// 比默默挤掉一个别人正在用的会话要好。
var ErrCapacity = errors.New("session capacity reached")

// ErrBusy 表示这个会话已经有一轮在别的 pod 上跑着。转发 lock.ErrHeld，让 router
// 不必依赖 internal/lock —— 路由层关心的是"该返 409"，不是锁用什么实现的。
//
// 跟 ErrCapacity 的区别值得说清：ErrCapacity 是**服务器**满了，换台机器就能服务，
// 所以 503 + Retry-After；ErrBusy 是**这个会话**忙，换机器也一样，所以 409。
var ErrBusy = lock.ErrHeld

type Manager struct {
	mu       sync.RWMutex
	sessions map[string]*AgentSession

	idleTTL     time.Duration
	maxSessions int

	stopSweep chan struct{}
	sweepOnce sync.Once

	// locker 用来查"全集群有没有在跑这个会话"（IsHeld）。跟 AgentSession 里那个
	// 是同一个单例，各自持有只是为了不让 Manager 去 reach into session 内部。
	// nil = 单实例，本进程的内存就是全部真相。
	locker *lock.Locker
	// control 是跨 pod 的控制面。nil = 未启用，转发一律如实失败而不是假装成功。
	control *control.Bus
	// unsubControl 退订控制面。Close 时调，不然测试里每 NewManager 一次就多一条
	// 挂着的订阅 goroutine。
	unsubControl func()
	closeOnce    sync.Once
}

func NewManager() *Manager {
	m := &Manager{
		sessions:    make(map[string]*AgentSession),
		idleTTL:     durationFromEnv("LEA_AGENT_SESSION_TTL", defaultSessionIdleTTL),
		maxSessions: intFromEnv("LEA_AGENT_MAX_SESSIONS", defaultMaxSessions),
		stopSweep:   make(chan struct{}),
		locker:      lock.Shared(),
		control:     control.Shared(),
	}
	go m.sweepLoop()
	// 未启用时返回 no-op，不用判空。
	m.unsubControl = m.control.SubscribeStop(m.applyRemoteStop)
	return m
}

// applyRemoteStop 是收到别的 pod 转发过来的停止请求时的动作。
//
// 全局频道下每个 pod 都会收到每一条，所以绝大多数调用的结果是"本进程没有这个
// 会话"→什么也不做。这不是浪费，是这个形状的全部代价（见 control 包注释）。
//
// 这里**不再校验归属**：发起方在转发之前已经查过库了（见 handleStop）。也就是说
// 信任边界在 Redis —— 能往这个频道 PUBLISH 的东西就能停掉任意会话。跟"能写锁键
// 就能让任意会话卡住"是同一级别的信任，而 Redis 本来就只在内网。
func (m *Manager) applyRemoteStop(sessionID string) {
	m.mu.RLock()
	s := m.sessions[sessionID]
	m.mu.RUnlock()
	if s == nil {
		return // 这条不是给本 pod 的
	}
	// stopped=false 是正常的：转发路上那一轮刚好自己结束了。
	stopped := s.Stop()
	log.Printf("remote_stop_applied session=%s stopped=%v", sessionID, stopped)
}

// IsTurnRunning 回答"这个会话此刻有没有在生成"，覆盖全集群。
//
// 前端断流之后唯一能做的兜底是去拉 /sessions/:id/messages，但 assistant 消息是
// persistTurn 在**整轮结束时**才写的 —— 所以"还在生成"和"已经结束但我没收到"
// 在那个接口上长得一模一样。缺了这个判断，前端只能猜：猜早了丢答案，猜晚了
// 用户对着一个永远转不完的圈。
//
// 先看本进程内存再查锁，顺序不能反：单实例部署下 locker 是 nil、IsHeld 恒为 false，
// 只有内存那道能给出正确答案。
func (m *Manager) IsTurnRunning(ctx context.Context, sessionID string) (bool, error) {
	m.mu.RLock()
	s := m.sessions[sessionID]
	m.mu.RUnlock()
	if s != nil && s.HasActiveTurn() {
		return true, nil
	}
	return m.locker.IsHeld(ctx, sessionID)
}

// ForwardStop 请持有该会话执行权的那个 pod 停掉它的 turn。
//
// 返回 false, nil 表示**全集群**都没有在跑的 turn —— 调用方可以据此如实返 404，
// 而不用乐观地假装转发成功。这是把锁当路由表用（见 lock.IsHeld）。
//
// 返回的 true 只表示"信号已经发出去了"，不表示对方已经停了。pub/sub 没有投递
// 保证，而且就算送到了，那一轮也要跑完收尾才真的结束。权威的完成信号是 SSE 流上
// 的 result + done —— 它就在跑这一轮的那个 pod 上，所以用户一定看得见。
func (m *Manager) ForwardStop(ctx context.Context, sessionID string) (bool, error) {
	held, err := m.locker.IsHeld(ctx, sessionID)
	if err != nil {
		return false, err
	}
	if !held {
		return false, nil
	}
	if err := m.control.PublishStop(ctx, sessionID); err != nil {
		return false, err
	}
	log.Printf("stop_forwarded session=%s —— 本 pod 没有这个会话，已转给持锁的 runner", sessionID)
	return true, nil
}

// GetOrCreate 复用已有会话或新建一个。
//
// 复用前必须校验归属：命中缓存就直接 return 的话，认证用户只要塞一个别人的
// session_id 进 /chat/send，就能往对方正在跑的会话里发消息、驱动对方的 agent，
// 而且后续 turn 全程用的是**对方**的 tenant/user 上下文（KB、记忆、沙箱都是）。
func (m *Manager) GetOrCreate(ctx context.Context, p Params) (*AgentSession, error) {
	if p.SessionID != "" {
		m.mu.RLock()
		s, ok := m.sessions[p.SessionID]
		m.mu.RUnlock()
		if ok {
			if !s.OwnedBy(p.TenantID, p.UserID) {
				return nil, ErrForbidden
			}
			s.Touch()
			return s, nil
		}
		// 内存里没有 —— 可能是别的副本在跑、可能本进程重启过、也可能这个
		// session_id 压根不是调用方的。**必须查库确认归属才能往下走。**
		//
		// 少了这道就是一个跨用户的数据泄漏，而且只在多副本下出现：
		// 下面 NewAgentSession 会沿用客户端给的 session_id，引擎的 Resume 又会按
		// 这个 id 调 backend.LoadHistory 把历史读进上下文 —— 于是拿着别人的
		// session_id 打到"内存里没有它"的副本，就能让模型复述别人的对话。
		//
		// 实测过：同一个请求打三次，落到持有该会话的副本返 403，落到另一个副本
		// 返 200 并带出了受害者的历史。安全边界取决于负载均衡挑了谁 —— 而单副本
		// 下永远命中上面那条内存分支，所以测不出来。
		if err := verifySessionOwner(ctx, p); err != nil {
			return nil, err
		}
	}

	// 建之前先腾地方。放在 LoadAgentConfig 之前：配置加载要打一次 HTTP，
	// 先确认有位置再花那个往返。
	if err := m.ensureCapacity(); err != nil {
		return nil, err
	}

	cfg, err := runtime.LoadAgentConfig(ctx, p.AgentID, p.TenantID)
	if err != nil {
		return nil, fmt.Errorf("load agent config: %w", err)
	}

	s, err := NewAgentSession(ctx, cfg, p)
	if err != nil {
		return nil, fmt.Errorf("create session: %w", err)
	}

	m.mu.Lock()
	m.sessions[s.ID()] = s
	m.mu.Unlock()
	return s, nil
}

// verifySessionOwner 确认一个**不在本进程内存里**的 session_id 归调用方所有。
//
// 三种结果，区别很重要：
//
//	库里有且是本人的  → nil，正常继续（多副本下这是常态：另一个副本建的会话）
//	库里有但不是本人的 → ErrForbidden
//	库里没有这一行     → nil。这是"客户端自带 id 开新会话"，没有任何历史可泄漏，
//	                     这个 id 从此归他。POST /api/sessions 之后紧接着发消息
//	                     就是走这条 —— 那个接口只在内存里建会话，第一条消息之后
//	                     才写库。
//	库不可达           → ErrUnverifiable。**不放行。**
//
// 有一个副作用是有意接受的：这让调用方能区分"这个 id 存在但不是我的"（403）和
// "这个 id 不存在"（继续新建）—— 一个存在性预言。可以接受是因为 session_id 是
// UUIDv4、枚举不可行，而另一边的代价是已经复现的跨用户历史泄漏。
// 哪天 session_id 变成可枚举的（自增、按时间戳派生），这个取舍要重算。
//
// 最后一条值得说明：这里选择"验不了就拒"，代价是数据库不可用时按 id 续聊会失败
// （不带 session_id 的新会话不受影响，它压根不走这个函数）。
// 反过来选"验不了就放行"的话，每一次数据库抖动都会把上面那个跨用户泄漏重新打开 ——
// 而且是静默的。这跟 router.authorizeAbsentSession 的取舍保持一致。
func verifySessionOwner(ctx context.Context, p Params) error {
	db, err := persistence.DB()
	if err != nil {
		// 配了库但连不上。这才是"我不知道"。
		log.Printf("session_owner_unverifiable session=%s err=%v", p.SessionID, err)
		return ErrUnverifiable
	}
	if db == nil {
		// LEA_AGENT_DB=off —— 有意关掉持久化。这跟"连不上"必须分开处理：
		// 持久化关着就没有任何历史被存过，backend.LoadHistory 也读不出东西，
		// 所以**没有可泄漏的数据**，放行是对的。
		//
		// 混成一个的后果是这个模式下按 id 续聊全部 503，而报错信息说的是
		// "cannot verify session ownership" —— 指向一个压根不存在的数据库问题。
		return nil
	}
	ownerUser, ownerTenant, err := persistence.GetSessionOwner(ctx, db, p.SessionID)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil // 全新会话，客户端自带 id
		}
		log.Printf("session_owner_query_failed session=%s err=%v", p.SessionID, err)
		return ErrUnverifiable
	}
	if ownerUser != p.UserID || ownerTenant != p.TenantID {
		log.Printf("session_owner_mismatch session=%s caller=%s/%s owner=%s/%s",
			p.SessionID, p.TenantID, p.UserID, ownerTenant, ownerUser)
		return ErrForbidden
	}
	return nil
}

// ensureCapacity 在达到上限时回收最久未活跃的空闲会话。
// 正在跑 turn 或还挂着 SSE 订阅者的一律不动 —— 淘汰它们等于掐断用户的对话。
func (m *Manager) ensureCapacity() error {
	m.mu.Lock()
	if len(m.sessions) < m.maxSessions {
		m.mu.Unlock()
		return nil
	}

	var victim *AgentSession
	var victimID string
	now := time.Now()
	var oldest time.Duration
	for id, s := range m.sessions {
		if s.HasActiveTurn() || s.SubscriberCount() > 0 {
			continue
		}
		if idle := s.IdleFor(now); victim == nil || idle > oldest {
			victim, victimID, oldest = s, id, idle
		}
	}
	if victim == nil {
		m.mu.Unlock()
		log.Printf("session_capacity_reached count=%d max=%d — 全部在活跃中，拒绝新建",
			len(m.sessions), m.maxSessions)
		return ErrCapacity
	}
	delete(m.sessions, victimID)
	m.mu.Unlock()

	log.Printf("session_evicted id=%s idle=%s reason=capacity", victimID, oldest.Round(time.Second))
	victim.Close() // 在锁外：Close 会跑 memory OnSessionEnd
	return nil
}

// sweepLoop 周期性清理空闲超时的会话。
func (m *Manager) sweepLoop() {
	t := time.NewTicker(sweepIntervalFor(m.idleTTL))
	defer t.Stop()
	for {
		select {
		case <-t.C:
			m.sweepIdle(time.Now())
		case <-m.stopSweep:
			return
		}
	}
}

// sweepIdle 摘出空闲超过 TTL 的会话并关闭。now 参数便于单元测试。
func (m *Manager) sweepIdle(now time.Time) int {
	m.mu.Lock()
	var expired []*AgentSession
	for id, s := range m.sessions {
		if s.HasActiveTurn() || s.SubscriberCount() > 0 {
			continue
		}
		if s.IdleFor(now) > m.idleTTL {
			expired = append(expired, s)
			delete(m.sessions, id)
		}
	}
	remaining := len(m.sessions)
	m.mu.Unlock()

	// Close 放锁外：它会起 goroutine 跑 memory OnSessionEnd，还要关订阅者 channel，
	// 拿着 Manager 的锁做这些会挡住所有请求。
	for _, s := range expired {
		s.Close()
	}
	if len(expired) > 0 {
		log.Printf("session_sweep expired=%d remaining=%d ttl=%s", len(expired), remaining, m.idleTTL)
	}
	return len(expired)
}

func (m *Manager) Get(id string) *AgentSession {
	m.mu.RLock()
	s := m.sessions[id]
	m.mu.RUnlock()
	if s != nil {
		s.Touch()
	}
	return s
}

func (m *Manager) ListByUser(tenantID, userID string) []map[string]string {
	m.mu.RLock()
	defer m.mu.RUnlock()
	var result []map[string]string
	for _, s := range m.sessions {
		if s.tenantID == tenantID && s.userID == userID {
			result = append(result, map[string]string{
				"session_id": s.ID(),
				"agent_id":   fmt.Sprintf("%d", s.agentID),
			})
		}
	}
	return result
}

// Count 返回当前驻留的会话数，供健康检查/监控用。
func (m *Manager) Count() int {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return len(m.sessions)
}

func (m *Manager) Delete(id string) {
	m.mu.Lock()
	s, ok := m.sessions[id]
	delete(m.sessions, id)
	m.mu.Unlock()
	if ok {
		s.Close()
	}
}

// ActiveTurns 返回当前有 turn 在跑的会话数。优雅退出靠它判断排空是否完成。
//
// 先把会话拷出来再逐个问，而不是握着 m.mu 调 HasActiveTurn —— 后者要拿 s.turnMu，
// 在 m.mu 里嵌套 turnMu 会给死锁留口子（任何"先 turnMu 再 m.mu"的路径就构成
// 反向加锁序）。拷一份指针切片是几十纳秒的事，不值得为它冒这个风险。
func (m *Manager) ActiveTurns() int {
	m.mu.RLock()
	sessions := make([]*AgentSession, 0, len(m.sessions))
	for _, s := range m.sessions {
		sessions = append(sessions, s)
	}
	m.mu.RUnlock()

	n := 0
	for _, s := range sessions {
		if s.HasActiveTurn() {
			n++
		}
	}
	return n
}

// WaitIdle 等到没有 turn 在跑，或者 ctx 到期。
//
// 这是优雅退出的第二段：http.Server.Shutdown 只等**还挂着 HTTP 连接**的请求，
// 而客户端提前断开的 turn 仍在后台跑（见 router.onClientGone），它没有连接可等。
// 那种 turn 恰恰是最需要等的 —— 它的答案只存在于 persistTurn 里，进程一死就没了。
//
// 为什么轮询而不是等一个 channel：turn 的生死由每个 AgentSession 自己的 goroutine
// 管，没有中心计数器。加一个就得让每条收尾路径都记得减一，漏一处就让退出流程永久
// 卡住 —— 而收尾路径有正常结束、出错、被 Stop、被打断好几条。轮询是拿 200ms 的
// 延迟换"不可能漏"。
func (m *Manager) WaitIdle(ctx context.Context) error {
	const poll = 200 * time.Millisecond
	tick := time.NewTicker(poll)
	defer tick.Stop()
	logEvery := time.NewTicker(5 * time.Second)
	defer logEvery.Stop()

	for {
		n := m.ActiveTurns()
		if n == 0 {
			return nil
		}
		select {
		case <-ctx.Done():
			// 调用方要据此决定是硬退还是再等 —— 把还剩几个带出去。
			log.Printf("drain_timeout active_turns=%d —— 这些 turn 的结果会丢（persistTurn 来不及跑）", n)
			return ctx.Err()
		case <-logEvery.C:
			log.Printf("draining active_turns=%d", n)
		case <-tick.C:
		}
	}
}

func (m *Manager) Close() {
	m.sweepOnce.Do(func() { close(m.stopSweep) })
	// 退订放在关会话之前：订阅还活着的时候进来一条 stop，它会去 map 里查，而 map
	// 正在被清空 —— 那本身是安全的（有锁，查不到就返回），但先退订能少一个需要
	// 想清楚的交错。unsubControl 会等订阅 goroutine 真的退出。
	m.closeOnce.Do(func() {
		if m.unsubControl != nil {
			m.unsubControl()
		}
	})
	m.mu.Lock()
	sessions := make([]*AgentSession, 0, len(m.sessions))
	for _, s := range m.sessions {
		sessions = append(sessions, s)
	}
	m.sessions = make(map[string]*AgentSession)
	m.mu.Unlock()
	for _, s := range sessions {
		s.Close()
	}
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
