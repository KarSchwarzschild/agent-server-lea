package session

import (
	"encoding/json"
	"strings"
	"sync"

	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
)

// messageCollector 累积一个 turn 内的可视化字段，最终打包成 chat_messages.extra JSON。
//
// H1 阶段只收 Go 侧当前有的：
//   - tool_calls   ← tool.Event（Started/Completed/Failed），排除 code_interpreter
//   - sandbox_calls ← tool.Event where ToolName == "code_interpreter"
//   - reasoning_blocks ← StreamThinking 累积成单块（无 expert_id）
//   - segments     ← 按到达顺序记录的时间线（delta/thinking/tool/sandbox），
//     tool/sandbox 用 index 引用 tool_calls/sandbox_calls，不复制对象
//
// 前端可视化字段（intent/skill/references/plan）Go 目前不 emit，先不收。
type messageCollector struct {
	mu sync.Mutex

	responseParts []string
	thinking      strings.Builder

	toolCalls      []map[string]any
	toolIndex      map[string]int
	sandboxCalls   []map[string]any
	sandboxIndex   map[string]int
	references     []map[string]any
	referenceIndex map[string]int

	// segments 保留 delta/thinking/tool/sandbox 到达的真实交错顺序。
	// 每项形如 {kind: "text"|"thinking"|"tool"|"sandbox", content?: string, index?: int}
	// tool/sandbox 用 index 指向 toolCalls/sandboxCalls，避免序列化时对象重复。
	segments []map[string]any

	// ── 用量（计费依据，一条都不能丢）──────────────────────────
	//
	// 这里收的是**子会话**的用量：子代理（launcher.go）和专家（expert_runner.go）
	// 各自结束时发 EventTypeAgentUsage。主会话自己的用量拿不到事件，由
	// AgentSession 在 RunTurn 返回后直接调 setMainUsage 填进来。
	//
	// 之前这两路都没落库：onToolEvent 只认 started/progress/completed，agent_usage
	// 流过就被丢掉；主会话的 result.Usages 压根没人消费。于是 chat_messages 的
	// token_count 列**一直是 0**，"这轮花了多少钱"在库里查不到 —— 做单 agent 对照
	// 实验时才暴露，因为对照组没有子会话，统计出来全是零。
	//
	// 计费要求每一笔都在，所以子会话逐条留（agentUsages），不只留合计：一次派活失败
	// 重试两次，那是两笔真实开销，合并之后无从对账。
	mainInputTokens  int
	mainOutputTokens int
	mainPeakInput    int
	mainIterations   int
	agentUsages      []map[string]any
	// mainRounds 是主会话每一轮 LLM 调用的用量明细。合计答不了"钱花在哪" ——
	// 累计输入是 N 轮历史的叠加，同一份内容被重复计了很多次。逐轮数据才能看出
	// 载荷是第几轮进的上下文、之后被重传了多少次。见 perRoundUsage 的说明。
	mainRounds []map[string]any
	// chainMode 标记这轮是 handoff 链，于是 main 里的 token 全零**是真实的**而不是
	// 没测到。不标的话落库数据里两者长得一样，而 setMainUsage 的注释专门讲过这件事
	// 要避免。
	chainMode bool
}

// setMainUsage 记主会话本轮的用量。RunTurn 返回后调一次。
//
// peak 单独记：累计和衡量花多少钱，峰值衡量离撞上下文窗口还有多远。委派类改动的收益
// 主要体现在峰值下降，而累计反而会上升（多了子会话的启动开销），只看一个数会得出
// 相反结论。
//
// **handoff 链不要调这个**，调 setChainUsage —— 理由见那边。
func (c *messageCollector) setMainUsage(in, out, peak, iterations int, rounds []map[string]any) {
	c.mu.Lock()
	c.mainInputTokens = in
	c.mainOutputTokens = out
	c.mainPeakInput = peak
	c.mainIterations = iterations
	c.mainRounds = rounds
	c.mu.Unlock()
}

// usageSnapshot 读一下当前累计的主会话用量，给"跑完立刻广播一次"用
// （agent_session.go 在发 done 之前调）。跟落库那份走的是同一份数据
// （mainInputTokens/mainOutputTokens），只是提前在关闭连接之前吐一次给
// 前端，省得用户必须刷新页面才能看到这轮花了多少 token。
func (c *messageCollector) usageSnapshot() (in, out int) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.mainInputTokens, c.mainOutputTokens
}

// setChainUsage 记一条 handoff 链的形状：轮数和 leg 的峰值，**不记 token**。
//
// 因为 handoff 下没有"主会话"这个东西。星型有：团长自己跑若干轮、读专家的报告、
// 决定下一步，那些轮次是团长的开销，跟专家的开销不重叠。链没有团长 —— 每条 leg
// 都是独立会话，而它们各自结束时已经通过 bridge 发了 agent_usage 进 agentUsages。
//
// 所以把链的 usages 喂给 setMainUsage 会让**三条 leg 被计两次**。run3 实测：
// main 87,995（三条 leg 之和）+ agents 555,538（含同样那三条）= 落库 643,533，
// 真实开销是 555,538，账单虚高 16%。计费依据虚高比缺失更糟 —— 缺失查得出来，
// 虚高看起来是个正常数字。
//
// 轮数和峰值仍然记，它们不是加法：
//   - iterations 是"这次对话打了多少次模型"，跟星型同义，前端那个数要它
//   - peak 是取最大值，幂等，重复取不会翻倍
//
// 但 peak 这里只是**各条 leg 里最高的那一次请求**，不是全链峰值：leg 的 usages 不含
// 它派出去的子代理（子代理的用量走 bridge 单独进 agentUsages）。run3 里 leg 峰值
// 5,729，而真正的全链峰值 32,920 在子代理身上 —— 想知道链离撞上下文窗口有多远，得
// 看 agents 里每条的 peak，main 这个数答不了。
func (c *messageCollector) setChainUsage(iterations, peak int, rounds []map[string]any) {
	c.mu.Lock()
	c.mainInputTokens = 0
	c.mainOutputTokens = 0
	c.mainPeakInput = peak
	c.mainIterations = iterations
	c.mainRounds = rounds
	c.chainMode = true
	c.mu.Unlock()
}

// onAgentUsage 记一笔子会话用量。
//
// 不做去重也不做合并：同一个 agentName 可能被派多次（这轮"文档处理专家"就被派了
// 两次），每次都是独立计费单元。
func (c *messageCollector) onAgentUsage(ev tool.Event) {
	if ev.InputTokens == 0 && ev.OutputTokens == 0 {
		return
	}
	u := map[string]any{
		"kind":         ev.ToolName, // ask_expert（专家会话）| Task（子代理会话）
		"agentName":    ev.AgentName,
		"toolCallId":   ev.ToolUseID,
		"inputTokens":  ev.InputTokens,
		"outputTokens": ev.OutputTokens,
		"durationMs":   ev.DurationMs,
	}
	// parentId 只在真有上一层时落 —— 空值意味着"这个子会话直接挂在主会话的 turn 上"，
	// 而不是"父亲不详"。团长直接派的专家就是这种情况。
	//
	// 这一列此前**恒等于 toolCallId**（每条都是自己的父亲），起因在引擎的事件桥：
	// agent_usage 从 EmitUsage 出来时 ToolUseID 和 ToolName 都是空的，桥把派生它的
	// 调用 id 写进了 ParentToolUseID，外层桥见非空就跳过，最后 executor 的
	// normalizeToolEvent 把同一个 id 补进仍为空的 ToolUseID —— 两个字段撞成一个值。
	// 后果是三层链（团长 → 专家 → 子代理）在库里看起来像三个平级兄弟，回看时
	// 判断不出 278k 是哪个专家的子代理花的。引擎侧已修（agentevents/bridge.go
	// 的 isSelfReport 分支），这里跟着改成"空就不落"。
	if ev.ParentToolUseID != "" {
		u["parentId"] = ev.ParentToolUseID
	}
	// 峰值和轮数是判断"委派值不值"的依据，不是可选的展示字段：inputTokens 是各轮
	// 之和，而每轮输入已包含此前的历史，所以它说明这次派活花了多少钱，说明不了
	// 单次请求离上下文窗口还有多远。把活挪进子会话会让父会话峰值下降、两个合计
	// 同时上升 —— 只看合计会把结论看反。
	if ev.PeakInputTokens > 0 {
		u["peakInputTokens"] = ev.PeakInputTokens
	}
	if ev.Iterations > 0 {
		u["iterations"] = ev.Iterations
	}
	c.mu.Lock()
	c.agentUsages = append(c.agentUsages, u)
	c.mu.Unlock()
}

// usageTotals 汇总全链路用量（主会话 + 全部子会话）。
func (c *messageCollector) usageTotals() (in, out int) {
	c.mu.Lock()
	defer c.mu.Unlock()
	in, out = c.mainInputTokens, c.mainOutputTokens
	for _, u := range c.agentUsages {
		if v, ok := u["inputTokens"].(int); ok {
			in += v
		}
		if v, ok := u["outputTokens"].(int); ok {
			out += v
		}
	}
	return in, out
}

func newCollector() *messageCollector {
	return &messageCollector{
		toolIndex:      make(map[string]int),
		sandboxIndex:   make(map[string]int),
		referenceIndex: make(map[string]int),
	}
}

func (c *messageCollector) onDelta(delta string) {
	if delta == "" {
		return
	}
	c.mu.Lock()
	c.responseParts = append(c.responseParts, delta)
	c.appendTextSegmentLocked("text", delta)
	c.mu.Unlock()
}

func (c *messageCollector) onThinking(delta string) {
	if delta == "" {
		return
	}
	c.mu.Lock()
	c.thinking.WriteString(delta)
	c.appendTextSegmentLocked("thinking", delta)
	c.mu.Unlock()
}

// appendTextSegmentLocked 把 text/thinking 增量并入 segments。
// 相邻同类段合并成一条，避免 delta token 级碎片撑爆 JSON。
func (c *messageCollector) appendTextSegmentLocked(kind, delta string) {
	if n := len(c.segments); n > 0 {
		last := c.segments[n-1]
		if last["kind"] == kind {
			if prev, ok := last["content"].(string); ok {
				last["content"] = prev + delta
				return
			}
		}
	}
	c.segments = append(c.segments, map[string]any{
		"kind":    kind,
		"content": delta,
	})
}

// onToolEvent 把 agentloop tool.Event 映射到 tool_calls / sandbox_calls。
// Bash 走 sandbox 组，其他走通用 tool 组。
func (c *messageCollector) onToolEvent(ev tool.Event) {
	if refs := referencesFromToolEvent(ev); len(refs) > 0 {
		c.onReferences(refs)
	}
	// 用量事件先接住 —— 它不是工具生命周期的一环，走下面的 started/progress/completed
	// 分支会被整个丢掉（这是 token_count 列一直为 0 的原因之一）。
	//
	// **必须在下面那道 ToolUseID/ToolName 早退之前**：用量是子会话对自己整个 run 的
	// 汇报，不对应任何一次工具调用，两个字段都由引擎沿途补（桥补 ToolUseID、executor
	// 补 ToolName）。补法将来若变，早退会把整轮计费静默吞掉，而这种丢失没有可见症状 ——
	// 前端照常渲染，只是 token_count 是 0，到月底对账才发现。
	if ev.Type == tool.EventTypeAgentUsage {
		c.onAgentUsage(ev)
		return
	}

	if ev.ToolUseID == "" || ev.ToolName == "" {
		return
	}

	c.mu.Lock()
	defer c.mu.Unlock()

	if ev.ToolName == "Bash" || ev.ToolName == "code_interpreter" {
		c.onSandboxLocked(ev)
		return
	}
	c.onToolLocked(ev)
}

func (c *messageCollector) onReferences(refs []map[string]any) {
	c.mu.Lock()
	defer c.mu.Unlock()
	for _, ref := range refs {
		id, _ := ref["id"].(string)
		if id == "" {
			continue
		}
		if index, ok := c.referenceIndex[id]; ok {
			c.references[index] = ref
			continue
		}
		c.referenceIndex[id] = len(c.references)
		c.references = append(c.references, ref)
	}
}

func referencesFromToolEvent(ev tool.Event) []map[string]any {
	refs := make([]map[string]any, 0, len(ev.Files))
	for _, file := range ev.Files {
		if file.Path == "" {
			continue
		}
		refs = append(refs, map[string]any{
			"id":     "file:" + file.Path,
			"title":  file.Path,
			"path":   file.Path,
			"source": string(file.Kind),
		})
	}
	if payload, ok := ev.Data.(map[string]any); ok {
		if raw, ok := payload["references"].([]any); ok {
			for _, item := range raw {
				if ref, ok := item.(map[string]any); ok {
					if _, exists := ref["id"]; !exists {
						ref["id"] = ref["url"]
					}
					if id, _ := ref["id"].(string); id != "" {
						refs = append(refs, ref)
					}
				}
			}
		}
	}
	return refs
}

func (c *messageCollector) onToolLocked(ev tool.Event) {
	idx, exists := c.toolIndex[ev.ToolUseID]
	switch ev.Type {
	case tool.EventTypeStarted:
		if exists {
			return
		}
		newIdx := len(c.toolCalls)
		c.toolIndex[ev.ToolUseID] = newIdx
		c.toolCalls = append(c.toolCalls, map[string]any{
			"toolCallId": ev.ToolUseID,
			"tool":       ev.ToolName,
			"input":      decodeInput(ev.Input),
			"status":     "running",
		})
		c.segments = append(c.segments, map[string]any{
			"kind":  "tool",
			"index": newIdx,
		})
	case tool.EventTypeProgress:
		// Tool 在 Run 内主动 emit Progress 携带 Data，用来把结构化的真实 result
		// 塞进 tool_calls[i]（覆盖引擎默认的 "completed" 占位）。
		// 约定 Data 里 "result" = 200 字摘要（时间线一行），"result_full" = 完整值供展开。
		if !exists {
			return
		}
		if payload, ok := ev.Data.(map[string]any); ok && payload != nil {
			tc := c.toolCalls[idx]
			if r, has := payload["result"]; has {
				tc["result"] = r
			}
			if rf, has := payload["result_full"]; has && rf != nil {
				tc["result_full"] = rf
			}
		}
	case tool.EventTypeCompleted, tool.EventTypeFailed:
		if !exists {
			// 没见过 started（可能事件丢了），补一条完整记录
			idx = len(c.toolCalls)
			c.toolIndex[ev.ToolUseID] = idx
			c.toolCalls = append(c.toolCalls, map[string]any{
				"toolCallId": ev.ToolUseID,
				"tool":       ev.ToolName,
				"input":      decodeInput(ev.Input),
			})
			c.segments = append(c.segments, map[string]any{
				"kind":  "tool",
				"index": idx,
			})
		}
		tc := c.toolCalls[idx]
		if ev.Type == tool.EventTypeFailed {
			tc["status"] = "error"
		} else {
			tc["status"] = "success"
		}
		// Progress 已经把结构化 result 塞过来了，别用引擎的 "completed" 占位盖掉。
		// 没有 Progress 时（工具报错就走这条）优先用 ev.Output 里的真实输出，
		// 拿不到才退回 Message —— 见 onSandboxLocked 里同一处理的注释。
		if _, has := tc["result"]; !has {
			stdoutText, stderrText := splitOutputLines(ev.Output)
			switch {
			case stderrText != "":
				tc["result"] = truncate(stderrText, 2000)
			case stdoutText != "":
				tc["result"] = truncate(stdoutText, 2000)
			case ev.Message != "":
				tc["result"] = ev.Message
			}
		}
		if ev.DurationMs > 0 {
			tc["durationMs"] = ev.DurationMs
		}
	}
}

func (c *messageCollector) onSandboxLocked(ev tool.Event) {
	idx, exists := c.sandboxIndex[ev.ToolUseID]
	switch ev.Type {
	case tool.EventTypeStarted:
		if exists {
			return
		}
		input := decodeInput(ev.Input)
		codePreview, language := extractCodeMeta(input)
		newIdx := len(c.sandboxCalls)
		c.sandboxIndex[ev.ToolUseID] = newIdx
		c.sandboxCalls = append(c.sandboxCalls, map[string]any{
			"callId":      ev.ToolUseID,
			"language":    language,
			"codePreview": codePreview,
			"status":      "running",
		})
		c.segments = append(c.segments, map[string]any{
			"kind":  "sandbox",
			"index": newIdx,
		})
	case tool.EventTypeProgress:
		// H5.C: code_interpreter.Run 在返回前 emit 一条 Progress，Data 里带
		// 沙箱执行结构化字段（artifacts/stdout/stderr/duration_ms）—— 是 Started
		// 之后、Completed 之前的信号，用来富化 sandboxCall。
		if !exists {
			return
		}
		payload, ok := ev.Data.(map[string]any)
		if !ok || payload == nil {
			return
		}
		sc := c.sandboxCalls[idx]
		if arts, ok := payload["artifacts"]; ok {
			sc["artifacts"] = normalizeArtifacts(arts)
		}
		if s, ok := payload["stdout"].(string); ok && s != "" {
			sc["stdoutPreview"] = truncate(s, 2000)
		}
		if s, ok := payload["stderr"].(string); ok && s != "" {
			sc["stderrPreview"] = truncate(s, 2000)
		}
		if d, ok := payload["duration_ms"].(float64); ok && d > 0 {
			sc["durationMs"] = int64(d)
		}
	case tool.EventTypeCompleted, tool.EventTypeFailed:
		if !exists {
			idx = len(c.sandboxCalls)
			c.sandboxIndex[ev.ToolUseID] = idx
			c.sandboxCalls = append(c.sandboxCalls, map[string]any{
				"callId": ev.ToolUseID,
			})
			c.segments = append(c.segments, map[string]any{
				"kind":  "sandbox",
				"index": idx,
			})
		}
		sc := c.sandboxCalls[idx]
		if ev.Type == tool.EventTypeFailed {
			sc["status"] = "error"
		} else {
			sc["status"] = "success"
		}
		if ev.DurationMs > 0 {
			sc["durationMs"] = ev.DurationMs
		}
		// 出错路径上 Progress 压根没 emit（code_interpreter.Run 在 execCode 报错时
		// 直接 return，推 Progress 那段在它后面），所以 stdout/stderr 都还是空的。
		// 引擎在 Failed 事件里带了 ev.Output —— 按 stream 分类的真实输出行，
		// 之前完全没用，只把字面 Message（"completed with error"）当 stdout 落库，
		// 结果最需要看 traceback 的时候库里什么都没有。
		stdoutText, stderrText := splitOutputLines(ev.Output)
		if _, has := sc["stdoutPreview"]; !has && stdoutText != "" {
			sc["stdoutPreview"] = truncate(stdoutText, 2000)
		}
		if _, has := sc["stderrPreview"]; !has && stderrText != "" {
			sc["stderrPreview"] = truncate(stderrText, 2000)
		}
		if ev.OutputTruncated {
			sc["outputTruncated"] = true
		}
		// Message 只作最后兜底，且明确标成 message 而不是冒充 stdout ——
		// "completed with error" 是引擎的状态描述，不是沙箱输出。
		if ev.Message != "" && ev.Message != "completed" {
			if _, hasOut := sc["stdoutPreview"]; !hasOut {
				if _, hasErr := sc["stderrPreview"]; !hasErr {
					sc["message"] = ev.Message
				}
			}
		}
	}
}

// splitOutputLines 把引擎的 OutputLine 按 stream 归成两坨文本。
// stderr / diff 之类归 stderr，其余（stdout/info/match）归 stdout —— 沙箱
// 场景下只有这两种流有意义。
func splitOutputLines(lines []tool.OutputLine) (stdout, stderr string) {
	if len(lines) == 0 {
		return "", ""
	}
	var outParts, errParts []string
	for _, l := range lines {
		if l.Stream == tool.OutputStreamStderr {
			errParts = append(errParts, l.Text)
			continue
		}
		outParts = append(outParts, l.Text)
	}
	return strings.Join(outParts, "\n"), strings.Join(errParts, "\n")
}

// normalizeArtifacts 把 payload["artifacts"] 规整成 []string / []map，
// 兼容后端两种约定（纯字符串列表 或 {name,path,size}）。
func normalizeArtifacts(raw any) any {
	arr, ok := raw.([]any)
	if !ok {
		return nil
	}
	if len(arr) == 0 {
		return nil
	}
	// 若第一项是 string → 全按 []string 处理
	if _, isString := arr[0].(string); isString {
		out := make([]string, 0, len(arr))
		for _, v := range arr {
			if s, ok := v.(string); ok {
				out = append(out, s)
			}
		}
		return out
	}
	return arr
}

// hasTimelineInfo 判断 segments 是否值得落库：只有 0 段或"单纯一段文字"时视为
// 没有交错信息（跟 message.content 冗余），返回 false。
func hasTimelineInfo(segs []map[string]any) bool {
	if len(segs) == 0 {
		return false
	}
	if len(segs) == 1 && segs[0]["kind"] == "text" {
		return false
	}
	return true
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

func (c *messageCollector) responseText() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return strings.Join(c.responseParts, "")
}

// toExtra 打包成落库 JSON。全为空返回 nil。
func (c *messageCollector) toExtra() map[string]any {
	c.mu.Lock()
	defer c.mu.Unlock()

	extra := make(map[string]any)
	if len(c.toolCalls) > 0 {
		extra["tool_calls"] = c.toolCalls
	}
	if len(c.sandboxCalls) > 0 {
		extra["sandbox_calls"] = c.sandboxCalls
	}
	if len(c.references) > 0 {
		extra["references"] = c.references
	}
	if t := c.thinking.String(); t != "" {
		extra["reasoning_blocks"] = []map[string]any{
			{
				"expertId":   "__model__",
				"expertName": "思考过程",
				"reasoning":  t,
			},
		}
	}
	// 只有一段纯 text 时不落 —— 与 content 重复，多此一举。
	// 出现 tool/sandbox/thinking 或多段 text（被工具打断过）才有时间线价值。
	if hasTimelineInfo(c.segments) {
		extra["segments"] = c.segments
	}

	// 只要有任何用量就落 —— 这是计费依据，不能因为"看起来没信息量"就省掉。哪怕一轮
	// 只调了一次模型、没派任何活，那次调用也是要计费的。
	//
	// 全零才跳过，那意味着 setMainUsage 没被调过（没跑 turn，或者上游没给 Usages），
	// 落一堆 0 只会让"真的花了 0"和"没测到"两种情况在库里长得一样。
	//
	// main 和 agents 分开放：前者是团长/单 agent 自己的开销，后者逐条列出每个子会话，
	// 对账时能追到具体哪次派活花了多少。total 是两者之和，省下游再算一遍。
	totalIn, totalOut := c.mainInputTokens, c.mainOutputTokens
	for _, u := range c.agentUsages {
		if v, ok := u["inputTokens"].(int); ok {
			totalIn += v
		}
		if v, ok := u["outputTokens"].(int); ok {
			totalOut += v
		}
	}
	if totalIn > 0 || totalOut > 0 {
		main := map[string]any{
			"inputTokens":     c.mainInputTokens,
			"outputTokens":    c.mainOutputTokens,
			"peakInputTokens": c.mainPeakInput,
			"iterations":      c.mainIterations,
		}
		if len(c.mainRounds) > 0 {
			main["rounds"] = c.mainRounds
		}
		usage := map[string]any{
			"main":              main,
			"totalInputTokens":  totalIn,
			"totalOutputTokens": totalOut,
		}
		if c.chainMode {
			// 让 main 里的零是自解释的：读这份数据的人（或月底对账的脚本）看到
			// inputTokens=0 时，要能分清"这是链、开销全在 agents 里"和"用量没测到"。
			usage["topology"] = "handoff"
		}
		if len(c.agentUsages) > 0 {
			usage["agents"] = c.agentUsages
		}
		extra["usage"] = usage
	}

	if len(extra) == 0 {
		return nil
	}
	return extra
}

func decodeInput(raw json.RawMessage) map[string]any {
	if len(raw) == 0 {
		return nil
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil
	}
	return out
}

// extractCodeMeta 从沙箱工具的 input 里挑出代码预览 / language。
// 前端 sandbox_calls 卡片需要这俩字段。
func extractCodeMeta(input map[string]any) (preview string, language string) {
	if input == nil {
		return "", ""
	}
	if lang, ok := input["language"].(string); ok {
		language = lang
	}
	code, ok := input["code"].(string)
	if !ok {
		code, ok = input["command"].(string)
		if ok && language == "" {
			language = "bash"
		}
	}
	if ok {
		if len(code) > 200 {
			preview = code[:200] + "..."
		} else {
			preview = code
		}
	}
	return
}
