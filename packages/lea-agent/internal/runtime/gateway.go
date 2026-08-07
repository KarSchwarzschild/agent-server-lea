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
	return append(tools, BuildPlatformTools(ts.cfg, ts.tenantID, ts.userID, ts.sessionID)...)
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
	}
	json.Unmarshal(rawBody, &result)

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
