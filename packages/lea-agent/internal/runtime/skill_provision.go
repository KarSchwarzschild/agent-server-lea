package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// skillProvisionClient 单独一个 client：投递是本地目录拷贝（bundle 已按 sha 缓存在
// {NFS_ROOT}/cache/skills/<sha>/），不下 S3，所以不需要 gatewayClient 那 120s ——
// 那个超时是留给 Cube 冷启沙箱的。
var skillProvisionClient = &http.Client{Timeout: 30 * time.Second}

// ProvisionSessionSkills 让 lea-tools 把 agent 绑定的 skill 投递到本会话的 skills
// 目录，使 Read 能读到 SKILL.md 引用的 reference 文件。
//
// 为什么要在建会话时显式调这一次：lea-tools 侧原本只在**沙箱创建那一刻**投递
// （sandbox_session.py 的 create()）。于是 skill 文件在盘上的时机取决于模型有没有
// 恰好先调过一次 code_interpreter —— 不进沙箱的 Read/Glob 因此可能读到
// FileNotFoundError。旧的 activate_skill 引导让模型用 code_interpreter 读 reference，
// 顺带掩盖了这个隐式依赖；引导换成 Read 之后就必须把投递提前到会话生命周期上。
//
// 返回值只用于日志。**失败不该让会话建不起来**：读不到 reference 是降级（模型少一份
// 参考资料，SKILL.md 正文仍在），会话起不来是彻底不可用。lea-tools 侧同样是每个
// skill 独立 try。
func ProvisionSessionSkills(ctx context.Context, cfg *AgentConfig, tenantID, sessionID string) ([]string, error) {
	if cfg == nil || len(cfg.Skills) == 0 {
		return nil, nil
	}
	// 只投有 bundle 的。纯 prompt 型 skill（system_prompt 有内容但没打包 reference）
	// 没有 bundle_uri，投递它只会在 lea-tools 侧被跳过。
	items := make([]map[string]string, 0, len(cfg.Skills))
	for _, s := range cfg.Skills {
		if s.BundleURI == "" {
			continue
		}
		items = append(items, map[string]string{"name": s.Name, "bundle_uri": s.BundleURI})
	}
	if len(items) == 0 {
		return nil, nil
	}

	body, err := json.Marshal(map[string]any{
		"session_id": sessionID,
		"tenant_id":  tenantID,
		"skills":     items,
	})
	if err != nil {
		return nil, err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		toolsGatewayURL+"/api/files/skills/provision", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Tenant-ID", tenantID)

	resp, err := skillProvisionClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("lea-tools unreachable: %w", err)
	}
	defer resp.Body.Close()

	var out struct {
		Provisioned []string `json:"provisioned"`
		Requested   int      `json:"requested"`
	}
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("skill provision returned %d", resp.StatusCode)
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	return out.Provisioned, nil
}
