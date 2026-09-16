package runtime

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
	"gitlab.ouc-online.com.cn/aibase/agentloop/team"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/cache"
)

var configServiceURL = getEnv("LEA_CONFIG_URL", "http://localhost:8500")

var (
	globalCache     *cache.Cache
	globalCacheOnce sync.Once
)

// InitCache 初始化全局缓存（从 main.go 调用）
func InitCache(redisClient *redis.Client) {
	globalCacheOnce.Do(func() {
		if redisClient == nil {
			log.Printf("[cache] Redis client is nil, cache disabled")
			return
		}
		globalCache = cache.New(redisClient, cache.DefaultConfig)
		log.Printf("[cache] Cache initialized with TTL=%s", cache.DefaultConfig.DefaultTTL)
	})
}

// GetCache 返回全局缓存实例（可能为 nil 如果 Redis 不可用）
func GetCache() *cache.Cache {
	return globalCache
}

// SetConfigServiceURLForTest 把 lea-config 的地址指向一个假上游，返回恢复函数。
//
// 只给测试用。runtime 包内的测试直接改 configServiceURL 就行（同包），但 router
// 包的测试够不着那个私有变量 —— 而建会话必打 /internal/agent-config，没有这个钩子
// 那边就只能起真服务或者跳过不验。
func SetConfigServiceURLForTest(u string) func() {
	old := configServiceURL
	configServiceURL = u
	return func() { configServiceURL = old }
}

type ToolDef struct {
	Name        string `json:"name"`
	DisplayName string `json:"display_name"`
	Description string `json:"description"`

	// InputSchema 的 tag 是 `schema_json`，**不是 `input_schema`**。
	//
	// 这一个词错了的代价：json.Decode 对不认识的键静默丢弃，于是 InputSchema 恒为
	// nil，jsonSchemaToToolSchema(nil) 返回一个**空对象 schema** —— 模型看到的工具
	// 没有任何参数声明。
	//
	// ⚠️ 症状不是"调不动"，是"全靠猜"。实测过 A/B（同一个中性提问「北京现在天气
	// 怎么样？」，只换 tag 重编译）：
	//
	//	tag 错   get_weather({"latitude": "39.9042", "longitude": "116.4074"})
	//	tag 对   get_weather({"latitude": 39.9042,   "longitude": 116.4074})
	//
	// **两次都答对了。** 模型从工具名和描述里把参数名猜了出来，而 open-meteo 收字符串
	// 也认。所以这个 bug 在 get_weather 上只表现为类型退化 —— 它恰好是最好猜的那种
	// 工具。真正吃亏的是参数名猜不出来的（q / sku_id / dept_code）、上游对类型严格的、
	// 以及有必填项需要强制的：没有 schema 就没有 required，模型可以整个漏掉。
	//
	// 一句话：坏得不均匀，所以更难发现 —— 抽查几个"看起来能用"完全证明不了没问题。
	//
	// 只影响 api / mcp 两类：内置那批在 BuildPlatformTools 的 builtins map 里被跳过，
	// schema 是 Go 代码里硬写的。也就是说**恰好只有用户自定义的工具受影响**，而那正是
	// 没人天天回归的那一类。
	//
	// 来历：老的 lea-server 那版下发的键确实叫 input_schema（它的模型里有一列就叫这个
	// 名字），这个结构体是照着它写的。lea-config 接手时把列统一成了执行器的词汇
	// （见 lea_config/models/tool.py 头部记的那次改名 input_schema → schema_json），
	// Go 侧没跟。LEA_CONFIG_URL 在 .env / k8s/config.yaml / 两个 compose 里全部指向
	// lea-config，所以老服务那个键今天没有任何来源。
	//
	// ⚠️ 不要为了"兼容"两个键都认。这张表被三个服务各定义一遍、留下两组表达同一件事
	// 的列，正是 migrations/006 花了一轮删掉的病；在下发契约上再造一组是同一个错误的
	// 第二版。权威词汇只有一套 —— 运行时真正决定工具能不能调通的那一侧（执行器）的
	// 那套。
	InputSchema map[string]any `json:"schema_json"`
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

	// ModelContextWindow 是**模型的**上下文窗口，来自 llm_providers.model_limits。
	//
	// 跟上面的 MaxTokens 是两回事，非常容易看混：
	//
	//	MaxTokens            单轮最多**生成**多少 token —— 进 llm.Request，输出侧
	//	ModelContextWindow   模型一次能吃下多少 token —— 客观事实，不进请求
	//
	// 一次请求 = [系统提示 + 历史 + 本轮输入] + [生成的回复]，窗口管的是这两段之和。
	//
	// 我们不直接用这个数，用的是它算出来的预算（session.contextBudget）。
	// 0 = 配置里没给这个模型，走保守兜底，见 session.fallbackContextWindow。
	ModelContextWindow int `json:"model_context_window"`

	// Team 非 nil 表示这个 agent 是一个专家团（库里 agent_teams 有它的行）。
	Team *TeamDef `json:"team"`
}

var httpClient = &http.Client{Timeout: 10 * time.Second}

// LoadAgentConfig 取 agent 的完整运行时配置。
// H2：agentID=0（前端硬编码 super-agent）时，lea-config 会兜底到 tenant super-agent，
// 返回的 cfg.ID 是解析后的真实 agent 主键。
func LoadAgentConfig(ctx context.Context, agentID int, tenantID string) (*AgentConfig, error) {
	// 降级保护：缓存未初始化时直接回源
	if globalCache == nil {
		return loadAgentConfigFromSource(ctx, agentID, tenantID)
	}

	// 缓存键：agent:{id}:{tenant}
	cacheKey := fmt.Sprintf("agent:%d:%s", agentID, tenantID)

	// 通过缓存加载
	data, err := globalCache.GetOrLoad(ctx, cacheKey, func(ctx context.Context) ([]byte, error) {
		cfg, err := loadAgentConfigFromSource(ctx, agentID, tenantID)
		if err != nil {
			return nil, err
		}
		return json.Marshal(cfg)
	})

	if err != nil {
		return nil, err
	}
	if data == nil {
		return nil, fmt.Errorf("agent %d not found for tenant %s", agentID, tenantID)
	}

	var cfg AgentConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("cache data unmarshal failed: %w", err)
	}

	return &cfg, nil
}

// loadAgentConfigFromSource 从 lea-config 加载配置（原 LoadAgentConfig 逻辑）
func loadAgentConfigFromSource(ctx context.Context, agentID int, tenantID string) (*AgentConfig, error) {
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
			// 窗口跟 base_url / api_key 一起下发：它们都是"这个模型怎么调"的一部分，
			// 而且都由 internal.py 里同一次 provider 匹配得出。
			ModelContextWindow int `json:"model_context_window"`
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
	if raw.LLM.ModelContextWindow > 0 {
		cfg.ModelContextWindow = raw.LLM.ModelContextWindow
	}
	// 这里**故意不兜底成某个常数**（不同于下面的 MaxTokens/MaxTurns）。兜底属于
	// session.contextBudget，因为只有那边知道"没给窗口"和"给了个 0"要按同一条保守
	// 路径处理，而且那个保守值跟引擎的触发线是一起推导的。在这里先填一个数，只会让
	// 两处各有一个缺省，而它们迟早不一样。
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
