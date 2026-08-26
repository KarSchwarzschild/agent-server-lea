package router

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	engine "gitlab.ouc-online.com.cn/aibase/agentloop"
	"gitlab.ouc-online.com.cn/aibase/agentloop/llm"
	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
	"gitlab.ouc-online.com.cn/aibase/agentloop/turn"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/runtime"
)

type DebugTraceRequest struct {
	AgentID int    `json:"agent_id"`
	Message string `json:"message"`
	UserID  string `json:"user_id"`
}

type traceStep struct {
	Phase  string `json:"phase"`
	Action string `json:"action"`
	Detail any    `json:"detail,omitempty"`
	Ms     int64  `json:"ms"`
}

// handleDebugTrace 跑一整轮并返回逐阶段耗时，用来定位"慢在哪"和"记忆注入了什么"。
//
// 身份必须来自 JWT，不能来自请求 —— 这条曾经是个真洞：
// tenant_id 取 query、user_id 取 body，于是任何人不带 token 就能
//  1. 指定 user_id 跑一轮，PreTurn 会把那个人的记忆 profile 检索出来，
//     再由下面的 "content" 字段**原样回显**（= 跨租户记忆读取 oracle）；
//  2. 结束时 OnSessionEnd 还会把这轮对话**写进那个人的记忆**，污染他后续
//     所有会话的注入内容（= 持久化投毒）；
//  3. 顺带白跑平台的 LLM key，且能碰到沙箱。
//
// 现在两个身份都从 JWT claims 取，客户端传什么都不看。
//
// 另外整个端点默认关闭 —— 它会回显记忆正文和完整 prompt，属于只在排查时才
// 该开的东西。要用就设 LEA_AGENT_DEBUG_TRACE=1。
func handleDebugTrace(c *gin.Context) {
	var req DebugTraceRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(400, gin.H{"error": err.Error()})
		return
	}

	// 身份只认 JWT。middleware.JWTAuth 保证 user_id 非空、tenant_id 有缺省值。
	tenantID := c.GetString("tenant_id")
	userID := c.GetString("user_id")
	if userID == "" {
		c.JSON(401, gin.H{"error": "missing token"})
		return
	}
	req.UserID = userID

	ctx := c.Request.Context()
	var steps []traceStep
	addStep := func(phase, action string, detail any, start time.Time) {
		steps = append(steps, traceStep{
			Phase: phase, Action: action, Detail: detail,
			Ms: time.Since(start).Milliseconds(),
		})
	}

	// ── Step 1: Load config ──
	t0 := time.Now()
	cfg, err := runtime.LoadAgentConfig(ctx, req.AgentID, tenantID)
	if err != nil {
		c.JSON(500, gin.H{"error": "load config failed: " + err.Error()})
		return
	}
	addStep("init", "load_config", map[string]any{
		"agent_id": req.AgentID,
		"model":    cfg.Model,
		"kb_ids":   cfg.KBIDs,
		"memory":   cfg.MemoryMode,
		"tools":    len(runtime.BuildPlatformTools(cfg, tenantID, req.UserID, "debug")),
	}, t0)

	// ── Step 2: Memory PreTurn ──
	var memoryInjection string
	if cfg.MemoryMode != "off" {
		t1 := time.Now()
		mem := runtime.NewMemoryMiddleware(tenantID, req.UserID, fmt.Sprintf("%d", req.AgentID))
		memoryInjection = mem.PreTurn(ctx, req.Message)
		addStep("pre_turn", "memory_inject", map[string]any{
			"injected": memoryInjection != "",
			"chars":    len(memoryInjection),
			"content":  truncate(memoryInjection, 500),
		}, t1)
	}

	// ── Step 3: Build enriched message ──
	enrichedMsg := req.Message
	if memoryInjection != "" {
		enrichedMsg = "[系统提示 - 用户相关信息]\n" + memoryInjection + "\n\n[用户消息]\n" + req.Message
	}
	addStep("pre_turn", "final_prompt", map[string]any{
		"chars":   len(enrichedMsg),
		"preview": truncate(enrichedMsg, 800),
	}, time.Now())

	// ── Step 4: Build engine + run turn ──
	extraTools := runtime.BuildPlatformTools(cfg, tenantID, req.UserID, "debug-trace")
	toolEvents := make(chan tool.Event, 64)
	var toolTrace []map[string]any
	toolDone := make(chan struct{})

	go func() {
		for ev := range toolEvents {
			toolTrace = append(toolTrace, map[string]any{
				"tool": ev.ToolName, "type": string(ev.Type),
			})
		}
		close(toolDone)
	}()

	engineCfg := engine.Config{
		Provider:       "openai",
		Model:          cfg.Model,
		MaxTokens:      cfg.MaxTokens,
		BaseURL:        cfg.LLMBaseURL,
		APIKey:         cfg.LLMAPIKey,
		CWD:            "/tmp",
		MaxIterations:  cfg.MaxTurns,
		SystemPrompt:   buildSystemPrompt(cfg),
		PermissionMode: "flight",
		// 跟主链路同一个理由（见 agent_session.go 的说明）。排查端点更要关：它
		// 跑的是真轮次，会真往那份进程级共享的 memory.md 里写。
		DisableMemory: true,
	}
	opts := engine.Options{
		ToolEvents: toolEvents,
		ExtraTools: extraTools,
		// 用与 extraTools 相同的 "debug-trace" 作 session id，两边的 workspace
		// 才是同一个目录 —— 排查时看到的文件树就是这轮真正写的那份。
		ToolRuntime: runtime.NewGatewayRuntime(cfg, tenantID, req.UserID, "debug-trace"),
	}

	sess, err := engine.Build(engineCfg, opts)
	if err != nil {
		c.JSON(500, gin.H{"error": "engine build failed: " + err.Error()})
		return
	}

	t3 := time.Now()
	result, err := sess.RunTurn(ctx, enrichedMsg)
	close(toolEvents)
	<-toolDone

	var assistantText string
	if err != nil {
		addStep("run_turn", "error", map[string]any{"error": err.Error()}, t3)
	} else {
		assistantText = extractTextFromResult(result)
		addStep("run_turn", "completed", map[string]any{
			"iterations":   result.Iterations,
			"stopped":      result.Stopped,
			"output_chars": len(assistantText),
			"output":       truncate(assistantText, 1000),
			"tool_calls":   toolTrace,
		}, t3)
	}

	sess.Close(ctx)

	// ── Step 5: Memory OnSessionEnd ──
	if cfg.MemoryMode != "off" && assistantText != "" {
		t4 := time.Now()
		mem := runtime.NewMemoryMiddleware(tenantID, req.UserID, fmt.Sprintf("%d", req.AgentID))
		messages := []runtime.MessagePair{
			{Role: "user", Content: req.Message},
			{Role: "assistant", Content: assistantText},
		}
		mem.OnSessionEnd(ctx, messages, "debug-"+time.Now().Format("20060102-150405"))
		addStep("session_end", "memory_extract_merge", map[string]any{
			"messages": len(messages),
		}, t4)
	}

	c.JSON(200, gin.H{
		"trace":  steps,
		"output": assistantText,
	})
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max] + "..."
}

func extractTextFromResult(result turn.Result) string {
	var parts []string
	for _, block := range result.FinalAssistant.Content {
		if block.Type == llm.ContentBlockTypeText && block.Text != "" {
			parts = append(parts, block.Text)
		}
	}
	return strings.Join(parts, "\n")
}

// suppress unused imports
var _ = context.Background
var _ json.RawMessage

func buildSystemPrompt(cfg *runtime.AgentConfig) string {
	prompt := cfg.SystemPrompt
	if len(cfg.Skills) > 0 {
		prompt += "\n\n## 可用技能\n当用户请求匹配以下技能时，先调用 activate_skill 激活对应技能：\n"
		for _, s := range cfg.Skills {
			prompt += fmt.Sprintf("- %s: %s\n", s.Name, s.Description)
		}
	}
	return prompt
}
