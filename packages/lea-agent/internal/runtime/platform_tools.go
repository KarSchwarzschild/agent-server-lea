package runtime

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
)

var ragServiceURL = getEnv("LEA_RAG_URL", "http://localhost:8300")

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
// 这条分界是"专家把 skill 执行派给子代理"能不能成立的前提：一个跑 pptx skill 的子代理
// 必须能调 code_interpreter，否则派了活却没有干活的手段。code_interpreter 此前在
// ExtraTools 里，子代理和专家一个都拿不到 —— 那时"派活给子代理"只是看起来可行。
//
// 附带一层：npm 工作区、NODE_PATH、os.environ 追加而非替换那些踩出来的规则写在
// CodeInterpreterTool.Description() 里。工具描述随工具走，所以工具进了子代理，坑的
// 说明也自动跟进去，不需要在别处复述一遍。
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

	if cfg.SandboxEnabled {
		tools = append(tools, &CodeInterpreterTool{
			tenantID:  tenantID,
			userID:    userID,
			sessionID: sessionID,
			skillID:   cfg.ActiveSkillID,
			skills:    cfg.Skills,
		})
	}

	// activate_skill 已退役 —— skill 披露走引擎的 Skill 工具（见 skill_discloser.go）。
	//
	// 换掉它的理由不是文案，是**注册位置**：它在 ExtraTools 里，而那是在 build.go 的
	// 子代理快照**之后**才 append 的，所以任何子代理和专家都拿不到它。而"专家把 skill
	// 执行派给子代理"要求子代理必须能加载 skill。引擎的 Skill 工具注册在快照之前，
	// 天然被继承。
	//
	// 两个不能并存：工具名不同（activate_skill / Skill）所以装配不会失败，但两个工具做
	// 同一件事，模型会随机挑一个，而其中一个的引导指向拿不到的通路。
	//
	// ActivateSkillTool 的类型定义暂时保留（下面 builtins map 里也还挡着这个名字），
	// 等真机验过 Skill 通路再删，免得回退时无处可退。

	// 动态工具：从 lea-config 拿到的工具定义，执行走 lea-tools 网关
	builtins := map[string]bool{"kb_search": true, "memory_search": true, "memory_add": true, "code_interpreter": true, "activate_skill": true}
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
// 目前只有 memory_add。它写的是用户跨会话的长期记忆，而子代理是临时的 —— 它的历史
// 用完即弃，让它往用户的长期记忆里写东西，语义上说不通：那条记忆的来由在子代理结束
// 时就消失了，只剩一条没人能追溯的记录。
//
// 这跟引擎对自己的 Remember 工具的处理是同一条线：build.go 把它放在快照之后，注释写着
// "sub-agents read memory but cannot write it — only the main session saves new
// memories"。读走 BuildPlatformTools 的 memory_search，写留在这里。
func BuildMainSessionTools(cfg *AgentConfig, tenantID, userID string) []tool.Tool {
	if cfg == nil || cfg.MemoryMode == "off" {
		return nil
	}
	return []tool.Tool{&MemoryAddTool{tenantID: tenantID, userID: userID}}
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

	req, _ := http.NewRequestWithContext(ctx, "POST",
		ragServiceURL+"/kb/search", strings.NewReader(string(body)))
	req.Header.Set("Content-Type", "application/json")

	resp, err := httpClient.Do(req)
	if err != nil {
		return tool.Result{IsError: true, Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: fmt.Sprintf("KB search failed: %v", err)},
		}}, nil
	}
	defer resp.Body.Close()

	var result json.RawMessage
	json.NewDecoder(resp.Body).Decode(&result)

	// summary 给一行摘要，full 塞前 5 条命中的关键字段供展开查看
	var parsed struct {
		Hits []struct {
			Title   string  `json:"title"`
			DocID   any     `json:"doc_id"`
			Score   float64 `json:"score"`
			Snippet string  `json:"snippet"`
			Content string  `json:"content"`
		} `json:"hits"`
	}
	json.Unmarshal(result, &parsed)
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
		{Type: tool.ResultContentTypeJSON, Data: result},
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
	req, _ := http.NewRequestWithContext(ctx, "POST",
		ragServiceURL+"/memory/search", strings.NewReader(string(body)))
	req.Header.Set("Content-Type", "application/json")

	resp, err := httpClient.Do(req)
	if err != nil {
		return tool.Result{IsError: true, Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: fmt.Sprintf("Memory search failed: %v", err)},
		}}, nil
	}
	defer resp.Body.Close()

	var result json.RawMessage
	json.NewDecoder(resp.Body).Decode(&result)

	var parsed struct {
		Memories []struct {
			Content string  `json:"content"`
			Score   float64 `json:"score"`
		} `json:"memories"`
	}
	json.Unmarshal(result, &parsed)
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
		{Type: tool.ResultContentTypeJSON, Data: result},
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
	req, _ := http.NewRequestWithContext(ctx, "POST",
		ragServiceURL+"/memory/add", strings.NewReader(string(body)))
	req.Header.Set("Content-Type", "application/json")

	resp, err := httpClient.Do(req)
	if err != nil {
		return tool.Result{IsError: true, Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: fmt.Sprintf("Memory add failed: %v", err)},
		}}, nil
	}
	defer resp.Body.Close()

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
//       长 Text (>1KB) 不落 full 避免 chat_messages.extra 撑爆。
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

// ── Activate Skill Tool ────────────────────────────────────

type ActivateSkillTool struct {
	skills    []SkillDef
	tenantID  string
	userID    string
	sessionID string
}

func (t *ActivateSkillTool) Name() string { return "activate_skill" }
func (t *ActivateSkillTool) Description() string {
	var names []string
	for _, s := range t.skills {
		names = append(names, s.Name)
	}
	return "激活一个技能以获取完整指引。可用: " + strings.Join(names, ", ")
}
func (t *ActivateSkillTool) IsConcurrencySafe() bool { return true }
func (t *ActivateSkillTool) InputSchema() tool.Schema {
	enumVals := make([]any, 0, len(t.skills))
	for _, s := range t.skills {
		enumVals = append(enumVals, s.Name)
	}
	return tool.Schema{
		Type: tool.SchemaTypeObject,
		Properties: map[string]tool.Schema{
			"skill_name": {
				Type:        tool.SchemaTypeString,
				Description: "要激活的技能名称",
				Enum:        enumVals,
			},
		},
		Required: []string{"skill_name"},
	}
}

func (t *ActivateSkillTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var params struct {
		SkillName string `json:"skill_name"`
	}
	json.Unmarshal(input, &params)

	var matched *SkillDef
	for i := range t.skills {
		if t.skills[i].Name == params.SkillName {
			matched = &t.skills[i]
			break
		}
	}
	if matched == nil {
		return tool.Result{IsError: true, Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: fmt.Sprintf("未找到技能: %s", params.SkillName)},
		}}, nil
	}

	// 返回完整 SKILL.md body 作为工具结果，LLM 将以此作为执行指南。
	//
	// SKILL.md 正文常用相对链接引用 references/*.md（页型定义、配色系统、API 手册）。
	// 那些文件不注入 prompt（pptx-generator 一家就 56KB ≈ 1.7 万 token，且多数
	// 单次任务用不到），而是靠 NFS RO 挂载让 LLM 按需读。所以这里必须给出可用的
	// 绝对路径 —— 不明说路径，LLM 会拿相对链接去猜，猜不到就跳过依赖这些文件的
	// 步骤（实测：pptx 的调研/配色/页型规划全被跳过，只剩 SKILL.md 正文里的步骤）。
	//
	// 试过在这儿加"缺内容就该读"的通用判据，想让模型补上被跳过的 Step 7 QA
	// （SKILL.md 那步正文只有一个裸链接 `See [QA Process](references/pitfalls.md)`）。
	// 实测无效且有害：pitfalls.md 照旧不读，反而多读了 13KB 用不上的 pptxgenjs.md。
	// 原因是模型对那一步的误判不是"我判断不需要读"，而是"我以为我已经在做 QA 了"——
	// 判据管不到一个它并不认为存在的缺口。明确说"按 Step 7 做 QA"时它第一件事就是
	// 读 pitfalls.md，可见差别在有没有人点出这一步，不在有没有判断规则。
	// 结论：这类跳过的根因在 SKILL.md 把整步内容全外置，得在 skill 侧修，工具提示
	// 词这一层碰不到。
	// 引导用 Read 而不是 code_interpreter：Read 走 lea-tools 直读共享存储，毫秒级、
	// 不起沙箱（冷启动实测 >10s）、不占执行配额，读一个 md 文件本来就不该起一个
	// Python executor。
	//
	// 前提是 skill 文件在会话创建时就已投递（ProvisionSessionSkills，见
	// agent_session.go）。在那之前投递只发生在沙箱创建那一刻，于是"Read 读不读得到
	// skill"取决于模型有没有恰好先调过一次 code_interpreter —— 旧引导用
	// code_interpreter 顺带掩盖了这个隐式依赖，换成 Read 就必须把投递提前。
	skillRoot := fmt.Sprintf("/opt/skills/%s", matched.Name)
	response := fmt.Sprintf("[技能已激活: %s]\n\n"+
		"以下是完整的技能执行规范，请严格按照此规范完成用户请求：\n\n---\n%s\n---\n\n"+
		"技能文件在 `%s/`（只读），上述规范里 `references/xxx.md` 这类"+
		"相对路径都在这个目录下。\n"+
		"**规范里提到某个 reference 文件时，先用 Read 读它再继续**，例如：\n"+
		"`Read(path=\"%s/references/slide-types.md\")`\n"+
		"（换成当前步骤实际引用的文件名 —— 按上面规范里写的相对路径拼，不用先列目录）",
		matched.Name, matched.SystemPrompt, skillRoot, skillRoot)

	// activate_skill 的 result_full 是完整 SKILL.md，可能几千字 —— 不落 full 避免 DB 膨胀
	emitToolResult(out, t.Name(), tctx.ToolUseID, fmt.Sprintf("已激活技能 %s（SKILL.md 已注入 LLM）", matched.Name), nil)

	return tool.Result{Content: []tool.ResultContent{
		{Type: tool.ResultContentTypeText, Text: response},
	}}, nil
}

// prepareSandboxBundle 已删除。
//
// 原实现 POST /sandbox/prepare-bundle 到 lea-tools，但那个端点从来没被实现过，
// 404 又被 goroutine 里的 httpClient.Do 静默丢弃 —— skill 文件因此从未下发。
// NFS 方案（路线 A）下平台 skill 走 {root}/platform/skills 全局共享挂载，
// lea-tools 启动时 sync_platform_skills() 一次性同步，不需要 per-session 下发。
// 见 docs/02-技术方案/SANDBOX_NFS_STORAGE.md §5。
