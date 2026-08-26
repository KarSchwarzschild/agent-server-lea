package runtime

import (
	"context"

	"gitlab.ouc-online.com.cn/aibase/agentloop/permissions"
)

// PlatformResolver 教会引擎的权限层认识**我们自己的**工具。
//
// ## 规则一句话
//
// 规划态只允许**服务端内置的只读工具** + **联网调研**，其余一律挡住。
//
//	放行  Read / Glob / 只读 shell        引擎原生，DefaultResolver 判的
//	放行  WebSearch / WebFetch           引擎原生，network 不算 mutating
//	放行  kb_search / memory_search / get_plan    我们的只读工具
//	放行  save_plan / update_plan_step   规划态的**产出**，挡了就交不出计划
//	挡住  Write / Edit / Bash            引擎原生
//	挡住  code_interpreter               沙箱里能写文件、发请求、改产物
//	挡住  memory_add                     写用户跨会话的长期记忆
//	挡住  其余任何名字                    租户自定义工具 + MCP，读写意图无从判断
//
// 这跟 Claude Code 的选择一致 —— 它的 plan 模式也是"MCP 工具全挡，除非显式列进
// permissions"（见 anthropics/claude-code#78085）。区别是我们多给了 WebSearch/
// WebFetch，所以规划阶段不至于完全没有外部信息来源。
//
// ## 不装它会怎样（实测）
//
// 引擎的 DefaultResolver 只认它自己那批工具名，其余一律
// `Operation: OperationUnknown`，而 `isMutatingAction` 对 unknown 走 default 分支
// 返回 false —— 也就是**不算会改东西**。于是 plan 模式在我们这儿的实际效果是：
//
//	mode=flight  tool=Write                 op=write    deny   plan_mode
//	mode=flight  tool=Edit                  op=edit     deny   plan_mode
//	mode=flight  tool=Bash                  op=execute  deny   plan_mode
//	mode=flight  tool=code_interpreter      op=unknown  allow  flight_mode  ← 沙箱里随便写
//	mode=flight  tool=memory_add            op=unknown  allow  flight_mode  ← 写长期记忆
//	mode=flight  tool=some_tenant_write_tool op=unknown allow  flight_mode  ← 所有网关工具
//
// 只挡住三个引擎原生工具，平台自己那一整套全通 —— 而"规划态不改任何东西"正是
// plan 模式对用户的承诺。
//
// ## 为什么不能改用 safe 模式了事
//
// 同一个探针在 safe 下所有平台工具都是 `deny unknown_tool`，agent 直接瘫。
// 问题不在模式，在**分类**：引擎不知道 kb_search 是只读、code_interpreter 会执行。
//
// ## 三条分支，最后一条是关键
//
//	1. 我们自己的工具   → 下面这几张表显式分类
//	2. 引擎原生的工具   → 交给 DefaultResolver（Read/Write/Edit/Glob/Bash/Web*/AskUser…）
//	3. 其余任何名字     → DefaultResolver 会给 unknown，我们把它**升级成 External**
//
// 第 3 条覆盖的是租户在 lea-config 里自定义的网关工具（DynamicTool）和 MCP 工具 ——
// 名字是运行时才知道的，数量不定，读写意图我们无从判断。保守当成"会改外面的东西"，
// 于是规划态挡住、执行态照常（flight 会旁路策略层）。
//
// 用"升级 unknown"而不是"枚举引擎原生工具名"，是因为后者会漂：引擎加一个工具、或者
// 我们多注册一个同名工具，名单就少一项，而少一项的表现是那个工具在规划态下悄悄放行。
// 反过来这个写法的失败模式是"多挡了一个"，模型会收到明确的拒绝并且能报告出来。
//
// ## 被挡住的租户工具怎么办
//
// 不特殊处理。模型会收到明确的拒绝，它该做的是**把"去查 X"写进计划的第一步** ——
// 那一步在执行阶段跑，那时没有任何限制。用户审批的是"先查课程再排路径"这个方案，
// 而不是查出来的结果；写方案时不需要先把资料查完，只需要知道该查什么。
//
// 想让某个只读的租户工具在规划态可用，正确的做法是给 tools 表加一列
// read_only 由建工具的人声明 —— 那条信息只有他知道，名字和 schema 都判断不出来。
// 记在 PLAN_MODE.md 的待办里，触发条件是真出现"既要规划、又只能靠自己 API 调研"
// 的诉求。
type PlatformResolver struct {
	fallback permissions.DefaultResolver
}

// 只读：规划态需要它们做调研，执行态也无害。
var readOnlyPlatformTools = map[string]bool{
	"kb_search":     true,
	"memory_search": true,
	"get_plan":      true,
}

// 平台自己的元数据：不碰用户的工作空间，也不碰外部世界。
//
// save_plan / update_plan_step **必须**落在这一类：save_plan 正是规划态的产出，
// 把它当成写操作挡掉，plan 模式就交不出计划来了。
// 这跟引擎把自己的 TodoWrite / Remember 判成 OperationManage 是同一条线。
var metadataPlatformTools = map[string]bool{
	"save_plan":        true,
	"update_plan_step": true,
}

func (r PlatformResolver) Resolve(ctx context.Context, req permissions.ResolveRequest) (permissions.Action, error) {
	switch {
	case readOnlyPlatformTools[req.ToolName]:
		return permissions.Action{
			ToolName:  req.ToolName,
			Operation: permissions.OperationRead,
			Risk:      permissions.RiskLow,
		}, nil

	case metadataPlatformTools[req.ToolName]:
		return permissions.Action{
			ToolName:  req.ToolName,
			Operation: permissions.OperationManage,
			Risk:      permissions.RiskLow,
		}, nil

	case req.ToolName == "memory_add":
		// 刻意跟引擎对自己 Remember 的判定（Manage）不同。
		//
		// 引擎那个写的是本地一份 memory.md，是它自己的草稿；我们这个写的是用户**跨
		// 会话的长期记忆**，下一个会话、别的 agent 都会读到。规划态承诺"什么都不改"，
		// 那就包括这个。
		return permissions.Action{
			ToolName:  req.ToolName,
			Operation: permissions.OperationWrite,
			Risk:      permissions.RiskMedium,
		}, nil

	case req.ToolName == "code_interpreter":
		// 沙箱里跑任意代码：能写文件、能发网络请求、能改 S3 产物。规划态必须挡。
		return permissions.Action{
			ToolName:  req.ToolName,
			Operation: permissions.OperationExecute,
			Risk:      permissions.RiskHigh,
		}, nil
	}

	action, err := r.fallback.Resolve(ctx, req)
	if err != nil {
		// 解析失败要原样往上传：Service 会 fail-closed 拒掉，而那是对的。
		return action, err
	}
	if action.Operation == permissions.OperationUnknown {
		// 认不出来的都是租户自定义的网关工具或 MCP 工具。当成会改外面的东西。
		action.Operation = permissions.OperationExternal
		if action.Risk == "" {
			action.Risk = permissions.RiskHigh
		}
	}
	return action, nil
}

// NewPermissionService 建一个装了 PlatformResolver 的权限服务，交给
// engine.Options.Permissions。
//
// **每个会话一个**，不要做成进程级单例：plan 模式是这个服务上的一个原子量
// （SetPlanMode），共享一个实例会让一个会话进规划态时把同一个 pod 上所有会话
// 一起挡住。
//
// 一个会话内它是共享的，而这正是想要的：deriveExpertBase 派生专家会话时只换了
// ExtraTools 和 ToolRuntime，Permissions 这个**指针**原样继承下去 —— 所以团长进
// 规划态时，它的专家和子代理跟着一起被挡住。不注入自己的服务时引擎会给每个会话
// 各建一个，那时团长的 plan 模式管不到专家。
func NewPermissionService(mode string) *permissions.Service {
	return permissions.NewService(permissions.Options{
		Mode:     mode,
		Resolver: PlatformResolver{},
	})
}
