package runtime

import (
	"context"
	"log"

	"gitlab.ouc-online.com.cn/aibase/agentloop/agent"
	"gitlab.ouc-online.com.cn/aibase/agentloop/team"
)

// SkillRunnerName 是专家派活的对象。
//
// 用引擎内置的 general-purpose 而不是自造一个：它的人设就是"open-ended, multi-step
// tasks delegated by the main agent"，工具继承全部（Tools 为空 = inherit-all），跟
// "执行一个已激活 skill 的完整流程"完全对得上。
const SkillRunnerName = "general-purpose"

// skillRunnerPrompt 是我们这边的 general-purpose 人设。
//
// 引擎的内置版本是代码场景的（搜代码库、追调用链、跑只读检查）。那六个内置 agent 并非
// 客户端独占 —— 我们 Config.Agents 为 nil 时同样拿到它们，这就是线上现状 —— 但在教育
// 产品里 code-reviewer / debugger 这类目录项是噪音，还可能误派。而 Config.Agents 一旦
// 非 nil，引擎就不再合并内置的六个（build.go 的理由：宿主提供名册就由宿主决定全部），
// 所以这份要自己给。
//
// **故意不写"怎么执行 skill"。** skill 的步骤在 SKILL.md 里，那是 skill 自己的事；
// 派哪个 skill 由主代理在 Task 的 prompt 里说。人设只交代执行者的通用契约，这样这个
// 子代理也能干别的活（读文档、整理文件），不是只能跑 skill。
//
// 加载 skill 的能力本来就有：Skill 工具注册在 build.go 的子代理快照**之前**，而
// launcher.go 让子代理继承父会话的 basePrompt（含 skill catalog）和工具集。
const skillRunnerPrompt = `你是执行代理。主代理把一件自包含的任务交给你独立完成。

你和主代理共享同一个工作空间，所以你写的文件它读得到。最终消息要自包含地说明你做了
什么、产出在哪几个文件 —— 它会原样回给主代理，它看不到你的过程。`

// BuildAgentRoster 返回交给引擎的子代理名册。
//
// **必须返回非 nil**。引擎里 Config.Agents 为 nil 的意思是"去扫磁盘"，而我们的 CWD 是
// /tmp，扫出来是空的 —— 那时引擎会合并它内置的六个代码场景人设，于是线上能委派的一直
// 只有那六个，库里配的专家一个都不在。这是 T1 之前的实际状态。
//
// 一个通用执行者就够 —— 子代理是通用的，不是每个 skill 一个专属代理。要它跑哪个
// skill、做什么，由主代理在 Task 的 prompt 里讲。
//
// 专家不进这个名册：专家走 ask_expert（独立会话，所以它自己也持有 Task），子代理走
// Task（同会话内的子会话），两者是不同的委派通道。
func BuildAgentRoster() []agent.Agent {
	return []agent.Agent{{
		Name:        SkillRunnerName,
		Description: "执行一件自包含的任务。给它完整独立的任务描述和要产出什么；它有你的全部工具，能自己加载技能、读写工作空间、跑代码。",
		Prompt:      skillRunnerPrompt,
		Source:      agent.SourceBuiltin,
		// Tools 留空 = 继承父会话能委派的全部工具（agent.InheritsAllTools）。
		// 专家把 skill 执行派下来时，子代理需要 Skill / Read / Write /
		// code_interpreter 一整套。
	}}
}

// BuildExperts 把团配置装成引擎的专家名册。
//
// members 里只有 member_agent_id 这个数字（lea-config 的 internal.py 按团长 agent_id
// 查团），所以每个专家要用那个 id 再打一次 /internal/agent-config 拿它自己绑的
// skill / tool / model。
//
// 一个专家装不起来只跳过它，不让整个会话建不起来 —— 少一个专家是降级，会话起不来是
// 彻底不可用。全部失败则返回 nil，引擎因此不注册 ask_expert（团长退化成普通 agent）。
func BuildExperts(ctx context.Context, cfg *AgentConfig, tenantID, userID, sessionID string) []team.Expert {
	if cfg == nil || cfg.Team == nil || len(cfg.Team.Members) == 0 {
		return nil
	}

	experts := make([]team.Expert, 0, len(cfg.Team.Members))
	for _, m := range cfg.Team.Members {
		if m.MemberAgentID <= 0 {
			continue
		}
		e, err := buildExpert(ctx, m.MemberAgentID, tenantID, userID, sessionID)
		if err != nil {
			log.Printf("expert_assembly_failed agent=%d err=%v (跳过该专家)", m.MemberAgentID, err)
			continue
		}
		// 团里配的 name 覆盖 agent 自己的名字 —— 同一个 agent 可以在不同团里
		// 担任不同角色，名字取团里那份对团长更有意义。
		if m.Name != "" {
			e.Name = m.Name
		}
		// role 是**团里的**位置，不是 agent 自己的属性：同一个"文档处理专家"在一个团里
		// 干活、在另一个团里验收。所以取自 member 行而不是 agent 配置。
		//
		// 星型完全不读它（引擎侧 Config.TeamMode 为空时 Role 是惰性的），所以这里无条件
		// 填不会影响现有的团。
		e.Role = EngineRole(m.Role, e.Name)
		experts = append(experts, e)
	}
	if len(experts) == 0 {
		return nil
	}
	return experts
}

// buildExpert 用 member_agent_id 装配一个专家。
func buildExpert(ctx context.Context, memberAgentID int, tenantID, userID, sessionID string) (team.Expert, error) {
	c, err := LoadAgentConfig(ctx, memberAgentID, tenantID)
	if err != nil {
		return team.Expert{}, err
	}

	// skill bundle 也要投递 —— 专家的 skill 跟团长的是不同的集合，团长会话创建时
	// 只投了它自己那些。共享 sessionID 所以落在同一个 skills 目录。
	if _, err := ProvisionSessionSkills(ctx, c, tenantID, sessionID); err != nil {
		log.Printf("expert_skill_provision_failed agent=%d err=%v (SKILL.md 正文不受影响)", memberAgentID, err)
	}

	return team.Expert{
		Name:        c.Name,
		Description: c.Description,
		Prompt:      c.SystemPrompt,
		// 专家可以用不同的模型 —— 便宜的干粗活。空则继承团长的。
		Model: c.Model,
		// 必须非 nil：nil 在引擎里是"扫磁盘"，而专家会话的 CWD 同样是 /tmp。
		Skills: ToEngineSkills(c.Skills),
		// **Tools 留空。**
		//
		// 专家会话由引擎从团长的 Options 派生，其中 ToolRuntime 被包了一层
		// （engine 侧的 withExtraProvisioned），而我们的 GatewayRuntime 持的是**团长的**
		// cfg —— 所以专家会话 Provision 出来的是团长那套平台工具，不是专家自己绑的那套。
		//
		// 这是个已知偏差，记在 T6 的验证清单里：一个只绑了 kb_search 的专家，实际会拿到
		// 团长绑的全部工具。要修得让引擎按 Expert 重建 runtime，或者 GatewayRuntime 支持
		// per-expert cfg。在此之前不要往这里塞工具 —— 塞了会跟 Provision 出来的同名工具
		// 撞名字，引擎装配阶段直接失败，比多几个工具严重得多。
		//
		// sessionID 仍要传团长的：三层共享一个 workspace 一个沙箱靠它。引擎侧
		// Config.SessionID 是继承的，但传进 GatewayRuntime 的那个是我们给的 —— 传错引擎
		// 管不了。
	}, nil
}
