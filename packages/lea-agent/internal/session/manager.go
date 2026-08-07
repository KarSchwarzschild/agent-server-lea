package session

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"strconv"
	"sync"
	"time"

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

// ErrCapacity 表示已达会话上限且没有可淘汰的空闲会话。返回 503 让客户端重试，
// 比默默挤掉一个别人正在用的会话要好。
var ErrCapacity = errors.New("session capacity reached")

type Manager struct {
	mu       sync.RWMutex
	sessions map[string]*AgentSession

	idleTTL     time.Duration
	maxSessions int

	stopSweep chan struct{}
	sweepOnce sync.Once
}

func NewManager() *Manager {
	m := &Manager{
		sessions:    make(map[string]*AgentSession),
		idleTTL:     durationFromEnv("LEA_AGENT_SESSION_TTL", defaultSessionIdleTTL),
		maxSessions: intFromEnv("LEA_AGENT_MAX_SESSIONS", defaultMaxSessions),
		stopSweep:   make(chan struct{}),
	}
	go m.sweepLoop()
	return m
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

func (m *Manager) Close() {
	m.sweepOnce.Do(func() { close(m.stopSweep) })
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
