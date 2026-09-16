package runtime

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/sony/gobreaker"
	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/breaker"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
)

var ragServiceURL = getEnv("LEA_RAG_URL", "http://localhost:8300")

var (
	// 熔断器实例（全局单例）
	kbBreaker     *gobreaker.CircuitBreaker
	memoryBreaker *gobreaker.CircuitBreaker
)

func init() {
	kbBreaker = breaker.NewKBBreaker()
	memoryBreaker = breaker.NewMemoryBreaker()
}

// emitToolResult 把 tool 的真实结果通过 Progress 事件推给 collector。
// 引擎自己在 Run 返回后 emit 默认 Completed（Message="completed"），只反映"跑完了"，
// 无法反映"跑出了什么" —— 所以走 Progress 侧信道推 Data。
//
// summary：一行人读摘要（"命中 5 条 (query=...)"），落 tool_calls[i].result
// full：可选完整值（结构化 JSON、短 Text），落 tool_calls[i].result_full 供前端展开
// 传 nil / 空表示不推 full。长 Text (>1KB) 类的果断丢 full，别撑爆 DB extra 列。
//
// 非阻塞发送：channel 满就丢，不阻塞主流程。
func emitToolResult(out chan<- tool.Event, name, toolUseID string, summary string, full any) {
	if out == nil {
		return
	}
	data := map[string]any{"result": summary}
	if full != nil {
		data["result_full"] = full
	}
	select {
	case out <- tool.Event{
		Type:      tool.EventTypeProgress,
		ToolName:  name,
		ToolUseID: toolUseID,
		Data:      data,
	}:
	default:
	}
}

// BuildPlatformTools 返回**子代理也该拿到**的平台工具。
//
// 它的返回值走 ToolRuntime 的 Provision 通道（见 GatewayToolset.Tools），因为引擎在
// build.go 里的顺序是：Provision → 并入 sessionTools → 快照 eligibleSubagentTools →
// append ExtraTools。落在快照之前的才被子代理继承。
//
// 这条分界是"专家把 skill 执行派给子代理"能不能成立的前提：子代理必须能拿到
// Bash，才能在沙箱中执行 Python、Node 和 skill 脚本。
//
// 只有主会话该有的工具见 BuildMainSessionTools。
func BuildPlatformTools(cfg *AgentConfig, tenantID, userID, sessionID string) []tool.Tool {
	var tools []tool.Tool

	if cfg.KBMode != "off" && len(cfg.KBIDs) > 0 {
		tools = append(tools, &KBSearchTool{
			kbIDs:    cfg.KBIDs,
			tenantID: tenantID,
			userID:   userID,
		})
	}

	// memory_search 只读，子代理该有。memory_add 不在这里 —— 见
	// BuildMainSessionTools。
	if cfg.MemoryMode != "off" {
		tools = append(tools, &MemorySearchTool{tenantID: tenantID, userID: userID})
	}

	// get_plan 是只读的，**专家和子代理都该有**：转交链里执行第 3 步的那个专家，
	// 得看得到整体计划才不会跑偏。写那两个（save_plan / update_plan_step）在
	// BuildMainSessionTools —— 子代理改不了用户批准过的契约，标"完成"也轮不到一个
	// 没人验收的产出来推进进度。
	tools = append(tools, &GetPlanTool{tenantID: tenantID, sessionID: sessionID})

	// 动态工具：从 lea-config 拿到的工具定义，执行走 lea-tools 网关
	builtins := map[string]bool{
		"kb_search": true, "memory_search": true, "memory_add": true,
		"save_plan": true, "get_plan": true, "update_plan_step": true,
	}
	for _, td := range cfg.Tools {
		if builtins[td.Name] {
			continue
		}
		tools = append(tools, &DynamicTool{
			def:      td,
			tenantID: tenantID,
			userID:   userID,
		})
	}

	return tools
}

// BuildMainSessionTools 返回**只有主会话该拿到**的平台工具，走 Options.ExtraTools
// （快照之后 append，所以子代理拿不到）。
//
// 目前有 save_plan 和 memory_add。memory_add 写的是用户跨会话的长期记忆，而子代理是
// 临时的 —— 它的历史用完即弃，让它往用户的长期记忆里写东西，语义上说不通：那条记忆的
// 来由在子代理结束时就消失了，只剩一条没人能追溯的记录。save_plan 同理：计划属于
// 主会话的生命周期，子代理不该改它。
//
// 这跟引擎对自己的 Remember 工具的处理是同一条线：build.go 把它放在快照之后，注释写着
// "sub-agents read memory but cannot write it — only the main session saves new
// memories"。读走 BuildPlatformTools 的 memory_search，写留在这里。
func BuildMainSessionTools(cfg *AgentConfig, tenantID, userID, sessionID string, projectID *int) []tool.Tool {
	var tools []tool.Tool
	// 计划的两个**写**工具。始终注册 —— 计划模式是所有 agent 都支持的能力，
	// 不跟着 memory 配置走（计划持久化跟记忆是两回事），也没有按 agent 的开关：
	// 要不要进规划态是**每次请求**带来的（SendRequest.plan_mode），不是配置出来的。
	// 只读的 get_plan 在 BuildPlatformTools，专家也拿得到。
	tools = append(tools,
		&SavePlanTool{tenantID: tenantID, sessionID: sessionID},
		&UpdatePlanStepTool{tenantID: tenantID, sessionID: sessionID},
	)

	// deliver_artifact 也始终注册，理由跟 save_plan 同一条：它是 session_artifacts
	// 的唯一写入口，而"交付成品"跟 memory/KB 配置无关，任何 agent 都可能产出成品。
	//
	// 留在主会话而不进 Provision —— 子代理的产出是**给主会话看的中间结果**，不是
	// 给用户的成品。让它直接往用户的长期文件库里写，等于绕开了那个唯一有资格判断
	// "这份东西用户要不要"的角色：主会话拿到子代理的结果之后还要复核、还可能推翻。
	// 真要交付，由主会话再调一次 —— 那时它已经看过内容了。
	tools = append(tools, &DeliverArtifactTool{
		base: fileToolBase{
			tenantID:  tenantID,
			userID:    userID,
			sessionID: sessionID,
			// 不给 readSet：这个工具一个字节都不读（发布是 lea-tools 在共享存储上
			// 就地做的），新鲜度校验对它没有意义。
		},
		projectID: nullInt64FromPtr(projectID),
	})
	// memory_add 只在 memory 开启时注册
	if cfg != nil && cfg.MemoryMode != "off" {
		tools = append(tools, &MemoryAddTool{tenantID: tenantID, userID: userID})
	}
	return tools
}

// ── SavePlan Tool ─────────────────────────────────────────
//
// save_plan 是给 agent 的显式工具，也是计划的**唯一写入口**：用它提交或修订计划，
// 不依赖 plan_mode 开关。这样执行中发现计划要改时，agent 可以直接调 save_plan 存
// 修订版本，再调 AskUser 请用户重新批准——用户不需要手动切回 plan mode。
//
// AskUser 不再碰计划表。它以前在 plan_mode=true 时会顺手把追问的 question 当计划
// 存进去，那是把"这是一份计划"和"停下来等用户"两个信号焊在了一个事件上：规划一轮
// 里模型先调 save_plan(完整计划) 再调 AskUser("是否同意？")，收尾时后者**覆盖**掉
// 前者，执行阶段注入的"计划"就成了那句追问。现网数据里能直接看到这个痕迹。
//
// 写的是 draft，不碰已批准的那份 —— 用户还没表态之前，正在执行的计划必须留着。
// 见 persistence/plan_store.go 顶部和 migrations/009。

type SavePlanTool struct {
	tenantID  string
	sessionID string
}

func (t *SavePlanTool) Name() string { return "save_plan" }
func (t *SavePlanTool) Description() string {
	return "保存或更新执行计划。在规划阶段提交计划供用户审批；执行中发现计划需要修改时也可调用以提交修订版本。保存后需要用户明确批准才会生效——在此之前，已批准的旧计划继续有效。调用后请用 AskUser 请用户审批。"
}
func (t *SavePlanTool) IsConcurrencySafe() bool { return true }
func (t *SavePlanTool) InputSchema() tool.Schema {
	return tool.Schema{
		Type: tool.SchemaTypeObject,
		Properties: map[string]tool.Schema{
			"content": {Type: tool.SchemaTypeString, Description: "计划全文（目标、涉及的文件、风险、待确认的问题）"},
			"steps": {
				Type:        tool.SchemaTypeArray,
				Description: "计划的步骤清单，按执行顺序。执行阶段用 update_plan_step 逐步标记进度，所以每一步应当是一件能明确说'做完了'的事。",
				Items:       &tool.Schema{Type: tool.SchemaTypeString},
			},
		},
		Required: []string{"content", "steps"},
	}
}
func (t *SavePlanTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var params struct {
		Content string   `json:"content"`
		Steps   []string `json:"steps"`
	}
	json.Unmarshal(input, &params)

	db, err := persistence.DB()
	if err != nil || db == nil {
		return tool.Result{IsError: true, Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: "计划持久化不可用（数据库未配置）"},
		}}, nil
	}

	// 提交时一律 pending —— 谁都还没开始做。让模型在提交时就填状态只会得到一堆
	// 它凭空断言的进度。
	steps := make([]persistence.PlanStep, 0, len(params.Steps))
	for _, title := range params.Steps {
		if title = strings.TrimSpace(title); title != "" {
			steps = append(steps, persistence.PlanStep{Title: title, Status: persistence.StepPending})
		}
	}

	if err := persistence.SaveDraftPlan(ctx, db, t.tenantID, t.sessionID, params.Content, steps); err != nil {
		return tool.Result{IsError: true, Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: fmt.Sprintf("保存计划失败: %v", err)},
		}}, nil
	}

	emitToolResult(out, t.Name(), tctx.ToolUseID,
		fmt.Sprintf("已保存计划（%d 步），等待用户批准", len(steps)), nil)

	return tool.Result{Content: []tool.ResultContent{
		{Type: tool.ResultContentTypeText, Text: fmt.Sprintf(
			"计划已保存，共 %d 步，等待用户批准（在此之前已批准的旧计划仍然有效）", len(steps))},
	}}, nil
}

// ── GetPlan Tool ──────────────────────────────────────────
//
// 计划从"每轮推给模型"改成"模型按需来拉"。
//
// 推的代价是实打实的：引擎的 store 落的是它收到的那条 llm.Message，也就是**拼过
// 前缀的那条**，所以每一轮的 user 消息都带一份计划全文，ReloadHistory 读回 20 轮
// 就是 20 份。现网数据里能直接看到那个前缀被存进去。
//
// 拉的那一份为什么不会同样堆积 —— 靠下面的 ResultRetention：
//
//	Reproducible  这份结果模型随时能再调一次拿到 → 陈旧之后允许被脱水
//	Identity      每次调用观察的都是"这个会话的计划"，所以带同一个 Identity。
//	              后一次调用会让前一次的那份塌成一个指针，历史里只留最新一份。
//
// 两者都只在引擎的历史回收真的跑起来时才生效，而 MaxContextTokens 已经设上了
// （agent_session.go 的 contextWindowFor）。

type GetPlanTool struct {
	tenantID  string
	sessionID string
}

func (t *GetPlanTool) Name() string { return "get_plan" }
func (t *GetPlanTool) Description() string {
	return "查看当前会话已批准的执行计划全文与各步骤进度。每轮消息里只带一行计划摘要，需要具体内容时调它。"
}
func (t *GetPlanTool) IsConcurrencySafe() bool { return true }

// InputSchema 故意是空对象。
//
// ⚠️ 不要加 session_id 参数。工具实例构造时就把租户和会话焊死了，所以模型**结构上
// 够不到别人的计划**；开一个参数出来等于把它换成"每次调用都要记得校验"，而这个库
// 刚因为同一类问题（按名字查工具不带 user_id）修了一整轮。
func (t *GetPlanTool) InputSchema() tool.Schema {
	return tool.Schema{Type: tool.SchemaTypeObject, Properties: map[string]tool.Schema{}}
}

// ResultRetention 让引擎知道这份结果可以随取随弃。见类型注释。
//
// Identity 不带调用参数（本来也没有参数）：每次调用观察的都是同一个东西——这个会话
// 当前的计划。这正是 Identity 想表达的"同一个观察对象"，所以旧的那份该被新的取代。
func (t *GetPlanTool) ResultRetention(json.RawMessage) tool.Retention {
	return tool.Retention{
		Reproducible: true,
		Identity:     "get_plan:" + t.sessionID,
		Recovery:     `get_plan()`,
	}
}

func (t *GetPlanTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	db, err := persistence.DB()
	if err != nil || db == nil {
		return tool.Result{IsError: true, Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: "计划不可用（数据库未配置）"},
		}}, nil
	}

	plan, err := persistence.LoadPlan(ctx, db, t.tenantID, t.sessionID)
	if err != nil {
		return tool.Result{IsError: true, Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: fmt.Sprintf("读取计划失败: %v", err)},
		}}, nil
	}
	if plan.Approved == "" {
		// 不是错误：还没有计划是一个正常状态，说清楚比报错有用。
		return tool.Result{Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: "当前没有已批准的执行计划。"},
		}}, nil
	}

	done, total := plan.Progress()
	emitToolResult(out, t.Name(), tctx.ToolUseID, fmt.Sprintf("计划进度 %d/%d", done, total), nil)

	return tool.Result{Content: []tool.ResultContent{
		{Type: tool.ResultContentTypeText, Text: renderPlan(plan)},
	}}, nil
}

// renderPlan 把计划渲染成给模型看的文本。步骤带序号，因为 update_plan_step 用的就是
// 这个序号——模型从这里读到"3."，标进度时才知道该传 3。
func renderPlan(plan persistence.Plan) string {
	var b strings.Builder
	b.WriteString("【已批准的执行计划】\n")
	b.WriteString(plan.Approved)

	if len(plan.ApprovedSteps) > 0 {
		done, total := plan.Progress()
		fmt.Fprintf(&b, "\n\n【步骤进度】%d/%d 已完成\n", done, total)
		for i, s := range plan.ApprovedSteps {
			fmt.Fprintf(&b, "%d. [%s] %s\n", i+1, stepMark(s.Status), s.Title)
		}
		b.WriteString("\n用 update_plan_step(step=<序号>, status=in_progress|completed) 标记进度。")
	}

	if plan.Draft != "" {
		// 有待批准的修订时要说清楚，否则模型会以为下面那份已经生效了。
		b.WriteString("\n\n【待用户批准的修订版】（尚未生效，当前仍按上面的计划执行）\n")
		b.WriteString(plan.Draft)
	}
	return b.String()
}

func stepMark(status string) string {
	switch status {
	case persistence.StepCompleted:
		return "已完成"
	case persistence.StepInProgress:
		return "进行中"
	default:
		return "待办"
	}
}

// ── UpdatePlanStep Tool ───────────────────────────────────
//
// 只给主会话（BuildMainSessionTools）。让子代理标"完成"意味着一个还没人验收的产出
// 直接推进了用户批准过的计划；主会话本来就要收专家的结果，由它来标才对得上。

type UpdatePlanStepTool struct {
	tenantID  string
	sessionID string
}

func (t *UpdatePlanStepTool) Name() string { return "update_plan_step" }
func (t *UpdatePlanStepTool) Description() string {
	return "更新已批准计划中某一步的状态。开始做某步时标 in_progress，做完标 completed。步骤序号见 get_plan 或每轮消息里的计划摘要。"
}

// IsConcurrencySafe 返回 false —— 它是读-改-写同一行 JSON。
//
// 返回 true 的话引擎会在同一轮里并行调它（DynamicTool 硬编码 true 就踩过这个），
// 两次调用各自读到改前的步骤表，后写的把先写的覆盖掉：模型标了两步、库里只留一步。
// 这种丢更新不会报错，只会表现为"进度偶尔少一格"。
func (t *UpdatePlanStepTool) IsConcurrencySafe() bool { return false }

func (t *UpdatePlanStepTool) InputSchema() tool.Schema {
	return tool.Schema{
		Type: tool.SchemaTypeObject,
		Properties: map[string]tool.Schema{
			"step":   {Type: tool.SchemaTypeInteger, Description: "步骤序号，从 1 开始"},
			"status": {Type: tool.SchemaTypeString, Description: "pending / in_progress / completed"},
		},
		Required: []string{"step", "status"},
	}
}

func (t *UpdatePlanStepTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var params struct {
		Step   int    `json:"step"`
		Status string `json:"status"`
	}
	json.Unmarshal(input, &params)

	db, err := persistence.DB()
	if err != nil || db == nil {
		return tool.Result{IsError: true, Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: "计划不可用（数据库未配置）"},
		}}, nil
	}

	plan, err := persistence.UpdateStepStatus(ctx, db, t.tenantID, t.sessionID, params.Step, params.Status)
	if err != nil {
		// 错误原文回给模型（越界、状态词拼错、没有计划）——它下一轮能自己纠正，
		// 所以这里要说清是哪一种，而不是笼统一句"失败了"。
		return tool.Result{IsError: true, Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: fmt.Sprintf("更新步骤失败: %v", err)},
		}}, nil
	}

	done, total := plan.Progress()
	emitToolResult(out, t.Name(), tctx.ToolUseID,
		fmt.Sprintf("第 %d 步 → %s（%d/%d）", params.Step, params.Status, done, total), nil)

	return tool.Result{Content: []tool.ResultContent{
		{Type: tool.ResultContentTypeText, Text: fmt.Sprintf(
			"第 %d 步已标记为 %s。当前进度 %d/%d。", params.Step, params.Status, done, total)},
	}}, nil
}

// ── KB Search Tool ──────────────────────────────────────────

type KBSearchTool struct {
	kbIDs    []int
	tenantID string
	userID   string
}

func (t *KBSearchTool) Name() string            { return "kb_search" }
func (t *KBSearchTool) Description() string     { return "搜索知识库" }
func (t *KBSearchTool) IsConcurrencySafe() bool { return true }
func (t *KBSearchTool) InputSchema() tool.Schema {
	return tool.Schema{
		Type: tool.SchemaTypeObject,
		Properties: map[string]tool.Schema{
			"query": {Type: tool.SchemaTypeString, Description: "搜索关键词"},
			"top_k": {Type: tool.SchemaTypeInteger, Description: "返回条数", Default: 5},
		},
		Required: []string{"query"},
	}
}

func (t *KBSearchTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var params struct {
		Query string `json:"query"`
		TopK  int    `json:"top_k"`
	}
	json.Unmarshal(input, &params)
	if params.TopK == 0 {
		params.TopK = 5
	}

	payload := map[string]any{
		"query":     params.Query,
		"kb_ids":    t.kbIDs,
		"top_k":     params.TopK,
		"tenant_id": t.tenantID,
	}
	body, _ := json.Marshal(payload)

	// 通过熔断器执行 HTTP 调用
	result, err := kbBreaker.Execute(func() (interface{}, error) {
		req, _ := http.NewRequestWithContext(ctx, "POST",
			ragServiceURL+"/kb/search", strings.NewReader(string(body)))
		req.Header.Set("Content-Type", "application/json")

		resp, err := httpClient.Do(req)
		if err != nil {
			return nil, err
		}
		defer resp.Body.Close()

		// 非 2xx 响应视为失败（触发熔断器计数）
		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			return nil, fmt.Errorf("HTTP %d", resp.StatusCode)
		}

		var result json.RawMessage
		json.NewDecoder(resp.Body).Decode(&result)
		return result, nil
	})

	// 熔断器打开：降级返回
	if err == gobreaker.ErrOpenState {
		emitToolResult(out, t.Name(), tctx.ToolUseID, "KB 熔断器打开，已降级", nil)
		return tool.Result{Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: "知识库服务熔断中，请稍后重试或继续对话。"},
		}}, nil
	}

	// HTTP 错误或其他失败：降级返回
	if err != nil {
		emitToolResult(out, t.Name(), tctx.ToolUseID, fmt.Sprintf("KB 服务不可用: %v", err), nil)
		return tool.Result{Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: "知识库暂时不可用，请稍后重试或继续对话。"},
		}}, nil
	}

	// 成功：解析结果
	resultJSON := result.(json.RawMessage)
	var parsed struct {
		Hits []struct {
			Title   string  `json:"title"`
			DocID   any     `json:"doc_id"`
			Score   float64 `json:"score"`
			Snippet string  `json:"snippet"`
			Content string  `json:"content"`
		} `json:"hits"`
	}
	json.Unmarshal(resultJSON, &parsed)
	summary := fmt.Sprintf("命中 %d 条 (query=%q, top_k=%d)", len(parsed.Hits), params.Query, params.TopK)
	// full：前 5 条精简（title+doc_id+score+snippet 前 200 字），够用户判断命中的对不对
	preview := make([]map[string]any, 0, len(parsed.Hits))
	for i, h := range parsed.Hits {
		if i >= 5 {
			break
		}
		text := h.Snippet
		if text == "" {
			text = h.Content
		}
		if len(text) > 200 {
			text = text[:200] + "…"
		}
		preview = append(preview, map[string]any{
			"title": h.Title, "doc_id": h.DocID, "score": h.Score, "snippet": text,
		})
	}
	emitToolResult(out, t.Name(), tctx.ToolUseID, summary, map[string]any{
		"hits_preview": preview, "total": len(parsed.Hits),
	})

	return tool.Result{Content: []tool.ResultContent{
		{Type: tool.ResultContentTypeJSON, Data: resultJSON},
	}}, nil
}

// ── Memory Search Tool ──────────────────────────────────────

type MemorySearchTool struct {
	tenantID string
	userID   string
}

func (t *MemorySearchTool) Name() string            { return "memory_search" }
func (t *MemorySearchTool) Description() string     { return "搜索用户记忆" }
func (t *MemorySearchTool) IsConcurrencySafe() bool { return true }
func (t *MemorySearchTool) InputSchema() tool.Schema {
	return tool.Schema{
		Type: tool.SchemaTypeObject,
		Properties: map[string]tool.Schema{
			"query": {Type: tool.SchemaTypeString, Description: "搜索关键词"},
		},
		Required: []string{"query"},
	}
}
func (t *MemorySearchTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var params struct {
		Query string `json:"query"`
	}
	json.Unmarshal(input, &params)

	payload := map[string]any{
		"query":     params.Query,
		"user_id":   t.userID,
		"tenant_id": t.tenantID,
	}
	body, _ := json.Marshal(payload)

	// 通过熔断器执行 HTTP 调用
	result, err := memoryBreaker.Execute(func() (interface{}, error) {
		req, _ := http.NewRequestWithContext(ctx, "POST",
			ragServiceURL+"/memory/search", strings.NewReader(string(body)))
		req.Header.Set("Content-Type", "application/json")

		resp, err := httpClient.Do(req)
		if err != nil {
			return nil, err
		}
		defer resp.Body.Close()

		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			return nil, fmt.Errorf("HTTP %d", resp.StatusCode)
		}

		var result json.RawMessage
		json.NewDecoder(resp.Body).Decode(&result)
		return result, nil
	})

	// 熔断器打开：降级返回
	if err == gobreaker.ErrOpenState {
		emitToolResult(out, t.Name(), tctx.ToolUseID, "记忆熔断器打开，已降级", nil)
		return tool.Result{Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: "记忆服务熔断中，请稍后重试或继续对话。"},
		}}, nil
	}

	// HTTP 错误或其他失败：降级返回
	if err != nil {
		emitToolResult(out, t.Name(), tctx.ToolUseID, fmt.Sprintf("记忆服务不可用: %v", err), nil)
		return tool.Result{Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: "记忆服务暂时不可用，请稍后重试或继续对话。"},
		}}, nil
	}

	// 成功：解析结果
	resultJSON := result.(json.RawMessage)
	var parsed struct {
		Memories []struct {
			Content string  `json:"content"`
			Score   float64 `json:"score"`
		} `json:"memories"`
	}
	json.Unmarshal(resultJSON, &parsed)
	summary := fmt.Sprintf("命中 %d 条记忆 (query=%q)", len(parsed.Memories), params.Query)
	preview := make([]map[string]any, 0, len(parsed.Memories))
	for i, m := range parsed.Memories {
		if i >= 5 {
			break
		}
		c := m.Content
		if len(c) > 200 {
			c = c[:200] + "…"
		}
		preview = append(preview, map[string]any{"content": c, "score": m.Score})
	}
	emitToolResult(out, t.Name(), tctx.ToolUseID, summary, map[string]any{
		"memories_preview": preview, "total": len(parsed.Memories),
	})

	return tool.Result{Content: []tool.ResultContent{
		{Type: tool.ResultContentTypeJSON, Data: resultJSON},
	}}, nil
}

// ── Memory Add Tool ─────────────────────────────────────────

type MemoryAddTool struct {
	tenantID string
	userID   string
}

func (t *MemoryAddTool) Name() string            { return "memory_add" }
func (t *MemoryAddTool) Description() string     { return "保存信息到用户记忆" }
func (t *MemoryAddTool) IsConcurrencySafe() bool { return true }
func (t *MemoryAddTool) InputSchema() tool.Schema {
	return tool.Schema{
		Type: tool.SchemaTypeObject,
		Properties: map[string]tool.Schema{
			"content": {Type: tool.SchemaTypeString, Description: "要记住的内容"},
		},
		Required: []string{"content"},
	}
}

func (t *MemoryAddTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var params struct {
		Content string `json:"content"`
	}
	json.Unmarshal(input, &params)

	payload := map[string]any{
		"content":   params.Content,
		"user_id":   t.userID,
		"tenant_id": t.tenantID,
	}
	body, _ := json.Marshal(payload)

	// 通过熔断器执行 HTTP 调用
	_, err := memoryBreaker.Execute(func() (interface{}, error) {
		req, _ := http.NewRequestWithContext(ctx, "POST",
			ragServiceURL+"/memory/add", strings.NewReader(string(body)))
		req.Header.Set("Content-Type", "application/json")

		resp, err := httpClient.Do(req)
		if err != nil {
			return nil, err
		}
		defer resp.Body.Close()

		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			return nil, fmt.Errorf("HTTP %d", resp.StatusCode)
		}

		return nil, nil
	})

	// 熔断器打开：降级返回
	if err == gobreaker.ErrOpenState {
		emitToolResult(out, t.Name(), tctx.ToolUseID, "记忆熔断器打开，保存失败", nil)
		return tool.Result{Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: "记忆服务熔断中，保存失败，但对话可以继续。"},
		}}, nil
	}

	// HTTP 错误或其他失败：降级返回
	if err != nil {
		emitToolResult(out, t.Name(), tctx.ToolUseID, fmt.Sprintf("记忆服务不可用: %v", err), nil)
		return tool.Result{Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: "记忆服务暂时不可用，保存失败，但对话可以继续。"},
		}}, nil
	}

	// 成功
	preview := params.Content
	if len(preview) > 60 {
		preview = preview[:57] + "..."
	}
	emitToolResult(out, t.Name(), tctx.ToolUseID, fmt.Sprintf("已保存: %q", preview), nil)

	return tool.Result{Content: []tool.ResultContent{
		{Type: tool.ResultContentTypeText, Text: "已保存到记忆"},
	}}, nil
}

// ── Dynamic Tool（网关工具） ─────────────────────────────────

type DynamicTool struct {
	def      ToolDef
	tenantID string
	userID   string
}

func (t *DynamicTool) Name() string            { return t.def.Name }
func (t *DynamicTool) Description() string     { return t.def.Description }
func (t *DynamicTool) IsConcurrencySafe() bool { return true }

func (t *DynamicTool) InputSchema() tool.Schema {
	return jsonSchemaToToolSchema(t.def.InputSchema)
}

func (t *DynamicTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var params map[string]any
	json.Unmarshal(input, &params)
	result, err := ExecuteRemoteTool(ctx, t.def.Name, params, t.tenantID, t.userID)

	// 从 result.Content 里抽一段可读摘要给前端。原始 result 完整 JSON 还是给 LLM。
	// 用户自建工具千姿百态：网关返 Text/JSON/Image 都可能，做通用尽力提取。
	if !result.IsError {
		summary, full := summarizeToolResult(result)
		if summary != "" {
			emitToolResult(out, t.Name(), tctx.ToolUseID, summary, full)
		}
	}
	return result, err
}

// summarizeToolResult 尽力从 tool.Result 里抽 (summary, full)。
// summary: 200 字截断一行摘要 —— 时间线上快速看的"发生了什么"
// full: 结构化 JSON 或短 Text 原文 —— 前端展开时看的完整值。
//
//	长 Text (>1KB) 不落 full 避免 chat_messages.extra 撑爆。
//
// 都没有就返 ("", nil)。
func summarizeToolResult(r tool.Result) (string, any) {
	for _, c := range r.Content {
		if c.Type == tool.ResultContentTypeText && c.Text != "" {
			var full any
			if len(c.Text) <= 1024 {
				full = c.Text
			}
			return truncateSummary(c.Text), full
		}
	}
	for _, c := range r.Content {
		if c.Type == tool.ResultContentTypeJSON && c.Data != nil {
			b, err := json.Marshal(c.Data)
			if err != nil || len(b) <= 2 {
				continue
			}
			// JSON 无论大小都保留 full —— 结构化数据前端可展开渲染成表格/list
			return truncateSummary(string(b)), c.Data
		}
	}
	return "", nil
}

func truncateSummary(s string) string {
	s = strings.TrimSpace(s)
	if len(s) > 200 {
		return s[:200] + "…"
	}
	return s
}

// jsonSchemaToToolSchema 递归转换 JSON Schema → agentloop tool.Schema
func jsonSchemaToToolSchema(m map[string]any) tool.Schema {
	if m == nil {
		return tool.Schema{Type: tool.SchemaTypeObject}
	}

	s := tool.Schema{}

	if desc, ok := m["description"].(string); ok {
		s.Description = desc
	}

	switch m["type"] {
	case "object":
		s.Type = tool.SchemaTypeObject
	case "string":
		s.Type = tool.SchemaTypeString
	case "number":
		s.Type = tool.SchemaTypeNumber
	case "integer":
		s.Type = tool.SchemaTypeInteger
	case "boolean":
		s.Type = tool.SchemaTypeBoolean
	case "array":
		s.Type = tool.SchemaTypeArray
	default:
		s.Type = tool.SchemaTypeObject
	}

	if props, ok := m["properties"].(map[string]any); ok {
		s.Properties = make(map[string]tool.Schema)
		for k, v := range props {
			if vm, ok := v.(map[string]any); ok {
				s.Properties[k] = jsonSchemaToToolSchema(vm)
			}
		}
	}

	if req, ok := m["required"].([]any); ok {
		for _, r := range req {
			if rs, ok := r.(string); ok {
				s.Required = append(s.Required, rs)
			}
		}
	}

	if items, ok := m["items"].(map[string]any); ok {
		is := jsonSchemaToToolSchema(items)
		s.Items = &is
	}

	if enums, ok := m["enum"].([]any); ok {
		for _, e := range enums {
			if es, ok := e.(string); ok {
				s.Enum = append(s.Enum, es)
			}
		}
	}

	if def, ok := m["default"]; ok {
		s.Default = def
	}

	return s
}

// prepareSandboxBundle 已删除。
//
// 原实现 POST /sandbox/prepare-bundle 到 lea-tools，但那个端点从来没被实现过，
// 404 又被 goroutine 里的 httpClient.Do 静默丢弃 —— skill 文件因此从未下发。
// NFS 方案（路线 A）下平台 skill 走 {root}/platform/skills 全局共享挂载，
// lea-tools 启动时 sync_platform_skills() 一次性同步，不需要 per-session 下发。
// 见 docs/02-技术方案/SANDBOX_NFS_STORAGE.md §5。
