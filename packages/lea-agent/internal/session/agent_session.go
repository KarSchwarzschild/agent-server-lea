package session

import (
	"context"
	"errors"
	"fmt"
	"log"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	engine "gitlab.ouc-online.com.cn/aibase/agentloop"
	"gitlab.ouc-online.com.cn/aibase/agentloop/llm"
	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/runtime"
)

type AgentSession struct {
	id       string
	tenantID string
	userID   string
	agentID  int
	cfg      *runtime.AgentConfig
	engine   *engine.Session
	memory   *runtime.MemoryMiddleware

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
	extraTools := runtime.BuildMainSessionTools(cfg, p.TenantID, p.UserID)
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
	if len(experts) > 0 {
		names := make([]string, 0, len(experts))
		for _, e := range experts {
			names = append(names, e.Name)
		}
		log.Printf("team_assembled session=%s strategy=%s experts=%v",
			sessionID, cfg.Team.Strategy, names)
	}

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
		MaxContextTokens: contextWindowFor(cfg.Model),

		// 以下三份名册**必须非 nil**，这是服务端和客户端的分界：
		// nil 在引擎里的意思是"去磁盘扫"（config.go 的 tri-state），而我们 CWD 是
		// /tmp，扫出来是空的 —— 那时引擎会合并它内置的六个代码场景 agent，于是线上
		// 能派活的一直只有那六个、库里配的一个都不在。这就是本轮之前的实际状态。
		Skills:  runtime.ToEngineSkills(cfg.Skills),
		Agents:  runtime.BuildAgentRoster(),
		Experts: experts,
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
	}

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

	sess, err := engine.Build(engineCfg, opts)
	if err != nil {
		return nil, err
	}
	s.engine = sess

	go s.forwardToolEvents(toolEvents)

	return s, nil
}

func (s *AgentSession) ID() string { return s.id }

// OwnedBy 校验会话归属 —— stop 之类的控制端点必须先过这道，
// 否则拿到别人的 session_id 就能掐断别人正在跑的 turn。
func (s *AgentSession) OwnedBy(tenantID, userID string) bool {
	return s.tenantID == tenantID && s.userID == userID
}

func (s *AgentSession) Send(message string, attachmentIDs []int64) error {
	// turn 级可取消 ctx —— Stop() 靠它掐断。不能用 context.Background()：
	// 引擎的 collectAssistantMessage / 工具层 httpClient 全是 ctx-aware，
	// 但没人持有 cancel 时那些 select <-ctx.Done() 永远不会触发，一旦
	// RunTurn 起飞就得等 MaxIterations 跑完（实测 20+ 轮、几分钟）。
	s.Touch()

	ctx, cancel := context.WithCancel(context.Background())
	s.turnMu.Lock()
	if s.turnCancel != nil {
		// 上一轮还没收尾（引擎侧 turnActive 也会拒绝并发 turn），先掐掉它
		s.turnCancel()
	}
	s.turnCancel = cancel
	s.turnEpoch++
	s.turnStopped = false
	myEpoch := s.turnEpoch
	s.turnMu.Unlock()

	go func() {
		defer func() {
			cancel()
			s.turnMu.Lock()
			if s.turnEpoch == myEpoch {
				s.turnCancel = nil
			}
			s.turnMu.Unlock()
		}()
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

		// ── Phase 2: 执行引擎 RunTurn ──
		var assistantText string
		var runErr error
		result, err := s.engine.RunTurn(ctx, message)
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
			s.currentCollector().setMainUsage(inTok, outTok, peakInputTokens(result.Usages), result.Iterations, perRoundUsage(result.Usages))
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
			s.currentCollector().setMainUsage(inTok, outTok, peakInputTokens(result.Usages), result.Iterations, perRoundUsage(result.Usages))
			s.broadcast(SSEEvent{Type: "result", Data: map[string]any{
				"text":            assistantText,
				"iterations":      result.Iterations,
				"stopped":         result.Stopped,
				"inputTokens":     inTok,
				"outputTokens":    outTok,
				"peakInputTokens": peakInputTokens(result.Usages),
			}})
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
	s.mu.Lock()
	for ch := range s.subscribers {
		close(ch)
	}
	s.subscribers = make(map[chan SSEEvent]struct{})
	s.mu.Unlock()
}

func (s *AgentSession) broadcast(ev SSEEvent) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for ch := range s.subscribers {
		select {
		case ch <- ev:
		default:
		}
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

// defaultContextWindow 是拿不准的模型用的保守缺省。
//
// 这个数字只决定**什么时候开始回收历史**（引擎在超过 threshold ratio 时才跑 Reduce），
// 不是硬上限。估小了只是回收得早一点、多付几次重读的代价；估大了会在模型真实上限前
// 撞墙，那才是用户能看到的失败。所以缺省往小取。
const defaultContextWindow = 128_000

// contextWindowFor 返回模型的上下文预算。
//
// 按前缀匹配而不是精确表：精确表在模型名多一个后缀时就静默失效（"qwen3-max" →
// "qwen3-max-2026" 会掉进缺省分支而没人发现），而这里判断错的代价只是回收时机偏移。
// 前缀匹配对同一系列的新版本自动生效。
//
// 实际在用的模型见 .env：LEA_MODEL_CHAT / LEA_MODEL_ROUTING = qwen3-max，
// LEA_MODEL_FALLBACK = glm-5.1，LEA_LOCAL_MODEL = qwen3-8b。
func contextWindowFor(model string) int {
	m := strings.ToLower(strings.TrimSpace(model))
	switch {
	case strings.HasPrefix(m, "qwen3-8b"), strings.HasPrefix(m, "qwen2"):
		// 小模型窗口小得多，给它 128k 会让 Reduce 永远不触发然后直接撞墙。
		return 32_000
	case strings.HasPrefix(m, "qwen3-max"), strings.HasPrefix(m, "qwen-max"),
		strings.HasPrefix(m, "qwen-plus"), strings.HasPrefix(m, "qwen3"):
		return defaultContextWindow
	case strings.HasPrefix(m, "glm-5"), strings.HasPrefix(m, "glm-4"):
		return defaultContextWindow
	default:
		return defaultContextWindow
	}
}
