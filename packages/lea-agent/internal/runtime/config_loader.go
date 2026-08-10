package runtime

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"gitlab.ouc-online.com.cn/aibase/agentloop/team"
)

var configServiceURL = getEnv("LEA_CONFIG_URL", "http://localhost:8500")

type ToolDef struct {
	Name        string         `json:"name"`
	DisplayName string         `json:"display_name"`
	Description string         `json:"description"`
	InputSchema map[string]any `json:"input_schema"`
}

type SkillDef struct {
	ID            int    `json:"id"`
	Name          string `json:"name"`
	DisplayName   string `json:"display_name"`
	Description   string `json:"description"`
	SystemPrompt  string `json:"system_prompt"`
	BundleURI     string `json:"bundle_uri"`
	RequiredTools []any  `json:"required_tools"`
	Priority      int    `json:"priority"`
}

// TeamMemberDef 是专家团的一个成员。
//
// MemberAgentID 是**另一个 agent 的主键** —— 成员的完整配置（它自己绑的 skill、
// tool、model）不在这个结构里，要用这个 id 再打一次 /internal/agent-config。
// lea-config 侧 internal.py:85 是按团长的 agent_id 查团，members 只给到这个数字。
type TeamMemberDef struct {
	MemberAgentID int    `json:"member_agent_id"`
	Role          string `json:"role"`
	Name          string `json:"name"`
}

// EngineRole 把库里的 role 字符串映射成引擎的 team.Role。
//
// 库里那列是自由字符串（models/team.py 的 `String(50)`，默认 "worker"），所以映射要
// 认识的才认、不认识的当 worker。这一层留在 lea-agent 而不是引擎里：这套词汇是我们
// 数据库的，引擎只接受 team.Role。
//
// 不认识的值记一行日志。它在星型下完全无害（引擎不读 Role），但在 handoff 下会让
// ValidateChain 报"没有 entry/exit"—— 那条报错本身是清楚的（会列出全部专家名），
// 日志是为了让人能一眼看出根因是拼错而不是漏配。
func EngineRole(role string, expertName string) team.Role {
	switch r := team.Role(strings.ToLower(strings.TrimSpace(role))); r {
	case team.RoleEntry, team.RoleWorker, team.RoleExit:
		return r
	case "":
		return team.RoleWorker
	default:
		log.Printf("unknown_team_role expert=%q role=%q (按 worker 处理；handoff 模式下 entry/exit 缺失会在 Build 期报错)",
			expertName, role)
		return team.RoleWorker
	}
}

// TeamDef 是"这个 agent 是一个专家团"的配置。字段对齐 lea-config 的
// models/team.py（AgentTeam / AgentTeamMember）。
//
// 之前这个字段是丢掉的：internal.py:89 一直老实把 team + members 查出来返回，
// 但下面 raw 结构体里没有 team，json.Decode 静默丢弃、不报错。后果是
// engine.Config.Agents 永远为 nil，引擎 build.go:225 于是走 else 分支去磁盘找
// agent 定义，而 CWD 是 /tmp —— 线上能委派的一直只有引擎内置那六个，
// 配在库里的专家一个都不在。
type TeamDef struct {
	Strategy          string          `json:"strategy"`
	DecomposerPrompt  string          `json:"decomposer_prompt"`
	SynthesizerPrompt string          `json:"synthesizer_prompt"`
	MaxSubtasks       int             `json:"max_subtasks"`
	Members           []TeamMemberDef `json:"members"`
}

type AgentConfig struct {
	ID   int    `json:"id"`
	Name string `json:"name"`
	// Description 是这个 agent 做什么。它作为专家进团时，这是团长唯一能看到的信息
	// （ask_expert 的目录项），所以要说清"用来干什么"而不只是"是什么"。
	// 库里一直有这列（agents.description），Go 侧之前没接，于是专家目录项会是空的。
	Description    string     `json:"description"`
	Model          string     `json:"model"`
	MaxTokens      int        `json:"max_tokens"`
	MaxTurns       int        `json:"max_turns"`
	Temperature    float64    `json:"temperature"`
	KBMode         string     `json:"kb_mode"`
	MemoryMode     string     `json:"memory_mode"`
	SystemPrompt   string     `json:"system_prompt"`
	SoulPrompt     string     `json:"soul_prompt"`
	KBIDs          []int      `json:"kb_ids"`
	Tools          []ToolDef  `json:"tools"`
	Skills         []SkillDef `json:"skills"`
	LLMBaseURL     string     `json:"llm_base_url"`
	LLMAPIKey      string     `json:"llm_api_key"`
	SandboxEnabled bool       `json:"sandbox_enabled"`
	ActiveSkillID  string     `json:"active_skill_id"`

	// Team 非 nil 表示这个 agent 是一个专家团（库里 agent_teams 有它的行）。
	Team *TeamDef `json:"team"`
}

var httpClient = &http.Client{Timeout: 10 * time.Second}

// LoadAgentConfig 取 agent 的完整运行时配置。
// H2：agentID=0（前端硬编码 super-agent）时，lea-config 会兜底到 tenant super-agent，
// 返回的 cfg.ID 是解析后的真实 agent 主键。
func LoadAgentConfig(ctx context.Context, agentID int, tenantID string) (*AgentConfig, error) {
	url := fmt.Sprintf("%s/internal/agent-config/%d?tenant_id=%s", configServiceURL, agentID, tenantID)
	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return nil, err
	}

	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("config service unreachable: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("config service returned %d", resp.StatusCode)
	}

	var raw struct {
		Agent  json.RawMessage `json:"agent"`
		KBIDs  []int           `json:"kb_ids"`
		Tools  []ToolDef       `json:"tools"`
		Skills []SkillDef      `json:"skills"`
		Team   *TeamDef        `json:"team"`
		LLM    struct {
			BaseURL string `json:"llm_base_url"`
			APIKey  string `json:"llm_api_key"`
		} `json:"llm"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&raw); err != nil {
		return nil, err
	}

	var cfg AgentConfig
	if err := json.Unmarshal(raw.Agent, &cfg); err != nil {
		return nil, err
	}

	cfg.KBIDs = raw.KBIDs
	cfg.Tools = raw.Tools
	cfg.Skills = raw.Skills
	// team 在 agent 那个子对象外面，所以要单独搬 —— 上面 json.Unmarshal(raw.Agent)
	// 只填得到 agents 表自己的列。
	cfg.Team = raw.Team

	// LLM 配置优先级：聚合接口下发 > 环境变量
	if raw.LLM.BaseURL != "" {
		cfg.LLMBaseURL = raw.LLM.BaseURL
	}
	if raw.LLM.APIKey != "" {
		cfg.LLMAPIKey = raw.LLM.APIKey
	}
	if cfg.LLMBaseURL == "" {
		cfg.LLMBaseURL = getEnv("LLM_BASE_URL", "https://api.openai.com/v1")
	}
	if cfg.LLMAPIKey == "" {
		cfg.LLMAPIKey = getEnv("LLM_API_KEY", "")
	}
	if cfg.MaxTokens == 0 {
		cfg.MaxTokens = 4096
	}
	if cfg.MaxTurns == 0 {
		cfg.MaxTurns = 20
	}

	// 适配 lea-config 字段格式
	if cfg.SystemPrompt == "" && cfg.SoulPrompt != "" {
		cfg.SystemPrompt = cfg.SoulPrompt
	}
	if cfg.Temperature > 1 {
		cfg.Temperature = cfg.Temperature / 10.0
	}
	if !cfg.SandboxEnabled {
		cfg.SandboxEnabled = getEnv("SANDBOX_ENABLED", "true") == "true"
	}

	return &cfg, nil
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
