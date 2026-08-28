package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
)

// 会话级挂载：用户在**这一个会话里**临时挂上的工具/知识库。
//
// 跟 agent 的绑定是两回事，别混：
//
//	agent 的绑定      agent 定义的一部分，所有使用者相同，改它 = 改这个 agent
//	会话挂载          只属于一个会话，别人用同一个 agent 完全不受影响
//
// 所以在别人共享出来的 agent 上挂自己的工具是安全的：那个 agent 一行都没动，作者看到
// 的还是他发布的配置，别人用还是原样。共享发布门（lea-config 的 _share_blockers）查的
// 是绑定，跟挂载无关。
//
// ## 为什么合并进 cfg，而不是往工具集里插
//
// 引擎的工具数组在 engine.Build() 那一刻就冻住了（agentloop/build.go 里
// Toolset.Tools() 全仓只调这一次），而 BuildPlatformTools 只读 cfg.Tools 和
// cfg.KBIDs。所以只要在 Build 之前把挂载并进 cfg，工具装配那一整套代码一个字都不用改，
// agentloop 也不用动。
//
// 代价是"生效要等下一次建会话" —— 由 Manager.Drop 解决：挂载后丢掉内存里那份引擎对象，
// 下一条消息重建时新工具自然就在初始集合里。对话不受影响（历史在 agent_messages）。

// AttachmentRef 是一条挂载记录，对应 session_attachments 的一行。
type AttachmentRef struct {
	Kind  string `json:"kind"` // tool | kb | skill（mcp_server 是下一批）
	RefID int    `json:"ref_id"`
}

// resolvedAttachments 是 lea-config 解析后的结果。
type resolvedAttachments struct {
	Tools  []ToolDef  `json:"tools"`
	Skills []SkillDef `json:"skills"`
	KBIDs  []int      `json:"kb_ids"`
	Droppd []struct {
		Kind   string `json:"kind"`
		RefID  int    `json:"ref_id"`
		Reason string `json:"reason"`
	} `json:"dropped"`
}

// MergeAttachments 把挂载解析出来并并进 cfg。
//
// ## 失败不阻断建会话
//
// 解析失败（lea-config 挂了、超时）只记日志，返回 cfg 原样 —— 用户少几个临时工具，
// 而不是**整个对话打不开**。这跟 lea-config 侧对失效项静默丢弃是同一条判断：挂载是
// 增量能力，它的任何问题都不该让一段已有的对话变成打不开的东西。
//
// ⚠️ 但"少了工具"必须能查。dropped 逐条打日志，否则"我昨天挂的工具今天不见了"在任何
// 地方都追不到 —— 而那种问题用户描述不清，只能靠日志复原。
func MergeAttachments(
	ctx context.Context,
	cfg *AgentConfig,
	tenantID, userID string,
	refs []AttachmentRef,
) *AgentConfig {
	if cfg == nil || len(refs) == 0 {
		return cfg
	}

	res, err := resolveAttachments(ctx, tenantID, userID, refs)
	if err != nil {
		log.Printf("attachments_resolve_failed tenant=%s user=%s n=%d err=%v (跳过挂载，会话照常建)",
			tenantID, userID, len(refs), err)
		return cfg
	}

	for _, d := range res.Droppd {
		log.Printf("attachment_dropped tenant=%s user=%s kind=%s ref_id=%d reason=%s",
			tenantID, userID, d.Kind, d.RefID, d.Reason)
	}

	// 去重按**暴露名**做，不是按 id。
	//
	// 工具名在一个会话里必须唯一 —— 引擎装配时重名会直接失败（agentloop 对 "Skill"
	// 那个名字就有专门处理）。而同一个工具既被 agent 绑了、又被用户挂了，是完全正常的
	// 情况（用户不知道这个 agent 已经绑了它）。
	//
	// 撞名时**保留 agent 绑定那一份**：那是 agent 定义的一部分，挂载是附加的。反过来
	// 让挂载覆盖绑定的话，用户就能在会话里悄悄替换掉这个 agent 的工具行为，而共享
	// agent 的作者对此一无所知。
	seen := make(map[string]bool, len(cfg.Tools))
	for _, t := range cfg.Tools {
		seen[t.Name] = true
	}
	for _, t := range res.Tools {
		if seen[t.Name] {
			log.Printf("attachment_tool_shadowed tenant=%s user=%s name=%s (agent 已绑同名，保留绑定那份)",
				tenantID, userID, t.Name)
			continue
		}
		seen[t.Name] = true
		cfg.Tools = append(cfg.Tools, t)
	}

	// skill 同理按名字去重，撞名同样保留 agent 绑定那份。
	//
	// 撞名的后果比工具更隐蔽：skill 是一段**注入进 prompt 的正文**，不是一个可调用的
	// 名字。让挂载覆盖绑定的话，共享 agent 的行为会被悄悄改写 —— 作者写的流程被替换成
	// 用户自己那份，而模型不会说它换了参考资料，输出看起来只是"表现不一样"。
	//
	// 一个会话里同名 skill 出现两次也不能留：引擎的 skill.Catalog 会列两条同名项，
	// Skill 工具按名字解析时只会命中一个，模型看到的目录跟能拿到的正文对不上。
	skillSeen := make(map[string]bool, len(cfg.Skills))
	for _, s := range cfg.Skills {
		skillSeen[s.Name] = true
	}
	for _, s := range res.Skills {
		if skillSeen[s.Name] {
			log.Printf("attachment_skill_shadowed tenant=%s user=%s name=%s (agent 已绑同名，保留绑定那份)",
				tenantID, userID, s.Name)
			continue
		}
		skillSeen[s.Name] = true
		cfg.Skills = append(cfg.Skills, s)
	}

	// kb_ids 去重，顺序无所谓（lea-rag 侧是个 IN 查询）。
	kbSeen := make(map[int]bool, len(cfg.KBIDs))
	for _, id := range cfg.KBIDs {
		kbSeen[id] = true
	}
	for _, id := range res.KBIDs {
		if !kbSeen[id] {
			kbSeen[id] = true
			cfg.KBIDs = append(cfg.KBIDs, id)
		}
	}

	return cfg
}

func resolveAttachments(
	ctx context.Context,
	tenantID, userID string,
	refs []AttachmentRef,
) (*resolvedAttachments, error) {
	payload, _ := json.Marshal(map[string]any{
		"tenant_id": tenantID,
		"user_id":   userID,
		"items":     refs,
	})

	req, err := http.NewRequestWithContext(ctx, "POST",
		configServiceURL+"/internal/resolve-attachments", bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("config service unreachable: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("resolve-attachments: HTTP %d", resp.StatusCode)
	}

	var out resolvedAttachments
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, fmt.Errorf("decode resolve-attachments: %w", err)
	}
	return &out, nil
}
