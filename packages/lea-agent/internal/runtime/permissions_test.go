package runtime

import (
	"context"
	"encoding/json"
	"testing"

	engine "gitlab.ouc-online.com.cn/aibase/agentloop"
	"gitlab.ouc-online.com.cn/aibase/agentloop/permissions"
)

// 这个文件测的是**规划态到底挡住了什么**，也就是 plan 模式对用户的那句承诺
// （"研究任务并给出计划，不做任何改动"）在我们的工具集上是否成立。
//
// 断言的是最终 Decision 而不是 Operation 分类：分类只是手段，真正会出事的是
// "以为挡住了其实没挡"。所以每一条都走完整的 AuthorizeTool 管线。

func decide(t *testing.T, svc *permissions.Service, name, input string) permissions.Decision {
	t.Helper()
	_, d := svc.AuthorizeTool(context.Background(), permissions.ResolveRequest{
		ToolName: name, Input: json.RawMessage(input),
	})
	return d
}

func planModeService(t *testing.T) *permissions.Service {
	t.Helper()
	svc := NewPermissionService("flight") // 线上就是 flight
	svc.SetPlanMode(true)
	return svc
}

// 规划态必须挡住的：任何会改工作空间、改用户长期状态、或者碰外部世界的东西。
//
// 这五条里有四条在装 PlatformResolver 之前是**放行**的（实测 allow/flight_mode），
// 因为 DefaultResolver 把它们全判成 unknown 而 unknown 不算 mutating。
func TestPlanModeBlocksMutatingTools(t *testing.T) {
	svc := planModeService(t)

	for _, c := range []struct{ name, input string }{
		{"Write", `{"path":"a.txt","content":"x"}`},
		{"Edit", `{"path":"a.txt","old_string":"a","new_string":"b"}`},
		{"Bash", `{"command":"rm -rf /tmp/x"}`},
		// 写的是跨会话的长期记忆，下个会话和别的 agent 都会读到
		{"memory_add", `{"content":"记住这个"}`},
		// 往用户的长期文件库里塞一份成品：占配额、在所有对话里都看得到。
		// 跟 save_plan 分在两边 —— 计划是规划态的产出，成品是执行态的。
		{"deliver_artifact", `{"path":"output/a.pptx","title":"报告"}`},
		// 租户在 lea-config 里自定义的网关工具 —— 名字运行时才知道，读写意图无从判断，
		// 保守当成会改外面的东西
		{"send_email", `{"to":"x@y.z"}`},
		{"jira_create_issue", `{"title":"x"}`},
		{"mcp_7_create_page", `{"title":"x"}`},
	} {
		t.Run(c.name, func(t *testing.T) {
			if d := decide(t, svc, c.name, c.input); d.FinalEffect != permissions.EffectDeny {
				t.Fatalf("规划态没挡住 %s: effect=%s reason=%s", c.name, d.FinalEffect, d.Reason)
			}
		})
	}
}

// 规划态必须放行的：调研要用的只读工具，以及计划自己那套。
//
// 挡错这一侧的后果比漏挡更直接 —— 挡住 save_plan，plan 模式就交不出计划；
// 挡住 kb_search，它就没法调研。
func TestPlanModeAllowsResearchAndPlanTools(t *testing.T) {
	svc := planModeService(t)

	for _, c := range []struct{ name, input string }{
		{"Read", `{"path":"a.txt"}`},
		{"Glob", `{"pattern":"**/*.go"}`},
		{"kb_search", `{"query":"认证模块"}`},
		{"memory_search", `{"query":"偏好"}`},
		{"get_plan", `{}`},
		// 这两个是规划态的**产出**，挡掉等于 plan 模式不能用
		{"save_plan", `{"content":"计划","steps":["一"]}`},
		{"update_plan_step", `{"step":1,"status":"completed"}`},
		// 追问用户没有副作用，而规划完要靠它请审批
		{"AskUser", `{"question":"同意吗？","options":["同意","修改"]}`},
		// 联网调研。引擎判 OperationNetwork，而 network 不算 mutating —— 这是有意的，
		// 引擎自己的 plan-mode prompt 就写着可以 "web fetches"。
		//
		// 对一个只配了业务 API、没有知识库的 agent，这两个是规划阶段唯一的外部信息
		// 来源（租户自定义工具在规划态按 External 挡住）。挡掉它们的话那类 agent
		// 在规划态就真的无工具可用了。
		{"WebSearch", `{"query":"课程设计方法"}`},
		{"WebFetch", `{"url":"https://example.com/doc"}`},
	} {
		t.Run(c.name, func(t *testing.T) {
			if d := decide(t, svc, c.name, c.input); d.FinalEffect == permissions.EffectDeny {
				t.Fatalf("规划态误挡了 %s: reason=%s", c.name, d.Reason)
			}
		})
	}
}

// 反向：不在规划态时，上面那批写操作一个都不能被挡。
//
// 这条守的是"分类写过头"——比如把 kb_search 判成 External，那执行态下它会先被策略
// 层拒、再被 flight 放行，看起来没事；但一旦有人把模式从 flight 调开，整条工具链就
// 停摆。更直接的是：写错成永久 deny 的话，平台在执行态直接不可用。
func TestExecutionModeBlocksNothing(t *testing.T) {
	svc := NewPermissionService("flight") // 没开 plan 模式

	for _, c := range []struct{ name, input string }{
		{"Write", `{"path":"a.txt","content":"x"}`},
		{"Bash", `{"command":"go test ./..."}`},
		{"memory_add", `{"content":"x"}`},
		{"kb_search", `{"query":"x"}`},
		{"save_plan", `{"content":"x","steps":[]}`},
		{"deliver_artifact", `{"path":"output/a.pptx","title":"报告"}`},
		{"send_email", `{"to":"x@y.z"}`},
	} {
		t.Run(c.name, func(t *testing.T) {
			if d := decide(t, svc, c.name, c.input); d.FinalEffect == permissions.EffectDeny {
				t.Fatalf("执行态挡住了 %s: reason=%s", c.name, d.Reason)
			}
		})
	}
}

// 认不出来的工具名要被升级成 External（→ 规划态算 mutating），而不是留在 unknown。
//
// 用"升级 unknown"而不是"枚举引擎原生工具名"是有意的：后者会漂，引擎加一个工具或者
// 我们多注册一个，名单就少一项，而少一项的表现是那个工具在规划态下悄悄放行。
func TestUnknownToolsAreTreatedAsExternal(t *testing.T) {
	action, _ := PlatformResolver{}.Resolve(context.Background(), permissions.ResolveRequest{
		ToolName: "brand_new_tenant_tool", Input: json.RawMessage(`{}`),
	})
	if action.Operation != permissions.OperationExternal {
		t.Fatalf("未知工具应当升级成 External，实际 %s", action.Operation)
	}
}

// 引擎原生工具仍然由 DefaultResolver 分类 —— 我们只是在它前面加了一层，不是替掉它。
// 挂了的表现是 Bash 的命令分类（只读 pipeline 放行那套）整个失效。
func TestEngineNativeToolsKeepDefaultClassification(t *testing.T) {
	r := PlatformResolver{}
	for _, c := range []struct {
		name, input string
		want        permissions.Operation
	}{
		{"Read", `{"path":"a.txt"}`, permissions.OperationRead},
		{"Write", `{"path":"a.txt","content":"x"}`, permissions.OperationWrite},
		{"Edit", `{"path":"a.txt","old_string":"a","new_string":"b"}`, permissions.OperationEdit},
		{"Bash", `{"command":"ls"}`, permissions.OperationExecute},
		{"AskUser", `{"question":"x"}`, permissions.OperationManage},
	} {
		t.Run(c.name, func(t *testing.T) {
			action, err := r.Resolve(context.Background(), permissions.ResolveRequest{
				ToolName: c.name, Input: json.RawMessage(c.input),
			})
			if err != nil {
				t.Fatalf("Resolve: %v", err)
			}
			if action.Operation != c.want {
				t.Fatalf("%s 分类成了 %s，期望 %s", c.name, action.Operation, c.want)
			}
		})
	}
}

// 规划态下的只读 shell 仍然可用 —— 引擎特意为此开了口子（ls / git status / dir|findstr
// 之类）。我们这一层不能把它堵上。
func TestPlanModeStillAllowsReadOnlyShell(t *testing.T) {
	svc := planModeService(t)
	for _, cmd := range []string{"ls -la", "git status", "git diff"} {
		if d := decide(t, svc, "Bash", `{"command":"`+cmd+`"}`); d.FinalEffect == permissions.EffectDeny {
			t.Errorf("规划态挡住了只读命令 %q: reason=%s", cmd, d.Reason)
		}
	}
}

// 权限服务是**指针**，所以按值复制 Options 的地方会共享它 —— 引擎派生专家会话正是
// 这么做的（deriveExpertBase 里 `opts := baseOpts`，只清了 ExtraTools、换了
// ToolRuntime，Permissions 原样带过去）。
//
// 这条钉住的是那个共享属性本身：团长 SetPlanMode(true) 之后，专家和子代理会话
// 用的是同一个服务，所以跟着一起被挡。不共享的话（比如哪天有人给我们这侧加了
// 一句 opts.Permissions = nil，或者干脆不注入让引擎每个会话各建一个），团长进
// 规划态时它派出去的专家仍然能写文件 —— 而那个漏法不会有任何报错。
func TestPermissionServiceIsSharedByValueCopiedOptions(t *testing.T) {
	svc := NewPermissionService("flight")
	opts := engine.Options{Permissions: svc}

	derived := opts          // deriveExpertBase 的第一句就是这个
	derived.ExtraTools = nil // 它清的是 ExtraTools，不是 Permissions

	svc.SetPlanMode(true)

	if derived.Permissions != svc {
		t.Fatal("派生出来的 Options 没共享权限服务 —— 专家不会跟着进规划态")
	}
	d := decide(t, derived.Permissions, "Write", `{"path":"a.txt","content":"x"}`)
	if d.FinalEffect != permissions.EffectDeny {
		t.Fatalf("专家那份没进规划态: effect=%s reason=%s", d.FinalEffect, d.Reason)
	}
}
