package runtime

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	engine "gitlab.ouc-online.com.cn/aibase/agentloop"
	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
	"gitlab.ouc-online.com.cn/aibase/agentloop/tools/askuser"
	"gitlab.ouc-online.com.cn/aibase/agentloop/tools/webfetch"
	"gitlab.ouc-online.com.cn/aibase/agentloop/tools/websearch"
)

var toolsGatewayURL = getEnv("LEA_TOOLS_URL", "http://localhost:8400")

// 工具执行不能共用 config_loader 那个 10s client：code_interpreter 首次调用
// 要等 Cube 冷启沙箱（拉 template + 起 VM + 挂 3 组 host-mount），实测远超
// 10s，之前日志里的 "context deadline exceeded" 就是这个 —— 靠引擎重试才救回来，
// 白等一轮。后续调用复用同一沙箱，都是几十毫秒。
var gatewayClient = &http.Client{Timeout: 120 * time.Second}

// GatewayRuntime 是引擎 ToolRuntime 端口的服务端实现。它替掉引擎默认的本地
// runtime —— 后者装配的是操作 lea-agent 本机磁盘的内置工具，而我们的工作空间在
// 沙箱/NFS 上，本机那套没有意义。
//
// 身份三元组必须在这里持有：文件工具要靠 tenantID + sessionID 才能定位到
// {NFS_ROOT}/tenants/{tid}/sessions/{sid}/workspace。
type GatewayRuntime struct {
	cfg       *AgentConfig
	tenantID  string
	userID    string
	sessionID string
}

func NewGatewayRuntime(cfg *AgentConfig, tenantID, userID, sessionID string) *GatewayRuntime {
	return &GatewayRuntime{cfg: cfg, tenantID: tenantID, userID: userID, sessionID: sessionID}
}

func (r *GatewayRuntime) Provision(ctx context.Context, spec engine.ToolsetSpec) (engine.Toolset, error) {
	return &GatewayToolset{
		cfg:       r.cfg,
		tenantID:  r.tenantID,
		userID:    r.userID,
		sessionID: r.sessionID,
	}, nil
}

type GatewayToolset struct {
	cfg       *AgentConfig
	tenantID  string
	userID    string
	sessionID string
}

// Tools 返回服务端版的内置文件工具（read/write/edit/list/bash）。
//
// **注册位置是有讲究的，不能挪到 ExtraTools。** 引擎在 build.go:146 调
// Provision，把结果并入 sessionTools；随后在 :222 快照 eligibleSubagentTools 交给
// 子代理启动器，最后才在 :260 append opts.ExtraTools。也就是说：
//
//	Provision 的工具  → 在快照内 → 子代理继承
//	ExtraTools 的工具 → 在快照后 → 子代理拿不到
//
// 平台工具（kb_search / code_interpreter 等）全走 ExtraTools，所以子代理此前的
// 工具集实际是空的。文件工具放这里，子代理无需上游改动就能读写同一个工作空间。
//
// **只有 Bash 挂 SandboxEnabled，其余四个不挂。**
//
// 这里原先整体挂在 SandboxEnabled 上，注释说"workspace 目录跟沙箱是同一份资源"——
// 那句是错的。workspace 是 NFS host-mount（宿主
// {NFS_ROOT}/tenants/{tid}/sessions/{sid}/workspace），lea-tools 进程直接读写；沙箱
// 只是**另一个**挂载它的地方。Read/Write/Edit/Glob 走 /api/files/*，一路不碰沙箱
// （file_tools.go 开头就写着"前四个工具不进沙箱"）。
//
// 按沙箱开关一起关掉的后果：一个纯文本产出的 agent（读 reference、读用户稿子、写一份
// markdown 评审意见 —— 全程不需要跑代码）如果 SandboxEnabled=false，会连 Read 都没有，
// 而它要的东西一样都不涉及沙箱。activate_skill 的引导又让它去 Read 那些 reference，
// 于是模型调一个没注册的工具。
//
// 正确的粒度是按资源分：文件工具要的是共享存储（session 目录由 provisioner 建，
// 跟沙箱无关），Bash 要的是沙箱。
// 平台工具（kb_search / memory_search / code_interpreter / 用户自建的 DynamicTool）
// 也在这里，不在 ExtraTools。它们此前走 ExtraTools，于是子代理和专家一个都拿不到 ——
// 那时"专家把 skill 执行派给子代理"只是看起来可行：派下去的子代理没有 code_interpreter，
// 跑不了 pptxgenjs。写记忆的 memory_add 例外，见 BuildMainSessionTools。
func (ts *GatewayToolset) Tools() []tool.Tool {
	if ts.cfg == nil {
		return nil
	}
	tools := BuildFileTools(ts.tenantID, ts.userID, ts.sessionID, ts.cfg.SandboxEnabled)
	tools = append(tools, BuildPlatformTools(ts.cfg, ts.tenantID, ts.userID, ts.sessionID)...)
	// 联网调研：WebSearch（DuckDuckGo，无需 key）+ WebFetch（抓一个 URL 的正文）。
	//
	// 它们是引擎自带的工具，但我们此前**一个都没有** —— 不是选择，是遗漏：build.go
	// 一看到 opts.ToolRuntime 非 nil 就整个替换掉本地 runtime，于是 tools.Builtins()
	// 里那批（WebSearch / WebFetch / Grep / TodoWrite / Delete）全没进来。跟
	// TodoWrite 缺失是同一个根因。
	//
	// 补上它们对**计划模式**尤其要紧：引擎判 OperationNetwork，而 isMutatingAction
	// 对 network 返回 false，所以规划态放行（实测 allow）—— 引擎自己的 plan-mode
	// prompt 里就写着可以 "web fetches"。对一个只配了业务 API、没有知识库的 agent，
	// 这是规划阶段唯一能用的外部信息来源（租户自定义工具在规划态被当成 External 挡住）。
	//
	// 走 Provision 而不是 ExtraTools：专家和子代理同样需要查资料，而
	// deriveExpertBase 会把 ExtraTools 清空。
	//
	// ⚠️ WebSearch 打的是 DuckDuckGo 的 HTML 结果页（引擎硬编码，见
	// tools/websearch）。它不用 key，所以开箱可用，但也意味着**绕过了 lea-tools
	// 的凭证和配额体系** —— 平台已有的 serpapi 那条路没被用上。要换成走网关得包一个
	// 自己的工具，那是另一件事；先接上引擎这个，让规划态有东西可查。
	tools = append(tools, websearch.New(), webfetch.New())
	// 追问工具。走 Provision 而不是 ExtraTools，因为**需要它的正是转交团的每一棒**，
	// 而 deriveExpertBase 会把 ExtraTools 清空 —— 放那边等于只有单 agent 能问。
	//
	// 引擎侧不会让子代理继承它（build.go 明确剔除）：子代理调它只会把自己那轮停掉，
	// 问题到不了用户，父代理反而拿到一个截断的结果然后替用户回答。
	//
	// 不接的代价实测过：内部沟通专家读完 skill（那份 skill 明写"先问用户目标受众"），
	// 提了四个问题就结束回合，链把它当丢线兜底，白跑两棒，用户一个字都没看到。
	return append(tools, askuser.New())
}

func (ts *GatewayToolset) Close(ctx context.Context) error {
	return nil
}

// ExecuteRemoteTool 通过 lea-tools 网关执行工具
func ExecuteRemoteTool(ctx context.Context, toolName string, params map[string]any, tenantID, userID string) (tool.Result, error) {
	payload := map[string]any{
		"tool_name":  toolName,
		"params":     params,
		"session_id": "",
	}
	body, _ := json.Marshal(payload)

	req, err := http.NewRequestWithContext(ctx, "POST",
		toolsGatewayURL+"/tools/execute",
		strings.NewReader(string(body)))
	if err != nil {
		return tool.Result{IsError: true}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Tenant-ID", tenantID)
	// userID 这个参数一直都在签名里，只是从来没发出去过 —— 网关侧因此只能按
	// (租户, 名字) 找工具。工具名改成用户内唯一之后（migrations/007），同一个租户里
	// alice 和 bob 可以各有一个 weather，不带这个头就是在两个人的工具之间随便挑一个。
	//
	// 空值不设头：网关把"没有 X-User-ID"当成 fail-closed（只解析平台工具），
	// 而设一个空字符串会变成"owner_user_id = ''"，那是个匹配不到任何人的值，
	// 但语义上比缺席更像"有身份"。缺席更诚实。
	if userID != "" {
		req.Header.Set("X-User-ID", userID)
	}
	req.Header.Set("X-User-Role", "user")

	resp, err := gatewayClient.Do(req)
	if err != nil {
		fmt.Printf("[gateway] %s gatewayClient.Do err: %v\n", toolName, err)
		return tool.Result{IsError: true, Content: []tool.ResultContent{
			{Type: tool.ResultContentTypeText, Text: fmt.Sprintf("tools gateway: %v", err)},
		}}, nil
	}
	defer resp.Body.Close()

	// 读原始 body 便于诊断
	rawBody, _ := io.ReadAll(resp.Body)
	fmt.Printf("[gateway] %s status=%d body[:200]=%s\n", toolName, resp.StatusCode, string(rawBody[:min(200, len(rawBody))]))

	var result struct {
		Status string          `json:"status"`
		Result json.RawMessage `json:"result"`
		Error  string          `json:"error"`
		// FastAPI 的 HTTPException 走的是 `detail`，跟上面三个字段一个都不重合。
		Detail json.RawMessage `json:"detail"`
	}
	json.Unmarshal(rawBody, &result)

	// ⚠️ 状态码必须先看。
	//
	// 以前这里完全不看 resp.StatusCode，只看 body 里的 status/error/result。
	// 而 lea-tools 的拒绝路径 raise 的是 HTTPException，body 形如
	//
	//     {"detail": "tool 'my__stock_lookup' not found"}
	//
	// 三个字段全是零值 → 不算 error → result 为 nil → 掉进最后那个分支，
	// 返回 **Text:"ok" 且 IsError:false**。于是 403 / 404 / 429 / 500 一律以
	// "工具调用成功、但没有内容" 的形式交给模型。
	//
	// 后果不是"报错难看"，是**模型会编**。实测：一个够不着的私有工具，模型收到
	// "ok" 之后回了「该库位位于 B-12 区，现有库存 245 件」—— 上游真实返回的是
	// 青鸾-7F / 2291，而这次调用根本没到上游。凭空捏造的数字，语气还很确定。
	//
	// 所以这里宁可把错误原样交给模型：它下一轮能自己纠正（换个工具、或者说查不到），
	// 而"成功但空"这个信号会让它以为拿到了数据。
	if resp.StatusCode >= 400 {
		msg := strings.TrimSpace(detailText(result.Detail))
		if msg == "" {
			msg = strings.TrimSpace(result.Error)
		}
		if msg == "" {
			// 兜底带上原始 body 的开头 —— 空错误信息比错误本身更难查。
			msg = fmt.Sprintf("HTTP %d: %s", resp.StatusCode,
				string(rawBody[:min(200, len(rawBody))]))
		}
		fmt.Printf("[gateway] %s denied status=%d msg=%s\n", toolName, resp.StatusCode, msg)
		return tool.Result{
			Content: []tool.ResultContent{{Type: tool.ResultContentTypeText, Text: msg}},
			IsError: true,
		}, nil
	}

	if result.Error != "" || result.Status == "error" {
		errMsg := result.Error
		if errMsg == "" {
			errMsg = "tool execution failed"
		}
		return tool.Result{
			Content: []tool.ResultContent{{Type: tool.ResultContentTypeText, Text: errMsg}},
			IsError: true,
		}, nil
	}

	if result.Result != nil {
		return tool.Result{
			Content: []tool.ResultContent{{Type: tool.ResultContentTypeJSON, Data: result.Result}},
		}, nil
	}
	return tool.Result{
		Content: []tool.ResultContent{{Type: tool.ResultContentTypeText, Text: "ok"}},
	}, nil
}

// detailText 把 FastAPI 的 `detail` 摊平成一行给模型看的文字。
//
// 两种形状都要认，因为它们来自两条不同的路径：
//
//	"tool 'x' not found"                     HTTPException(404, "...")  ← 我们自己抛的
//	[{"loc":[...],"msg":"field required"}]   422 请求体校验         ← pydantic 抛的
//
// 后一种以前根本到不了模型面前（整个错误分支都不走），所以"参数发错了"的表现是
// 工具静默返回 ok。摊平成 msg 拼接，模型下一轮能照着改。
func detailText(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var s string
	if json.Unmarshal(raw, &s) == nil {
		return s
	}
	var items []struct {
		Msg string `json:"msg"`
	}
	if json.Unmarshal(raw, &items) == nil {
		msgs := make([]string, 0, len(items))
		for _, it := range items {
			if it.Msg != "" {
				msgs = append(msgs, it.Msg)
			}
		}
		if len(msgs) > 0 {
			return strings.Join(msgs, "; ")
		}
	}
	return string(raw) // 认不出来就原样给出去，总比丢掉强
}
