package runtime

import (
	"encoding/json"
	"strings"
	"testing"

	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
)

// get_plan 必须真的实现 ResultRetentionProvider。
//
// 这是整个"拉取式计划"设计的支点，也是最容易静默失效的一处：那是个**可选**接口，
// 方法名拼错、签名写歪、或者把方法挂到值接收者上，全都照常编译，引擎只是
// `t.(ResultRetentionProvider)` 断言失败然后退回零值 Retention —— 于是计划全文
// 每调一次就在历史里留一份，永不脱水。症状是上下文慢慢变贵，没有任何报错。
func TestGetPlanToolDeclaresRetention(t *testing.T) {
	var tl tool.Tool = &GetPlanTool{tenantID: "t1", sessionID: "sess-a"}

	provider, ok := tl.(tool.ResultRetentionProvider)
	if !ok {
		t.Fatal("GetPlanTool 没实现 ResultRetentionProvider —— 计划全文会在历史里堆积")
	}

	r := provider.ResultRetention(json.RawMessage(`{}`))
	if !r.Reproducible {
		t.Error("Reproducible 必须为 true，否则陈旧的那几份不允许被脱水")
	}
	if r.Identity == "" {
		t.Error("Identity 为空表示'永不合并'，那样每次调用都会各留一份全文")
	}

	// 同一个会话的两次调用必须是同一个 Identity —— 引擎正是靠它判断"后一次取代
	// 前一次"。不同会话则必须不同，否则两个会话的计划会互相顶掉。
	other := &GetPlanTool{tenantID: "t1", sessionID: "sess-b"}
	if r.Identity != provider.ResultRetention(json.RawMessage(`{}`)).Identity {
		t.Error("同一会话两次调用的 Identity 不一致 —— 旧的那份不会被取代")
	}
	if r.Identity == other.ResultRetention(json.RawMessage(`{}`)).Identity {
		t.Error("不同会话共用了 Identity —— 会互相顶掉对方的计划")
	}
}

// get_plan 不接受任何参数，尤其不能有 session_id。
//
// 工具实例构造时就把租户和会话焊死了，所以模型**结构上**够不到别人的计划。开一个
// 参数出来等于把它换成"每次调用都要记得校验"，而这个库刚因为同一类问题（按名字查
// 工具不带 user_id）修了一整轮。
func TestGetPlanToolTakesNoIdentityParams(t *testing.T) {
	schema := (&GetPlanTool{}).InputSchema()
	for name := range schema.Properties {
		t.Errorf("get_plan 不该有参数，却有 %q", name)
	}
}

// update_plan_step 是读-改-写同一行 JSON，声明成并发安全会丢更新。
//
// 引擎会并行调所有 IsConcurrencySafe() 为 true 的工具（DynamicTool 硬编码 true 就
// 踩过）。两次调用各自读到改前的步骤表，后写的覆盖先写的：模型标了两步、库里只留
// 一步。不报错，只表现为"进度偶尔少一格"。
func TestUpdatePlanStepIsNotConcurrencySafe(t *testing.T) {
	if (&UpdatePlanStepTool{}).IsConcurrencySafe() {
		t.Fatal("update_plan_step 声明成并发安全会丢更新")
	}
}

// 渲染给模型的计划要带序号，因为 update_plan_step 用的就是这个序号 —— 模型从这里
// 读到 "3."，标进度时才知道该传 3。序号和工具参数对不上是个静默错位。
func TestRenderPlanNumbersStepsFromOne(t *testing.T) {
	out := renderPlan(persistence.Plan{
		Approved: "计划正文",
		ApprovedSteps: []persistence.PlanStep{
			{Title: "调研", Status: persistence.StepCompleted},
			{Title: "改代码", Status: persistence.StepInProgress},
		},
	})
	for _, want := range []string{"计划正文", "1. [已完成] 调研", "2. [进行中] 改代码", "1/2", "update_plan_step"} {
		if !strings.Contains(out, want) {
			t.Errorf("渲染结果里缺 %q:\n%s", want, out)
		}
	}
}

// 有待批准的修订时必须说清它还没生效，否则模型会照着下面那份跑 —— 而用户还没同意。
func TestRenderPlanMarksPendingRevision(t *testing.T) {
	out := renderPlan(persistence.Plan{Approved: "旧计划", Draft: "新计划"})
	if !strings.Contains(out, "旧计划") || !strings.Contains(out, "新计划") {
		t.Fatalf("两份都该出现:\n%s", out)
	}
	if !strings.Contains(out, "尚未生效") {
		t.Errorf("没说清修订版还没生效，模型会照着它跑:\n%s", out)
	}
}
