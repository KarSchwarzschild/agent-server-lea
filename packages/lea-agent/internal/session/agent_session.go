package session

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"encoding/json"
	"github.com/google/uuid"
	engine "gitlab.ouc-online.com.cn/aibase/agentloop"
	"gitlab.ouc-online.com.cn/aibase/agentloop/llm"
	"gitlab.ouc-online.com.cn/aibase/agentloop/team"
	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
	"gitlab.ouc-online.com.cn/aibase/agentloop/turn"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/control"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/eventlog"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/lock"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/runtime"
)

type AgentSession struct {
	id       string
	tenantID string
	userID   string
	agentID  int
	cfg      *runtime.AgentConfig
	// engine 是常驻的引擎会话，星型和单 agent 都用它 —— 历史留在里面，所以多轮对话
	// 连得上。
	//
	// handoff 模式下它是 nil：那条链每一棒都是引擎自己建的独立会话（engine.RunChain），
	// 没有一个"当前会话"可以持有。判断用哪条路看 chainCfg 而不是看它是否为 nil，
	// 这样"没建起来"和"这个团是 handoff"不会长得一样。
	engine *engine.Session
	// chainCfg / chainOpts 非零表示这是 handoff 团：每次 Send 用它们跑一整条链。
	// 存 Config 而不是 Session，因为链的会话生命周期在引擎手里。
	chainCfg  *engine.Config
	chainOpts engine.Options
	// isHandoff 表示这个会话是 handoff 团——没有常驻 engine，plan mode 会 panic。
	// 星型团有 engine，团长可以正常跑 plan mode（团长就是 Qoder 里的主 agent，
	// ask_expert 就是 Agent tool）。只 handoff 需要拒服。
	isHandoff bool

	// ctxBudget 是 Build 时算出的上下文预算（见 contextBudget），同时喂给引擎的
	// MaxContextTokens 和 EngineBackend 的 loadBudget。存成字段是因为 handoff 那条路
	// 每轮现建 backend，够不到 Build 里的局部变量。
	ctxBudget int
	// lastChain 是最近一条链的结果，runChain 写、落库读。链的会话是引擎建的、跑完
	// 就关，所以那段对话只在 ChainResult 里存在过一次 —— 不接住就没了。
	// 受 mu 保护。
	lastChain *engine.ChainResult
	memory    *runtime.MemoryMiddleware

	mu          sync.RWMutex
	subscribers map[chan SSEEvent]struct{}
	history     []runtime.MessagePair

	// H1: 每个 turn 独立的可视化收集器（Send 起手 new，落库后清空）
	collectorMu sync.Mutex
	collector   *messageCollector

	// turnCancel 是当前 turn 的取消函数，Stop() 用它掐断整条链路。
	// turnEpoch 每次 Send 自增：goroutine 收尾时只有 epoch 仍是自己那轮才清
	// cancel，否则会把下一轮刚注册的 cancel 误清成 nil（Stop 就失效了）。
	// turnStopped 记录本轮是否被用户主动停 —— 落库时用来区分"出错"和"用户停"。
	turnMu      sync.Mutex
	turnCancel  context.CancelFunc
	turnEpoch   uint64
	turnStopped bool

	// locker 是跨 pod 的执行权。存成字段而不是每次调 lock.Shared()，是为了让测试
	// 能在同一个二进制里同时覆盖"有 Redis"和"没 Redis"两条路 —— Shared() 是
	// sync.Once，一个进程里只能初始化一次。
	//
	// nil 合法：那就是单实例部署（今天的现状），所有加锁调用退化成 no-op。
	locker *lock.Locker

	// control 是跨 pod 的控制面，用来请持锁的那个 pod 让位（见 acquireTurnLock）。
	// nil 合法，但发布会如实返回 ErrUnavailable 而不是假装成功 —— 否则我们会去等
	// 一个永远不会到的让位。
	control *control.Bus

	// eventSeq 是本会话事件的单调序号，直接当 SSE 的 id: 用，也是续传的游标。
	// 每轮开始时从日志里 seed（见 seedEventSeq）—— 跨 pod 轮转时必须接着数。
	eventSeq atomic.Uint64
	// eventLog 把事件写进 Redis 供断线续传。nil = 未启用，Append 是 no-op。
	eventLog *eventlog.Writer
	// logs 是读侧（seed 序号要用）。跟 locker / control 一样存成字段而不是每次调
	// Shared()，这样测试能在同一个二进制里覆盖启用和未启用两条路。
	logs *eventlog.Log

	// lastActive 供 Manager 做空闲淘汰。单独一把锁而不是复用 mu/turnMu：
	// 清理协程要在不打断读写的前提下频繁读它。
	activeMu   sync.Mutex
	lastActive time.Time
}

// Touch 记一次活跃。Send / Subscribe / Get 都要调，否则正在用的会话会被清理掉。
func (s *AgentSession) Touch() {
	s.activeMu.Lock()
	s.lastActive = time.Now()
	s.activeMu.Unlock()
}

// IdleFor 返回距上次活跃的时长。
func (s *AgentSession) IdleFor(now time.Time) time.Duration {
	s.activeMu.Lock()
	defer s.activeMu.Unlock()
	return now.Sub(s.lastActive)
}

// HasActiveTurn 表示当前有 turn 在跑。淘汰前必须查这个 —— 一个会话可能空闲了
// 很久才发来一条消息，然后 turn 跑好几分钟（长任务 + 沙箱），期间 lastActive
// 不再更新。只看空闲时长会把正在生成回答的会话清掉。
func (s *AgentSession) HasActiveTurn() bool {
	s.turnMu.Lock()
	defer s.turnMu.Unlock()
	return s.turnCancel != nil
}

// SubscriberCount 返回当前挂着的 SSE 订阅者数量。
func (s *AgentSession) SubscriberCount() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.subscribers)
}

func NewAgentSession(ctx context.Context, cfg *runtime.AgentConfig, p Params) (*AgentSession, error) {
	sessionID := p.SessionID
	if sessionID == "" {
		sessionID = uuid.NewString()
	}

	// 只有主会话该有的工具（memory_add）。其余平台工具走 ToolRuntime 的 Provision
	// 通道，子代理和专家才继承得到 —— 见 GatewayToolset.Tools。
	extraTools := runtime.BuildMainSessionTools(cfg, p.TenantID, p.UserID, sessionID)
	toolEvents := make(chan tool.Event, 64)

	// skill bundle 投递到本会话的 skills 目录，让 Read 能读到 SKILL.md 引用的
	// reference 文件（activate_skill 的引导指的就是这些路径）。
	//
	// 失败只记日志：读不到 reference 是降级（SKILL.md 正文仍然注入），会话起不来是
	// 彻底不可用。lea-tools 侧沙箱创建时还会再投一次，是幂等的兜底。
	if names, err := runtime.ProvisionSessionSkills(ctx, cfg, p.TenantID, sessionID); err != nil {
		log.Printf("skill_provision_failed session=%s err=%v (SKILL.md 正文不受影响)", sessionID, err)
	} else if len(names) > 0 {
		log.Printf("skill_provisioned session=%s skills=%v", sessionID, names)
	}

	// system prompt 只放人设。skill 目录不再手拼 —— 填了 engineCfg.Skills 之后引擎的
	// skill.Catalog 会自动进 prompt（build.go 的 promptOpts.Skills），手拼那份就成了
	// 第二份目录，两份还可能不一致（DisabledSkills 只作用于引擎那份）。
	//
	// 而且旧那段写的是"先调用 activate_skill"，现在模型该调的是引擎的 Skill 工具 ——
	// 留着会把它引向一个正在退役的工具。
	systemPrompt := cfg.SystemPrompt

	// 专家名册：cfg.Team 非 nil 才有。装配要为每个成员再打一次 lea-config
	// （internal.py 按团长 agent_id 查团，members 只给到 member_agent_id 这个数字），
	// 所以放在 engineCfg 之前。
	experts := runtime.BuildExperts(ctx, cfg, p.TenantID, p.UserID, sessionID)
	// 拓扑由库里的 strategy 决定。解析失败（库里写了 "pipeline" 之类）退回星型并记日志：
	// 星型是这个字段有值以来一直的实际行为（strategy 之前压根没被消费过），所以对已有
	// 的团来说这不是变更；而静默当成 handoff 会让一个从没验证过的拓扑上线。
	teamMode := team.ModeStar
	if cfg.Team != nil {
		if m, err := team.ParseMode(cfg.Team.Strategy); err != nil {
			log.Printf("unknown_team_strategy session=%s strategy=%q err=%v (按星型运行)",
				sessionID, cfg.Team.Strategy, err)
		} else {
			teamMode = m
		}
	}
	if len(experts) > 0 {
		names := make([]string, 0, len(experts))
		for _, e := range experts {
			names = append(names, fmt.Sprintf("%s/%s", e.Name, team.RoleOf(e)))
		}
		log.Printf("team_assembled session=%s strategy=%s mode=%s experts=%v",
			sessionID, cfg.Team.Strategy, modeLabel(teamMode), names)
	}

	// 加载侧和发送侧必须用同一个预算，所以只算一次：MaxContextTokens 给引擎决定何时
	// 压缩，同一个数给 EngineBackend 决定 raw 路径最多载入多少。两处各算一次的话迟早
	// 漂开，而漂开的表现是加载侧砍了一段、引擎却以为还有余量。
	ctxBudget := contextBudget(cfg.ModelContextWindow, costCeilingTokens)

	engineCfg := engine.Config{
		Provider:       "openai",
		Model:          cfg.Model,
		MaxTokens:      cfg.MaxTokens,
		BaseURL:        cfg.LLMBaseURL,
		APIKey:         cfg.LLMAPIKey,
		CWD:            "/tmp",
		MaxIterations:  cfg.MaxTurns,
		SystemPrompt:   systemPrompt,
		PermissionMode: "flight",
		// 不填这个字段的后果是历史回收整个不跑：repl/session.go 里
		// `if s.maxContextTokens <= 0 { return working, false }` 直接返回，于是
		// 五个文件工具声明的 ResultRetention（Read/Glob 的 Reproducible、Write/Edit 的
		// InputStub、Bash 的 KeepTail）和引擎给 Skill 声明的那份，全都从未被调用过。
		MaxContextTokens: ctxBudget,

		// 引擎自带的跨会话记忆（memory.md + Remember 工具）在这里必须关掉，而且是
		// 无条件关，不跟着 memory_mode 走。
		//
		// 它的两个文件由 CWD 和进程的用户配置目录推导，而我们给每个会话的 CWD 都是
		// 同一个 "/tmp"，且 memory.Shared 对相同路径返回进程级共享的 Store。结果是
		// 一个 pod 上所有租户的所有会话共用同一份 memory.md：任何一个会话调
		// Remember 写进去的内容，会作为"既有事实"注入到别的租户下一个会话的 system
		// prompt 里。这是跨租户泄漏，而它不会自己报错。
		//
		// memory_mode=off 不生效只是这件事露出来的症状 —— 那个开关管的是本侧的
		// memory_add / memory_search / MemoryMiddleware（都带 tenant+user scope），
		// 管不到引擎内置的这个。
		//
		// 关掉之后没有功能损失：平台记忆是另一套，租户隔离由它保证。
		DisableMemory: true,

		// 以下三份名册**必须非 nil**，这是服务端和客户端的分界：
		// nil 在引擎里的意思是"去磁盘扫"（config.go 的 tri-state），而我们 CWD 是
		// /tmp，扫出来是空的 —— 那时引擎会合并它内置的六个代码场景 agent，于是线上
		// 能派活的一直只有那六个、库里配的一个都不在。这就是本轮之前的实际状态。
		Skills:  runtime.ToEngineSkills(cfg.Skills),
		Agents:  runtime.BuildAgentRoster(),
		Experts: experts,
		// 星型时是零值，也就是引擎的默认 —— 现有团走的代码路径一个字没变。
		TeamMode: teamMode,

		// 会话历史交给引擎自己读写，走 sessions.Backend（见下面的 opts.Backend）。
		//
		// Resume 让 Build 调 backend.LoadHistory —— 这个进程没跑过这个会话时（另一个
		// pod 服务过它，或本进程重启过）历史从库里回来。缺了它的后果是静默的：请求
		// 成功、照常计费，模型却当这段对话没发生过。
		//
		// PersistSession 让引擎把每轮消息写回去。落的是引擎原生消息（含 tool_use /
		// tool_result 块），所以下次加载连工具调用都在 —— 比从 chat_messages 的渲染
		// 正文反推要保真。chat_messages 那份仍然写，那是给前端看的。
		Resume:         sessionID,
		PersistSession: true,
	}

	// H2: 请求 AgentID 可能是 0（前端硬编码 super-agent），
	// LoadAgentConfig 已在 lea-config 侧兜底到 tenant super-agent，
	// 用 cfg.ID（真实 agent 主键）而不是 p.AgentID，保证 memory scope / 后续查询正确
	agentID := cfg.ID
	if agentID == 0 {
		agentID = p.AgentID
	}

	s := &AgentSession{
		id:          sessionID,
		tenantID:    p.TenantID,
		userID:      p.UserID,
		agentID:     agentID,
		cfg:         cfg,
		subscribers: make(map[chan SSEEvent]struct{}),
		lastActive:  time.Now(),
		locker:      lock.Shared(),
		control:     control.Shared(),
		// 只 handoff 团需要拒服 plan mode：它没有常驻 engine，SetPlanMode 会 panic。
		// 星型团有 engine，团长 = 主 agent，ask_expert = Agent tool，可以正常规划。
		isHandoff: teamMode == team.ModeHandoff,
		// 存下来给 handoff 那条路用：它每轮现建 backend 写历史（persistChainHistory），
		// 拿不到 Build 里的局部变量。
		ctxBudget: ctxBudget,
	}
	s.logs = eventlog.Shared()
	s.eventLog = s.logs.NewWriter(s.id)

	// 记忆中间件（memory_mode != off 时启用）
	if cfg.MemoryMode != "off" {
		s.memory = runtime.NewMemoryMiddleware(p.TenantID, p.UserID, fmt.Sprintf("%d", agentID))
	}

	opts := engine.Options{
		StreamDelta: func(delta string) {
			s.currentCollector().onDelta(delta)
			s.broadcast(SSEEvent{Type: "delta", Data: delta})
		},
		StreamThinking: func(delta string) {
			s.currentCollector().onThinking(delta)
			s.broadcast(SSEEvent{Type: "thinking", Data: delta})
		},
		ToolEvents: toolEvents,
		ExtraTools: extraTools,
		// 装我们自己的权限服务，为的是让它认识平台工具。
		//
		// 引擎的 DefaultResolver 只认它自己那批名字，其余全是 OperationUnknown，
		// 而 isMutatingAction 对 unknown 返回 false —— 于是 plan 模式在这儿只挡得住
		// Write/Edit/Bash 三个，code_interpreter、memory_add、所有租户网关工具照跑。
		// 见 runtime.PlatformResolver 顶部的实测输出。
		//
		// 一个会话一个实例（plan 模式是这个服务上的原子量，共享会串），而会话**内部**
		// 共享正是想要的：deriveExpertBase 原样继承这个指针，所以团长进规划态时它的
		// 专家和子代理跟着一起被挡。
		Permissions: runtime.NewPermissionService("flight"),
		// 身份传进 runtime：它装配的文件工具要靠 tenantID + sessionID 定位
		// workspace。放在 ToolRuntime 而非 ExtraTools，子代理才能继承（见
		// GatewayToolset.Tools 的注释）。
		ToolRuntime: runtime.NewGatewayRuntime(cfg, p.TenantID, p.UserID, sessionID),
		// 替掉引擎默认的 Skill 工具，只为在披露结果后追加一段"用 Read 读引用文件"的
		// 引导 —— 引擎的 locationHeader 说的是 "read them directly"，没点名工具，而
		// 实测不点名模型会跳过依赖这些文件的步骤。默认行为（schema / 截断 /
		// ResultRetention）靠内嵌 *skill.Tool 全部保留。
		//
		// 它不能走 ExtraTools：工具名在会话内唯一，第二个 "Skill" 会让装配失败而不是
		// 接管。
		// 传 true：拆 gate 之后 Read/Write/Edit/Glob 只要 cfg 非 nil 就注册
		// （gateway.go 的 Tools），而这里 cfg 必然非 nil，所以引导指向的 Read 一定在。
		SkillTool: runtime.NewGatewaySkillDiscloser(true),
	}

	// 会话历史的后端。db 拿不到时传 nil —— 引擎会退回它自己的 JSONL 后端，写到
	// CWD（/tmp）下，也就是本进程内有效、重启即失。那是降级而不是拒服：会话本身
	// 完全可用，只是跨节点接不上。
	if db, err := persistence.DB(); err == nil && db != nil {
		opts.Backend = persistence.NewEngineBackend(db, p.TenantID, ctxBudget)
	} else {
		log.Printf("engine_backend_unavailable session=%s err=%v —— 会话历史退回进程本地，"+
			"跨节点接续会失效", sessionID, err)
	}

	if teamMode == team.ModeHandoff {
		// handoff 团没有常驻会话：链上每一棒都是引擎按 role 建的独立会话，用完就关。
		// 所以这里只存配置，真正的装配发生在每次 Send。
		//
		// 代价是**不建会话就没人校验 roster**（引擎在 Build 里查 ValidateChain）。缺
		// entry/exit 的团要等到第一次 Send 才报错，那时用户已经在等回答了 —— 所以现在
		// 主动查一次，让配错的团在建会话时就失败，跟星型一样。
		if err := team.ValidateChain(team.NewSet(experts)); err != nil {
			return nil, fmt.Errorf("handoff 团配置不可用: %w", err)
		}
		s.chainCfg = &engineCfg
		s.chainOpts = opts
		go s.forwardToolEvents(toolEvents)
		return s, nil
	}

	sess, err := engine.Build(engineCfg, opts)
	if err != nil {
		return nil, err
	}
	s.engine = sess

	// 历史不在这里灌了 —— Build 已经通过 engineCfg.Resume + opts.Backend 自己加载过。
	// 这里曾经有一个手写的 seedHistoryFromDB，它的问题不是不好用而是**要人记得调**：
	// 星型这条调了，handoff 那条从来没调，而漏掉的表现是静默的。

	go s.forwardToolEvents(toolEvents)

	return s, nil
}

// modeLabel 把引擎的 team.Mode 渲染成日志里能读的字符串。ModeStar 是空串，直接打会
// 出现 `mode=` 这种看不出是星型还是没设的输出。
func modeLabel(m team.Mode) string {
	if m == team.ModeStar {
		return "star"
	}
	return string(m)
}

func (s *AgentSession) ID() string { return s.id }

// OwnedBy 校验会话归属 —— stop 之类的控制端点必须先过这道，
// 否则拿到别人的 session_id 就能掐断别人正在跑的 turn。
func (s *AgentSession) OwnedBy(tenantID, userID string) bool {
	return s.tenantID == tenantID && s.userID == userID
}

// lockWaitTimeout 是等本进程上一轮释放执行权的上限。取消传播很快，但上一轮还要
// 跑完收尾（落库），所以不能不等。等超了就返 409 让客户端重试 —— 比让 HTTP 请求
// 无限期挂着好。
const (
	lockWaitTimeout = 5 * time.Second
	lockPollEvery   = 50 * time.Millisecond
)

// acquireTurnLock 拿到本会话的执行权，必要时把上一轮打断。
//
// "上一轮在本进程"和"上一轮在别的 pod"是同一件事的两种物理形态，处理方式只差在
// 取消信号怎么送达：
//
//  1. 本进程 —— 直接调它的 cancel。REDIS_URL 没配时这是唯一生效的分支，所以
//     单实例下的行为跟接锁之前一模一样。
//  2. 别的 pod —— cancel 是个函数指针，跨不过进程边界，所以走控制面转发一条停止
//     请求（internal/control），然后跟等自己上一轮一样地轮询锁。
//
// 两条路合流之后语义统一了：**在生成过程中再发一条消息就是打断重问，跟落在哪个
// 副本上无关。**这是接 pub/sub 之前做不到的 —— 那时第 2 条只能拒（返 409），于是
// 同一个操作在单副本下是"打断"、多副本下有一定概率变成"被拒"，而概率取决于负载
// 均衡把请求丢给了谁。这种随部署形态变化的语义是最难排查的一类问题。
//
// 剩下的 409 只出现在真的等不到让位的时候：远端 pod 被 SIGKILL 了（没人解锁，要
// 等 TTL）、pub/sub 那条消息在重连窗口里丢了、或者那一轮的收尾比 lockWaitTimeout
// 还慢。这些都是异常，返 409 让客户端重试是对的。
//
// 顺带一句这不是唯一解：OpenAI Assistants 选的是纯拒绝（一个 thread 同时只能有一个
// active run，第二个直接 400），LangGraph 把它做成可配的 multitask_strategy
// （reject / interrupt / rollback / enqueue）。我们选 interrupt 是因为它匹配用户的
// 直觉 —— 打字打到一半改主意，按下发送就该换个问题，而不是收到一条报错。
func (s *AgentSession) acquireTurnLock() (*lock.Handle, error) {
	s.turnMu.Lock()
	prev := s.turnCancel
	s.turnMu.Unlock()
	if prev != nil {
		prev()
	}

	// waiting 表示"取消信号已经送出去了，值得轮询等它释放"。本进程有上一轮时天然
	// 成立；跨 pod 那条要等发布成功之后才成立 —— 没送出去就去等是白等五秒。
	waiting := prev != nil
	askedRemote := false

	deadline := time.Now().Add(lockWaitTimeout)
	for {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		h, err := s.locker.Acquire(ctx, s.id)
		cancel()
		if err == nil {
			return h, nil
		}
		if !errors.Is(err, lock.ErrHeld) {
			return nil, err
		}

		// 锁被别的 pod 占着（本进程的上一轮已经在上面 cancel 过了）。请它让位。
		// 只发一次 —— 轮询期间反复 PUBLISH 只会让对方重复调 Stop，那是幂等的但
		// 毫无意义，还会把日志刷满。
		if !waiting && !askedRemote {
			askedRemote = true
			pctx, pcancel := context.WithTimeout(context.Background(), 2*time.Second)
			perr := s.control.PublishStop(pctx, s.id)
			pcancel()
			if perr != nil {
				// 送不出去就别装作在等。如实返 ErrHeld → 409，客户端重试或者用户
				// 再点一次停止，都比让这个 HTTP 请求白挂五秒好。
				log.Printf("cross_pod_interrupt_failed session=%s err=%v —— 退回拒绝（409）", s.id, perr)
				return nil, err
			}
			log.Printf("cross_pod_interrupt session=%s —— 已请远端 runner 让位，等它释放执行权", s.id)
			waiting = true
		}

		// 走到这里 waiting 必然为真：要么本来就有上一轮，要么上面刚发出了转发请求
		// （发不出去的那条路已经 return 了）。
		//
		// 有一个罕见的漏网情况：本进程确实有上一轮（waiting=true），但它已经释放完
		// 执行权、被另一个 pod 抢走了 —— 这时我们只会干等而不会去请对方让位，5 秒后
		// 返 409。发生条件是"上一轮收尾"和"另一个 pod 抢锁"精确交错，而代价只是这次
		// 发送要重试一下，所以不为它增加一条分支。
		if time.Now().After(deadline) {
			return nil, err
		}
		time.Sleep(lockPollEvery)
	}
}

// seedEventSeq 把事件序号对齐到日志里已有的最大值，并清掉上一轮的降级标记。
//
// 为什么每轮都要做：序号是**进程内**的计数器，而会话可以在 pod 之间轮转。pod A 跑完
// 第一轮写到 seq=500，用户的第二条消息落到 pod B —— B 的计数器还是 0，于是它的 XADD
// 全部因为"ID 不大于流顶端"而失败。表现是那一轮完全没有日志，而且只在多副本轮转时
// 才出现，本地怎么测都测不到。
//
// 调这个函数的时候执行权锁已经在手，所以不存在两个 pod 同时 seed 的问题。
//
// 读失败时保守地不动计数器：本轮的日志大概率写不进去（会走降级删流），
// 而那只影响续传，不影响回答本身。
func (s *AgentSession) seedEventSeq() {
	s.eventLog.Reset()
	if s.eventLog == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	last, err := s.logs.LastSeq(ctx, s.id)
	if err != nil {
		log.Printf("event_seq_seed_failed session=%s err=%v —— 本轮可能没有续传日志", s.id, err)
		return
	}
	// 只前进不后退：本进程如果已经数得更高（同一个 pod 连着跑两轮），
	// 退回去会造成 ID 冲突。
	for {
		cur := s.eventSeq.Load()
		if last <= cur || s.eventSeq.CompareAndSwap(cur, last) {
			return
		}
	}
}

// PlanControl 是一次请求携带的两个计划相关信号。它们是**不同的东西**，所以分成
// 两个字段而不是一个枚举：
//
//	Mode    这一轮跑在规划态还是执行态（引擎原子量，turn 内生效）
//	Action  用户对**已提交的那份计划**表了什么态（批准 / 否决 / 没表态）
//
// 一条消息可以只有其中一个：用户点"同意"是 Mode=false + Action=approve；
// 用户在执行中途又切回规划是 Mode=true + Action=""（旧计划原样留着）。
type PlanControl struct {
	Mode   bool
	Action string
}

// plan_action 的取值。空串是合法的"没表态"，不是缺省错误 —— 绝大多数消息都是它。
const (
	PlanActionNone    = ""
	PlanActionApprove = "approve"
	PlanActionReject  = "reject"
)

func (s *AgentSession) Send(message string, attachmentIDs []int64, plan PlanControl) error {
	// turn 级可取消 ctx —— Stop() 靠它掐断。不能用 context.Background()：
	// 引擎的 collectAssistantMessage / 工具层 httpClient 全是 ctx-aware，
	// 但没人持有 cancel 时那些 select <-ctx.Done() 永远不会触发，一旦
	// RunTurn 起飞就得等 MaxIterations 跑完（实测 20+ 轮、几分钟）。
	s.Touch()

	// 计划模式跟专家团不兼容，而且不是"没做"，是结构性做不到 —— 所以在入口拒掉，
	// 不能静默忽略（那样用户以为自己在规划态，agent 已经在改东西了）。
	//
	// 三条，每一条单独都足以让它不成立：
	//
	//  1. handoff 团里 s.engine 是 nil（那条链每一棒都是引擎现建的），而
	//     SetPlanMode 会解引用它 —— 实测直接 panic，且 Send 的 goroutine 里没有
	//     recover，是整个进程挂掉。
	//  2. 引擎的 Config **没有** PlanMode 字段：它是 repl 会话上的运行时原子量。
	//     每条 leg 由 deriveExpertBase 从 Config 派生，所以计划模式传不下去。
	//  3. save_plan 走 Options.ExtraTools，而 deriveExpertBase 里有一句
	//     `opts.ExtraTools = nil` —— 团里没有任何一棒拿得到这个工具。
	//
	// 概念上也重复：handoff 链的拓扑本身就是一份计划，配置时就定好了。再叠一层
	// 每轮的计划等于两份计划各说各的。要支持得先给引擎加 Config.PlanMode 并接到
	// 每条 leg 上，那是另一个量级的事。
	if err := s.planSupported(plan); err != nil {
		return err
	}

	// 抢执行权必须同步做 —— 起了 goroutine 就没法把 409 回给调用方了。
	handle, err := s.acquireTurnLock()
	if err != nil {
		return err
	}

	ctx, cancel := context.WithCancel(context.Background())
	s.turnMu.Lock()
	s.turnCancel = cancel
	s.turnEpoch++
	s.turnStopped = false
	myEpoch := s.turnEpoch
	s.turnMu.Unlock()

	go func() {
		// 最先声明 = 最后执行：执行权要持到收尾（落库）真的做完，否则另一个 pod
		// 可能在我们还在 INSERT 的时候就开始写同一段历史。
		defer handle.Release()
		defer func() {
			cancel()
			s.turnMu.Lock()
			if s.turnEpoch == myEpoch {
				s.turnCancel = nil
			}
			s.turnMu.Unlock()
		}()
		// 必须排在第一次 broadcast 之前 —— 序号一旦发歪，这一整轮的日志都写不进去。
		s.seedEventSeq()
		s.broadcast(SSEEvent{Type: "start", Data: nil})

		// H1: 起新 collector，同时 touch session 行（首次自动 INSERT）
		s.collectorMu.Lock()
		s.collector = newCollector()
		s.collectorMu.Unlock()

		db, dbErr := persistence.DB()
		if dbErr != nil {
			log.Printf("db_unavailable err=%v (persistence disabled for this turn)", dbErr)
		}

		// H4: 附件文本注入 — 拉 chat_attachments 拼进 message（read-only，与 Python chat.py 对齐）
		userMsg := message
		if db != nil && len(attachmentIDs) > 0 {
			atts, err := persistence.LoadAttachments(ctx, db, s.tenantID, attachmentIDs)
			if err != nil {
				log.Printf("load_attachments_failed session=%s ids=%v err=%v", s.id, attachmentIDs, err)
			} else {
				userMsg = persistence.BuildContentWithAttachments(message, atts)
				message = userMsg
			}
		}

		if db != nil {
			if err := persistence.TouchSession(ctx, db, s.id, s.userID, s.tenantID, userMsg); err != nil {
				log.Printf("touch_session_failed session=%s err=%v", s.id, err)
			}
		}

		// ── Phase 1: Pre-turn 画像+记忆注入 ──
		if s.memory != nil {
			memories := s.memory.PreTurn(ctx, message)
			if memories != "" {
				message = "[系统提示 - 用户相关信息]\n" + memories + "\n\n[用户消息]\n" + message
			}
		}

		// ── Phase 1b: Plan 模式开关 + 计划状态流转 + 执行计划注入 ──
		//
		// SetPlanMode 是引擎原子量，turn 内生效，不持久化：请求带来什么就设什么，
		// 下一个请求（可能落在另一个 pod）自带它自己的值。plan_mode=true 时 prompt
		// 引导模型只做调研；plan_mode=false 时恢复执行态。
		//
		// ⚠️ 引擎的权限层也会跟着翻（perms.SetPlanMode），但它只挡得住引擎原生的
		// Write/Edit/Bash —— 平台工具（code_interpreter / memory_add / 网关动态工具）
		// 在 DefaultResolver 里全部解析成 OperationUnknown，而 isMutatingAction 对
		// unknown 返回 false，所以规划态下它们照样能跑。要真挡住得给引擎传一个自定义
		// Resolver，那是另一件事，不在这轮改动范围内。
		//
		// nil 判断不是多余的：handoff 团里 engine 就是 nil（见字段注释），而
		// SetPlanMode 会解引用它 —— 实测 panic，且这里是 goroutine 内、没有 recover，
		// 整个进程跟着走。Send 入口已经用 ErrPlanModeUnsupported 拦了带 plan_mode 的
		// 请求，这一道是防"以后有人加了别的调用路径"。
		if s.engine != nil {
			s.engine.SetPlanMode(plan.Mode)
		}

		// 计划状态流转：由请求里的 plan_action 显式驱动。
		//
		// 以前这里是「plan_mode=false 就 ActivatePlan」—— 从一个不相干的事件里推断
		// "用户批准了"。用户回一句"算了不用了"照样会把 draft 激活，然后那份没人要的
		// 计划会被注入到这个会话之后的每一轮。
		if db != nil {
			if err := applyPlanAction(ctx, db, s.tenantID, s.id, plan); err != nil {
				log.Printf("plan_action_failed session=%s action=%q err=%v", s.id, plan.Action, err)
			}
		}

		// 执行阶段注入的是**一行指针**，不是计划全文。
		//
		// 全文每轮拼一次的代价是实打实的：引擎的 store 落的是它收到的那条
		// llm.Message，也就是拼过前缀的那条，所以每一轮的 user 消息里都有一份完整
		// 计划，ReloadHistory 读回 20 轮就是 20 份。一份 1500 token 的计划 × 20 轮
		// ≈ 3 万 token 纯重复，而且这个平台没有可继承的 prompt cache，每轮全价。
		//
		// 换成指针之后全文由模型按需调 get_plan 取，那份结果声明了 Reproducible +
		// Identity，陈旧的会被引擎脱水、旧的那份会被新的取代，所以历史里始终只有
		// 最新一份。指针本身三十来个 token，堆二十份也无所谓。
		//
		// 只读**已批准**的那份：待批准的修订不进指针，否则用户还没点同意，模型已经
		// 按新计划跑了。
		//
		// 失败只记日志不拒服：DB 读不到就用原始 message，是降级不是不可用。
		if !plan.Mode && db != nil {
			if p, err := persistence.LoadPlan(ctx, db, s.tenantID, s.id); err != nil {
				log.Printf("load_plan_failed session=%s err=%v (本轮不注入计划)", s.id, err)
			} else if hint := planHint(p); hint != "" {
				message = hint + "\n\n[用户消息]\n" + message
			}
		}

		// ── Phase 2: 执行 ──
		//
		// 两条拓扑在这里分岔，往下的收尾（历史、用量、SSE、落库）完全共用：runChain 把
		// 一条链的结果整形成 turn.Result 的形状，因为下游关心的东西两者都有 —— 最终
		// 文本、逐轮用量、迭代数。分岔只在"谁跑"，不在"跑完怎么处理"。
		var assistantText string
		var runErr error
		var result turn.Result
		var err error
		if s.chainCfg != nil {
			result, err = s.runChain(ctx, message)
		} else {
			// 星型/单 agent：跑之前把历史从库里重读一遍。
			//
			// 这个会话对象被 Manager 按 session_id 缓存并跨请求复用，而它内部的引擎会话
			// 只在 Build 那一刻读过一次库。多副本下这条会静默出错：中间某一轮落到别的
			// pod，那一轮进了 agent_messages 但**没进本进程的内存**，于是下一轮再落回来
			// 时，模型拿着缺了一轮的历史回答，然后把自己这轮也写进去 —— 库里那段对话就
			// 成了两个写者交错的产物。
			//
			// 表现是"模型忘了刚才说过的话"，跟持久化整个没生效长得一模一样，排查时很容易
			// 以为是回退。
			//
			// 接力型不需要这一步：runChain 每轮都由引擎重新建 leg，天然每轮重载。
			//
			// 失败只记日志不拒服：读不到就用内存里那份，是降级不是不可用。
			if err := s.engine.ReloadHistory(ctx); err != nil {
				log.Printf("history_reload_failed session=%s err=%v (沿用内存中的历史)", s.id, err)
			}
			result, err = s.engine.RunTurn(ctx, message)
		}
		switch {
		case err != nil && errors.Is(err, context.Canceled):
			// 用户主动停：不是错误。已经流出去的 delta 就是这轮的成果，
			// 从 collector 取回当正文落库，前端刷新后仍能看到停在哪里。
			assistantText = s.currentCollector().responseText()
			s.mu.Lock()
			s.history = append(s.history,
				runtime.MessagePair{Role: "user", Content: userMsg},
				runtime.MessagePair{Role: "assistant", Content: assistantText},
			)
			s.mu.Unlock()
			inTok, outTok := sumUsages(result.Usages)
			s.recordTurnUsage(result)
			s.broadcast(SSEEvent{Type: "result", Data: map[string]any{
				"text":            assistantText,
				"iterations":      result.Iterations,
				"stopped":         true,
				"inputTokens":     inTok,
				"outputTokens":    outTok,
				"peakInputTokens": peakInputTokens(result.Usages),
			}})
		case err != nil:
			runErr = err
			// 失败也要记用量 —— 花掉的钱不因为这轮失败而退回。此前这条分支不记，于是一轮
			// 跑到一半失败的对话在库里 token_count 是空的，而它可能已经烧了几十万 token
			// （handoff 链中途某棒预算耗尽就是这个形状：前面几棒全跑完了）。
			//
			// 这种丢失没有可见症状：前端照常显示错误，只有对账时才发现账单少了一笔。
			s.recordTurnUsage(result)
			s.broadcast(SSEEvent{Type: "error", Data: err.Error()})
		default:
			assistantText = extractText(result.FinalAssistant)

			// 记录对话历史（供 SessionEnd 批量处理）
			s.mu.Lock()
			s.history = append(s.history,
				runtime.MessagePair{Role: "user", Content: userMsg},
				runtime.MessagePair{Role: "assistant", Content: assistantText},
			)
			s.mu.Unlock()

			inTok, outTok := sumUsages(result.Usages)
			s.recordTurnUsage(result)

			resultData := map[string]any{
				"text":            assistantText,
				"iterations":      result.Iterations,
				"stopped":         result.Stopped,
				"inputTokens":     inTok,
				"outputTokens":    outTok,
				"peakInputTokens": peakInputTokens(result.Usages),
			}
			// 追问标记：前端据此区分"正常完成"和"等用户回答"。
			if result.AwaitingUser {
				resultData["awaitingUser"] = true
			}
			s.broadcast(SSEEvent{Type: "result", Data: resultData})

			// 追问事件：携带问题和选项，前端渲染交互 UI。
			// 在 result 之后、done 之前播，保证前端在收到 done 之前已经拿到追问数据。
			// AskUser 只做一件事：把问题和选项播给前端。它**不碰 plan 表**。
			//
			// 这里以前还顺手 SavePlan(question)，那是把两个信号焊在了一个事件上：
			// "这是一份计划" 和 "停下来等用户"。后果是规划一轮里模型先调
			// save_plan(完整计划) 再调 AskUser("是否同意？")，收尾时这段把那句一行
			// 的追问**覆盖**掉刚存好的正文，执行阶段注入的"计划"就成了"是否同意？"。
			//
			// 而那个 `if planMode` 守卫本身就是耦合的症状：正因为 AskUser 会写 plan
			// 表，才必须判断"现在是不是规划阶段"，否则执行中的普通追问会污染计划。
			// 解耦之后这个判断不需要了 —— save_plan 是计划的唯一写入口。
			if result.AwaitingUser {
				if q, opts := extractAskUserPayload(result.FinalAssistant); q != "" {
					s.broadcast(SSEEvent{Type: "ask_user", Data: map[string]any{
						"question": q,
						"options":  opts,
					}})
				}
			}
		}

		// H1: 落库 user + assistant 两条消息（含 extra 可视化）
		//
		// 用独立 ctx 而不是 turn 的 ctx：用户点停之后 turn ctx 已 canceled，
		// 拿它去 INSERT 会被 database/sql 直接拒（"context canceled"），
		// 结果恰好在最该留痕的场景下什么都没落库。取消只该掐断 LLM 和工具，
		// 不该掐断收尾。
		persistCtx, persistCancel := context.WithTimeout(context.Background(), 15*time.Second)
		s.persistTurn(persistCtx, userMsg, assistantText, runErr)
		persistCancel()

		s.broadcast(SSEEvent{Type: "done", Data: nil})
	}()
	return nil
}

// recordTurnUsage 把一轮的用量交给 collector，按拓扑走对应的那条路。
//
// 抽出来是因为两个调用点（正常结束、用户主动停）要做同样的判断，而漏掉一个的后果是
// 静默的：账单虚高，看起来只是个正常数字。
func (s *AgentSession) recordTurnUsage(result turn.Result) {
	in, out := sumUsages(result.Usages)
	if s.chainCfg != nil {
		// 链没有主会话，每条 leg 的用量已经各自通过 bridge 进了 agentUsages。
		// 再把 in/out 填进 main 就是把 leg 计两次 —— 见 setChainUsage。
		s.currentCollector().setChainUsage(result.Iterations, peakInputTokens(result.Usages), perRoundUsage(result.Usages))
		return
	}
	s.currentCollector().setMainUsage(in, out, peakInputTokens(result.Usages), result.Iterations, perRoundUsage(result.Usages))
}

// runChain 跑一条 handoff 链，把结果整形成 turn.Result 的形状。
//
// 整形而不是让上面分两套收尾逻辑：下游要的三样东西（最终文本、逐轮用量、迭代数）两种
// 拓扑都有，只是来源不同 —— 星型来自一个会话的多轮，handoff 来自多个会话各自的多轮。
// 用量按 leg 顺序拼接，所以 sumUsages / peakInputTokens / perRoundUsage 不用改：
// peak 仍然是"单次请求最大的那一次"，跨 leg 取最大恰好是对的。
//
// FinalAssistant 只填文本块。链的最终答案是验收专家的最后一条消息，而它的工具调用块
// 属于那个 leg 的会话历史（已经随 leg 关闭而消失），带过来会让 extractText 之外的
// 消费者看到指向不存在会话的 tool_use。
func (s *AgentSession) runChain(ctx context.Context, message string) (turn.Result, error) {
	chain, err := engine.RunChain(ctx, *s.chainCfg, s.chainOpts, message)

	result := turn.Result{
		Usages:  chain.Usages,
		Stopped: chain.Stopped,
		// 迭代数 = 全链所有请求次数。它回答的是"这一次对话花了多少轮模型调用"，
		// 跟星型同义；leg 数另外单独播（chain_hop），两个数不该混。
		Iterations:   len(chain.Usages),
		AwaitingUser: chain.AwaitingUser,
		FinalAssistant: llm.Message{
			Role:    llm.RoleAssistant,
			Content: []llm.ContentBlock{{Type: llm.ContentBlockTypeText, Text: chain.Final}},
		},
	}

	// 追问场景：链因为某个专家调了 AskUser 而停下。问题和选项在 History 最后的
	// assistant 消息的 tool_use 块里。把它追加到 FinalAssistant，这样 Send 能
	// 从中提取 question/options 播 ask_user 事件。
	if chain.AwaitingUser {
		if block, ok := findAskUserToolUse(chain.History); ok {
			result.FinalAssistant.Content = append(result.FinalAssistant.Content, block)
		}
	}

	// 每一棒播一条事件。没有它前端只能看到一堆工具卡而不知道换过人 —— 链是平的，
	// 事件里没有父子关系可以让前端推断出分段（见 collector 的 parentToolUseID）。
	for i, leg := range chain.Legs {
		s.broadcast(SSEEvent{Type: "chain_hop", Data: map[string]any{
			"index":        i + 1,
			"expert":       leg.Expert,
			"role":         string(leg.Role),
			"handedTo":     leg.HandedTo,
			"inputTokens":  leg.Usage.InputTokens,
			"outputTokens": leg.Usage.OutputTokens,
			"durationMs":   leg.Usage.DurationMs,
		}})
	}
	if chain.DivertedToExit {
		// 兜底过就说出来：链跑完了，但这个团的 worker 不知道下一棒该给谁（或者点了个
		// 不存在的名字）。不记的话运维只看到一条正常完成的对话，而这是 roster 或提示词
		// 该改的信号。
		log.Printf("chain_diverted_to_exit session=%s legs=%d (worker 没指定下一棒或指定了不存在的专家)",
			s.id, len(chain.Legs))
	}
	if err != nil {
		// 中途失败：哪一棒、为什么。预算耗尽（max react iterations）是最常见的一种，
		// 对应的动作是调高那个专家的 max_turns 或者把活拆小。
		//
		// 产物不会因此丢：leg 的工具事件已经沿 bridge 流出去落库了，workspace 里的文件
		// 也还在，用户在历史记录里看得到做到哪一步 —— 只是没有人替他判断"这算不算完成"。
		log.Printf("chain_failed session=%s legs=%d err=%v", s.id, len(chain.Legs), err)
	}
	log.Printf("chain_done session=%s legs=%d diverted=%v stopped=%v",
		s.id, len(chain.Legs), chain.DivertedToExit, chain.Stopped)

	// 留给落库用。链现在是一整段对话，落的是引擎自己产出的那份消息 —— 里面有
	// handoff 的 tool_use 和 tool_result，下一轮读回来时交接的痕迹还在。
	// 存在字段上而不是塞进 turn.Result：那是引擎的类型，不该为这个宿主加字段。
	s.mu.Lock()
	s.lastChain = &chain
	s.mu.Unlock()

	return result, err
}

// Stop 掐断当前 turn。返回 false 表示当轮已经结束（没什么可停）。
//
// 取消信号沿 ctx 一路传：引擎的 collectAssistantMessage 在 select <-ctx.Done()
// 上立刻返回，正在跑的工具 —— 包括 code_interpreter 走网关那次 HTTP —— 也随
// req 的 ctx 中断。沙箱里已经起来的进程不受影响（那是远端 lea-tools 的事），
// 但引擎不会再等它，也不会进下一轮迭代。
func (s *AgentSession) Stop() bool {
	s.turnMu.Lock()
	cancel := s.turnCancel
	if cancel != nil {
		s.turnStopped = true
	}
	s.turnMu.Unlock()
	if cancel == nil {
		return false
	}
	cancel()
	return true
}

// currentCollector 拿当前 turn 的 collector；未 Send 前调用返回 no-op 实例。
func (s *AgentSession) currentCollector() *messageCollector {
	s.collectorMu.Lock()
	defer s.collectorMu.Unlock()
	if s.collector == nil {
		s.collector = newCollector()
	}
	return s.collector
}

func (s *AgentSession) persistTurn(ctx context.Context, userMsg, assistantMsg string, runErr error) {
	db, err := persistence.DB()
	if err != nil || db == nil {
		return
	}
	c := s.currentCollector()

	var msgs []persistence.Message
	if userMsg != "" {
		msgs = append(msgs, persistence.Message{
			SessionID: s.id, Role: "user", Content: userMsg,
		})
	}
	// runErr 时 assistantMsg 可能为空，但仍写一条 error 记录能让前端看到
	if assistantMsg == "" && runErr != nil {
		assistantMsg = fmt.Sprintf("[执行失败] %v", runErr)
	}
	// 用户在模型还没吐字时就点了停 —— 也得留一条，否则这一轮在历史里
	// 只剩用户消息，刷新后看起来像"发出去没反应"。
	s.turnMu.Lock()
	stopped := s.turnStopped
	s.turnMu.Unlock()
	if assistantMsg == "" && stopped {
		assistantMsg = "[已中断]"
	}
	if assistantMsg != "" {
		// token_count 列填全链路输入+输出之和。这一列在此之前**一直是 0** —— 字段和
		// 写库逻辑都在（persistence/messages.go:94），但没有一处赋值。
		//
		// 单列存不下计费需要的全部维度（输入/输出/峰值/逐个子会话），细账在
		// extra.usage 里；这一列保留"这条消息花了多少 token"的粗粒度语义，便于直接
		// SUM 出账单，不必解 JSON。
		totalIn, totalOut := c.usageTotals()
		total := totalIn + totalOut
		msgs = append(msgs, persistence.Message{
			SessionID: s.id, Role: "assistant", Content: assistantMsg,
			TokenCount: &total,
			Extra:      c.toExtra(),
		})
	}
	if err := persistence.SaveMessages(ctx, db, msgs...); err != nil {
		log.Printf("save_messages_failed session=%s err=%v", s.id, err)
	}

	// agent_messages（给模型的那份）在两条拓扑上由不同的人写，互斥不重叠：
	//
	//   星型 —— 引擎自己写（PersistSession=true + opts.Backend）。落的是引擎原生
	//           消息，含 tool_use / tool_result，保真度最高。
	//   handoff —— 引擎写不了：每一棒都是 PersistSession=false 的临时会话，跑完就
	//           销毁，RunChain 也不把各棒的消息交出来。所以这里补写。
	//
	// 只补用户消息和最终回答，不补各棒的内部往来 —— 一条链的**对话**就是这两条，
	// 中间换棒是实现细节。把上一棒的 tool_use 块重放给下一棒本身也说不通：那些块
	// 属于另一个专家的会话。
	if s.chainCfg != nil {
		s.appendChainHistory(ctx, db, runErr)
	}
}

// appendChainHistory 把一整条链新产生的那段对话落到 agent_messages。
//
// 落的是引擎产出的真消息，不是重新拼的一问一答。这是这轮改造的落点：链在引擎里已经
// 是一整段对话了，每一棒的 assistant 消息、handoff 的 tool_use 和 tool_result 都在
// 里面，原样落下去下一轮才读得回同一段东西。以前这里造一对 user/assistant 文本，
// 结果是**给模型看的历史和链真正跑过的历史不是一回事** —— 交接痕迹没了，工具调用
// 没了，第二轮的链只知道"上次有人回答过一句话"。
//
// 只落 History[PriorLen:]：前面那截是这条链启动时从库里读出来的，已经在表里了。
//
// 失败的轮次整轮不落：链跑挂时最后那棒可能停在半路，把半截东西灌回给模型会让它
// 把没做完的事当做过。chat_messages 那份仍然写 —— 用户该在历史里看到出过错。
func (s *AgentSession) appendChainHistory(ctx context.Context, db *sql.DB, runErr error) {
	if runErr != nil {
		return
	}
	s.mu.RLock()
	chain := s.lastChain
	s.mu.RUnlock()
	if chain == nil {
		return
	}
	fresh, ok := freshChainMessages(chain)
	if !ok {
		log.Printf("chain_history_bad_prior session=%s prior=%d len=%d —— 本轮不落",
			s.id, chain.PriorLen, len(chain.History))
		return
	}
	if len(fresh) == 0 {
		return
	}

	store, err := persistence.NewEngineBackend(db, s.tenantID, s.ctxBudget).OpenStore(ctx, s.id)
	if err != nil {
		log.Printf("chain_history_store_failed session=%s err=%v", s.id, err)
		return
	}
	defer store.Close(ctx)

	if err := store.Append(ctx, fresh); err != nil {
		// 降级而不是拒服：这一轮的回答已经给了用户，只是下一轮模型会少这段上下文。
		log.Printf("chain_history_append_failed session=%s msgs=%d err=%v —— 下一轮会缺这段上下文",
			s.id, len(fresh), err)
	}
}

// freshChainMessages 取一条链本次新产生的消息 —— History 去掉开头那截"跑之前就有的"。
//
// ok=false 表示 PriorLen 不可信，调用方整轮不落。引擎那边已经夹过一次，这里再挡是因为
// 出错的两种方向代价差得远：多写一段会让下一轮把同一段对话读两遍（模型看到自己复读），
// 少写不了什么；而 PriorLen 超出长度会直接 panic 在落库的收尾 goroutine 上。
//
// 单独拎出来是为了能不连库测。这段的判断（切在哪、越界怎么办）跟 SQL 没关系，混在
// 需要 *sql.DB 的方法里就只能靠真机跑到才发现切错了。
func freshChainMessages(chain *engine.ChainResult) ([]llm.Message, bool) {
	if chain == nil {
		return nil, true
	}
	if chain.PriorLen < 0 || chain.PriorLen > len(chain.History) {
		return nil, false
	}
	return chain.History[chain.PriorLen:], true
}

func (s *AgentSession) Subscribe() chan SSEEvent {
	ch := make(chan SSEEvent, 128)
	s.mu.Lock()
	s.subscribers[ch] = struct{}{}
	s.mu.Unlock()
	s.Touch() // 挂上 SSE 就算活跃，别在用户盯着流的时候把会话清了
	return ch
}

func (s *AgentSession) Unsubscribe(ch chan SSEEvent) {
	s.mu.Lock()
	delete(s.subscribers, ch)
	s.mu.Unlock()
}

func (s *AgentSession) Close() {
	// SessionEnd: 批量提取记忆 + merge 画像
	if s.memory != nil {
		s.mu.RLock()
		history := make([]runtime.MessagePair, len(s.history))
		copy(history, s.history)
		s.mu.RUnlock()
		if len(history) > 0 {
			go s.memory.OnSessionEnd(context.Background(), history, s.id)
		}
	}
	if s.engine != nil {
		s.engine.Close(context.Background())
	}
	// 日志写入器停掉，但**不删流** —— TTL 到了自然清。会话被空闲淘汰时那一轮
	// 早就结束了，而流还留着的话，正好赶上重连的客户端仍能把结尾补齐。
	s.eventLog.Close()
	s.mu.Lock()
	for ch := range s.subscribers {
		close(ch)
	}
	s.subscribers = make(map[chan SSEEvent]struct{})
	s.mu.Unlock()
}

// broadcast 是所有 SSE 事件的唯一出口 —— 发序号、写事件日志、扇出给订阅者都在这里。
//
// 放在这一个漏斗里是有意的：十来处 broadcast 调用点如果各自负责这些，漏掉一处的
// 表现是"某类事件续传不了"，而且只在断线重连时才暴露。
func (s *AgentSession) broadcast(ev SSEEvent) {
	// 序号在本地发，不等 Redis —— 广播这一刻就知道 id，SSE 写入零阻塞。
	// 从 1 开始：Redis 的流条目 ID 不接受 0-0。
	ev.Seq = s.eventSeq.Add(1)

	// 渲染一次，直播和重放共用同一份字节。原始对象留在 ev.Data 里给内存订阅者用，
	// 渲染结果只进日志 —— 两条路最终都过 gin 的 sse 编码器，格式一致。
	s.eventLog.Append(eventlog.Entry{Seq: ev.Seq, Type: ev.Type, Data: RenderSSEData(ev.Data)})

	s.mu.RLock()
	defer s.mu.RUnlock()
	for ch := range s.subscribers {
		select {
		case ch <- ev:
		default:
		}
	}
}

// RenderSSEData 把事件载荷渲染成 SSE 的 data 字段内容，规则跟 gin 的 sse 编码器一致：
// struct / slice / map 走紧凑 JSON，其余走 fmt.Sprint（于是 nil 渲染成 "<nil>"，
// 跟今天线上的行为一样）。
//
// 必须跟 gin 对齐，否则重放出来的载荷格式跟直播不同 —— 前端对 delta 是直接取字符串、
// 对 tool_event 是 JSON.parse，格式一变两边都会崩，而且只在断线重连时才发生。
//
// 渲染后的字符串再交回 gin 时会走 fmt.Sprint 那条分支原样写出，所以逐字节等价
// （紧凑 JSON 内部没有换行，不会被 gin 的多行 data: 拆分逻辑改动）。
func RenderSSEData(data any) string {
	// 照抄 gin 的 kindOfData：指针要先解引用再判断，所以 *struct 走 JSON、
	// *int 走 fmt.Sprint。不解引用的话这两类会分岔。
	v := reflect.ValueOf(data)
	kind := v.Kind()
	if kind == reflect.Ptr {
		kind = v.Elem().Kind()
	}
	switch kind {
	case reflect.Struct, reflect.Slice, reflect.Map:
		b, err := json.Marshal(data)
		if err != nil {
			// 编不出来就退回文本形式。丢格式好过丢事件 —— 少一条 delta 就是正文缺一段。
			log.Printf("sse_render_failed type=%T err=%v", data, err)
			return fmt.Sprint(data)
		}
		return string(b)
	default:
		return fmt.Sprint(data)
	}
}

func (s *AgentSession) forwardToolEvents(ch <-chan tool.Event) {
	for ev := range ch {
		s.currentCollector().onToolEvent(ev)
		s.broadcast(SSEEvent{Type: "tool_event", Data: ev})
	}
}

func extractText(msg llm.Message) string {
	var parts []string
	for _, block := range msg.Content {
		if block.Type == llm.ContentBlockTypeText && block.Text != "" {
			parts = append(parts, block.Text)
		}
	}
	return strings.Join(parts, "\n")
}

// perRoundUsage 把每一轮 LLM 调用的用量摊开成逐条记录。
//
// 为什么需要逐轮而不只是合计：合计答不了"钱花在哪"。一个 turn 的累计输入是
// N 轮历史的叠加（第 k 轮的输入包含前 k-1 轮的全部内容），所以 61 万这个数里，
// **同一份内容被重复计了很多次**。要判断"把 skill 正文挡在专家上下文外"这类改动
// 到底省了多少，得看每轮输入的增长曲线：载荷进上下文越早，被重传的次数越多，
// 真实成本 = 载荷 × 它之后还剩多少轮。
//
// 实测这个缺口很大：工具调用的载荷去重后只有 8.8 万字节（约 22k tok），而全链路
// 累计输入 61.5 万 tok —— 差 28 倍全在重传上。没有逐轮数据时那个乘数只能推算。
//
// 缓存字段一并记：命中缓存的输入 token 计价不同，合计里混在一起就算不出真实费用。
func perRoundUsage(usages []*llm.Usage) []map[string]any {
	rounds := make([]map[string]any, 0, len(usages))
	for i, u := range usages {
		if u == nil {
			continue
		}
		r := map[string]any{
			"round":        i + 1,
			"inputTokens":  u.InputTokens,
			"outputTokens": u.OutputTokens,
		}
		// 只在非零时落，避免每轮都带两个 0 把记录撑大一倍。
		if u.CacheCreationInputTokens > 0 {
			r["cacheCreationInputTokens"] = u.CacheCreationInputTokens
		}
		if u.CacheReadInputTokens > 0 {
			r["cacheReadInputTokens"] = u.CacheReadInputTokens
		}
		// cachedInputTokens 是 inputTokens 里被缓存命中的那部分（OpenAI 口径：缓存算在
		// 输入里，不是额外加项，所以**不能**跟上面两个相加）。
		//
		// ⚠️ 当前中转平台（LEA_TENANT_API_BASE）不上报 prompt_tokens_details，流式和
		// 非流式都已真机确认，所以这一列在本链路**恒为空**。看到它全空是预期，不是 bug，
		// 不用再去查一遍。它为两种情况存在：上游哪天补报，或者换成会报的 provider
		// （OpenAI 官方 / Azure / DeepSeek 都报）。
		//
		// 也就是说"缓存有没有生效"目前在这条链路上无法从 usage 观测 —— 全命中和全价
		// 报出来的 inputTokens 一模一样。想测得先让上游给这个字段。
		if u.CachedInputTokens > 0 {
			r["cachedInputTokens"] = u.CachedInputTokens
		}
		// 相对上一轮的输入增量 —— 这一轮真正"新增"了多少上下文。历史重传的部分
		// 不算在里面，所以这列的和才是这个 turn 实际产生的内容量。
		if i > 0 && usages[i-1] != nil {
			r["inputDelta"] = u.InputTokens - usages[i-1].InputTokens
		}
		rounds = append(rounds, r)
	}
	return rounds
}

// sumUsages 累加一个 turn 里所有 LLM 轮次的 token。
//
// **这是主会话唯一的用量出口。** 在此之前主会话的 token 一处都没记：SSE 的
// agent_usage 只由子代理（launcher.go）和专家（expert_runner.go）在各自结束时发，
// 落库的 extra 里也没有。所以"一个不派活的 agent 花了多少 token"这个问题以前答不上来
// —— 做单 agent 对照实验时才发现，对照组的用量统计全是 0，而那正是要比的那一半。
//
// 注意这是**累计值**，不是单次请求的上下文占用。同一个 turn 跑 20 轮，每轮的输入都
// 包含此前的完整历史，所以累加出来的数远大于任何一次的实际上下文。要看峰值上下文得
// 逐次记录 Usages 里的最大 InputTokens，引擎的 Result 里有这个数据，但那是另一个指标，
// 不要拿这里的和去当峰值。
func sumUsages(usages []*llm.Usage) (in, out int) {
	for _, u := range usages {
		if u != nil {
			in += u.InputTokens
			out += u.OutputTokens
		}
	}
	return in, out
}

// peakInputTokens 返回单次 LLM 请求的最大输入 token —— 即这个 turn 的峰值上下文占用。
//
// 跟 sumUsages 的和分开报，因为两者回答的是不同问题：累计和衡量花了多少钱，峰值衡量
// 离撞上下文窗口还有多远。"把 skill 执行下沉到子代理"这类改动的收益主要体现在峰值上
// （主会话不再吃 skill 正文和执行过程），累计和反而可能变大（多了一层会话的 prompt
// 开销）。只看和会得出相反的结论。
func peakInputTokens(usages []*llm.Usage) int {
	peak := 0
	for _, u := range usages {
		if u != nil && u.InputTokens > peak {
			peak = u.InputTokens
		}
	}
	return peak
}

// fallbackContextWindow 是配置里没给出窗口时的兜底。
//
// 刻意取小，而且比这里曾经的 128_000 小得多。理由是**它现在是兜底，不是典型值**：
// 它只在 lea-config 没下发 model_context_window 时才生效，也就是"我们不知道这是什么
// 模型"的时候。那种情况下猜大的代价是在模型真实上限处整轮失败（用户可见），猜小的
// 代价只是早压一次（一次摘要调用）。
//
// 原来那个 128_000 的问题不是数值不对，是它同时在当"典型值"和"兜底"用 —— 于是给
// qwen3-8b（真实窗口 32k）也发 128k，Reduce 永远不触发然后直接撞墙。
const fallbackContextWindow = 32_000

// costCeilingTokens 是成本闸：我们愿意为一轮**输入**付多少 token。0 = 不设闸。
//
// 为什么它跟模型窗口是两个数：窗口是模型的客观事实，成本是产品决策。压成一个的后果是
// "换了个大窗口的模型，账单自己涨了"，而没人做过那个决定。
//
// 具体到 qwen3-max，阿里按输入长度分档计价（0–32K $0.861/M，32–128K $1.434/M，
// 128–252K $2.151/M），所以让上下文长到窗口上限是 token 数和单价**同时**涨，超线性。
// 而这个平台每轮都重新加载历史、没有可继承的 prompt cache，是全价重发。
//
// 现在留空：先按模型窗口走，要收紧时把这个数填上即可，形状已经在了。
const costCeilingTokens = 0

// engineBudgetRatio 把"模型窗口"换算成引擎的 MaxContextTokens。
//
// 引擎在 MaxContextTokens × compactionThresholdRatio 处触发压缩，而那个 0.8 是引擎的
// **包级常量，改不了**。我们想让压缩落在真实窗口的 70%，所以 0.7 / 0.8 = 0.875。
//
// 也就是说 MaxContextTokens **不是**触发线，它是触发线除以 0.8 之后的那个数。这两者
// 差 25%，看混了就会以为压缩比实际更早或更晚。
const engineBudgetRatio = 0.875

// contextBudget 由模型窗口和成本闸算出引擎的上下文预算。
//
// modelWindow 来自 lea-config 下发的 AgentConfig.ModelContextWindow —— 模型和它的参数
// 都是外部传进来的，窗口没有理由例外。这里不再有"lea-agent 认识哪些模型"的表：那种表
// 在模型名多一个后缀时会静默失效，而且换模型要改代码、发版。
func contextBudget(modelWindow, costCeiling int) int {
	// 小于 minPlausibleWindow 的值当成"没配"，不是当成"这个模型真的这么小"。
	//
	// 不加这道的后果很具体：窗口填 1 时 int(1 × 0.875) == 0，而 0 在引擎那边的含义是
	// **把压缩整个关掉**（`if s.maxContextTokens <= 0 { return }`）。也就是说一个手滑
	// 填小的配置不会让压缩变激进，会让它彻底消失 —— 而且没有任何报错。
	// 零值和"小得离谱"必须走同一条兜底路径，否则这个函数在边界上是反直觉的。
	if modelWindow < minPlausibleWindow {
		modelWindow = fallbackContextWindow
	}
	if costCeiling >= minPlausibleWindow && costCeiling < modelWindow {
		modelWindow = costCeiling
	}
	return int(float64(modelWindow) * engineBudgetRatio)
}

// minPlausibleWindow 是"这看起来像个真的上下文窗口"的下限。
// 比任何在用的模型都小（最小的 qwen3-8b 也有 32k），所以正常配置不会被它捕获。
const minPlausibleWindow = 4_096

// findAskUserToolUse 从会话历史中找最后一个 AskUser tool_use 块。
// 追问场景下，问题和选项在 chain.History 最后一条 assistant 消息的 tool_use 里。
func findAskUserToolUse(history []llm.Message) (llm.ContentBlock, bool) {
	for i := len(history) - 1; i >= 0; i-- {
		msg := history[i]
		if msg.Role != llm.RoleAssistant {
			continue
		}
		for _, block := range msg.Content {
			if block.Type == llm.ContentBlockTypeToolUse && block.Name == "AskUser" {
				return block, true
			}
		}
	}
	return llm.ContentBlock{}, false
}

// extractAskUserPayload 从 FinalAssistant 的 AskUser tool_use 块中提取问题和选项。
// 返回 (question, options)。找不到或解析失败时 question 为空串。
func extractAskUserPayload(msg llm.Message) (string, []string) {
	for _, block := range msg.Content {
		if block.Type != llm.ContentBlockTypeToolUse || block.Name != "AskUser" {
			continue
		}
		var in struct {
			Question string   `json:"question"`
			Options  []string `json:"options"`
		}
		if err := json.Unmarshal(block.Input, &in); err != nil {
			return "", nil
		}
		return strings.TrimSpace(in.Question), in.Options
	}
	return "", nil
}
