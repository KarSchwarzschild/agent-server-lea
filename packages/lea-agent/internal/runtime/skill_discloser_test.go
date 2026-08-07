package runtime

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"gitlab.ouc-online.com.cn/aibase/agentloop/skill"
	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
)

// 工具名必须是 "Skill"：引擎在 build.go 的 skillDiscloser 里显式拒绝改名的替代品，
// 因为 prompt catalog、子代理白名单、DisabledTools 都按这个名字精确匹配。
func TestDiscloserKeepsEngineToolName(t *testing.T) {
	if got := NewGatewaySkillDiscloser(true).Name(); got != skill.ToolName {
		t.Errorf("Name() = %q，必须是 %q，否则引擎装配阶段就会拒绝", got, skill.ToolName)
	}
}

func discloseSkill(t *testing.T, d *GatewaySkillDiscloser, name string) tool.Result {
	t.Helper()
	raw, _ := json.Marshal(map[string]string{"name": name})
	res, err := d.Run(context.Background(), raw, &tool.Context{ToolUseID: "tu-1"}, nil)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	return res
}

func discloserText(r tool.Result) string {
	var b strings.Builder
	for _, c := range r.Content {
		b.WriteString(c.Text)
	}
	return b.String()
}

// 有 bundle 的 skill：正文 + 引擎的 locationHeader + 我们追加的 Read 引导，三样都要在。
//
// 引导是这里唯一的自有逻辑，必要性来自实测：引擎的 locationHeader 只说
// "read them directly"，不点名工具时模型会跳过依赖这些文件的步骤（pptx 的
// 调研/配色/页型规划全被跳过）。
func TestDiscloserAppendsReadGuidanceForBundledSkill(t *testing.T) {
	d := NewGatewaySkillDiscloser(true)
	d.SetSet(skill.NewSet(ToEngineSkills([]SkillDef{{
		Name:         "pptx-generator",
		Description:  "做 PPT",
		SystemPrompt: "BODY-MARKER",
		BundleURI:    "s3://bucket/skills/t/pptx/abc123.zip",
	}})))

	txt := discloserText(discloseSkill(t, d, "pptx-generator"))
	for _, want := range []string{
		"BODY-MARKER",                // 默认披露的正文
		"/opt/skills/pptx-generator", // locationHeader 给的目录（沙箱内路径）
		"先用 Read 读它再继续",              // 我们追加的引导
	} {
		if !strings.Contains(txt, want) {
			t.Errorf("披露结果缺 %q：\n%s", want, txt)
		}
	}
}

// 纯 prompt 型 skill（没打包 reference）不该有读文件的引导 —— 它没有目录，
// 那段引导会让模型去读一个不存在的路径。
func TestDiscloserSkipsGuidanceWithoutBundle(t *testing.T) {
	d := NewGatewaySkillDiscloser(true)
	d.SetSet(skill.NewSet(ToEngineSkills([]SkillDef{{
		Name:         "tone-guide",
		SystemPrompt: "BODY-MARKER",
		// 没有 BundleURI
	}})))

	txt := discloserText(discloseSkill(t, d, "tone-guide"))
	if !strings.Contains(txt, "BODY-MARKER") {
		t.Error("正文仍应披露")
	}
	if strings.Contains(txt, "Read(") {
		t.Errorf("无 bundle 的 skill 不该有读文件引导：\n%s", txt)
	}
}

// 没有文件工具时不给引导 —— 否则模型会去调一个没注册的工具。
func TestDiscloserSkipsGuidanceWhenNoFileTools(t *testing.T) {
	d := NewGatewaySkillDiscloser(false)
	d.SetSet(skill.NewSet(ToEngineSkills([]SkillDef{{
		Name: "pptx-generator", SystemPrompt: "BODY", BundleURI: "s3://b/k/sha.zip",
	}})))

	if txt := discloserText(discloseSkill(t, d, "pptx-generator")); strings.Contains(txt, "Read(") {
		t.Errorf("没有文件工具时不该引导用 Read：\n%s", txt)
	}
}

// 未知 skill 是模型的错，应原样返回引擎的可纠正错误，且**不追加**引导 ——
// 那时它要做的是换个名字，不是去读文件。
func TestDiscloserLeavesErrorResultAlone(t *testing.T) {
	d := NewGatewaySkillDiscloser(true)
	d.SetSet(skill.NewSet(nil))

	res := discloseSkill(t, d, "nonexistent")
	if !res.IsError {
		t.Fatal("未知 skill 应是 is_error 结果")
	}
	if strings.Contains(discloserText(res), "Read(") {
		t.Error("错误结果不该追加读文件引导")
	}
}

// SetSet 必须同时喂给内嵌的默认实现和我们自己的副本。只设一边的后果是：
// 要么默认实现解析不出 skill（正文丢了），要么我们查不到 Path（引导丢了）。
// 两种都不会编译失败。
func TestSetSetFeedsBothCopies(t *testing.T) {
	d := NewGatewaySkillDiscloser(true)
	if res := discloseSkill(t, d, "late"); !res.IsError {
		t.Fatal("空 set 时应报未知 skill")
	}
	d.SetSet(skill.NewSet(ToEngineSkills([]SkillDef{{
		Name: "late", SystemPrompt: "BODY-MARKER", BundleURI: "s3://b/k/sha.zip",
	}})))
	txt := discloserText(discloseSkill(t, d, "late"))
	if !strings.Contains(txt, "BODY-MARKER") {
		t.Error("内嵌实现没拿到新 set —— 正文丢了")
	}
	if !strings.Contains(txt, "先用 Read 读它再继续") {
		t.Error("自己那份副本没拿到新 set —— 引导丢了")
	}
}

// ── ToEngineSkills ──────────────────────────────────────────

// Path 必须指向沙箱内路径而不是 lea-agent 本机路径：它只用于给模型显示
// "Skill directory: X"，模型据此去读引用文件，而读文件走 Read 落在共享存储上。
// 填本机路径会让模型去读一个它访问不到的目录。
func TestToEngineSkillsUsesSandboxPath(t *testing.T) {
	out := ToEngineSkills([]SkillDef{
		{Name: "pptx-generator", BundleURI: "s3://b/k/sha.zip"},
		{Name: "tone-guide"}, // 无 bundle
	})
	if len(out) != 2 {
		t.Fatalf("应转出 2 个，实际 %d", len(out))
	}
	if got, want := out[0].Path, "/opt/skills/pptx-generator/SKILL.md"; got != want {
		t.Errorf("Path = %q，期望 %q", got, want)
	}
	// 必须是正斜杠 —— 这是 POSIX 沙箱路径，Windows 上 filepath.Join 会给反斜杠
	if strings.Contains(out[0].Path, "\\") {
		t.Errorf("Path 含反斜杠，沙箱里解析不了：%q", out[0].Path)
	}
	// 无 bundle 的留空，locationHeader 会因此整段跳过
	if out[1].Path != "" {
		t.Errorf("无 bundle 的 skill Path 应为空，实际 %q", out[1].Path)
	}
}

// 空输入要返回非 nil 的空 slice：nil 在引擎里的意思是"去扫磁盘"，而 CWD 是 /tmp。
func TestToEngineSkillsReturnsNonNilForEmpty(t *testing.T) {
	got := ToEngineSkills(nil)
	if got == nil {
		t.Fatal("必须返回非 nil —— nil 会让引擎去扫 /tmp")
	}
	if len(got) != 0 {
		t.Error("空输入应转出 0 个")
	}
}

// ── 名册 ────────────────────────────────────────────────────

// 名册非 nil 且含一个继承全部工具的执行者。nil 会让引擎去扫磁盘并合并它内置的六个
// 代码场景人设；名册里没有 inherit-all 的执行者则专家派活时子代理没工具可用。
func TestAgentRosterHasInheritAllExecutor(t *testing.T) {
	roster := BuildAgentRoster()
	if len(roster) == 0 {
		t.Fatal("名册不能为空 —— 那样引擎连 Task 工具都不注册")
	}
	found := false
	for _, a := range roster {
		if a.Name != SkillRunnerName {
			continue
		}
		found = true
		if !a.InheritsAllTools() {
			t.Error("执行者必须继承全部工具 —— 它要能加载 skill、读写工作空间、跑代码")
		}
		if strings.TrimSpace(a.Prompt) == "" || strings.TrimSpace(a.Description) == "" {
			t.Error("Description 进 prompt 目录、Prompt 是人设，都不能空")
		}
	}
	if !found {
		t.Errorf("名册里缺 %q", SkillRunnerName)
	}
}

// ── 大 skill 在专家会话里改成派活（体积分档）────────────────────────
//
// 起因是实测：文档处理专家自己加载 pptx-generator 读完全套 reference（约 10k tok），
// **然后才**决定派子代理 —— 子代理又重新加载一遍。那 10k 留在专家上下文里，之后
// 二三十轮每轮重传。这是三层方案比单 agent 贵 1.8× 的主要来源。
//
// 判定必须是规则而不是提示词引导：同一处试过加"缺内容就该读"的判据，实测无效
// （见 skill_discloser.go 里那段）。

func discloseAs(t *testing.T, d *GatewaySkillDiscloser, name string, expert bool) tool.Result {
	t.Helper()
	raw, _ := json.Marshal(map[string]string{"name": name})
	tctx := &tool.Context{ToolUseID: "tu-1"}
	if expert {
		tctx.Metadata = map[string]any{tool.MetadataExpertSession: true}
	}
	res, err := d.Run(context.Background(), raw, tctx, nil)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	return res
}

func bulkSkillSet() *skill.Set {
	return skill.NewSet(ToEngineSkills([]SkillDef{
		{
			Name:         "pptx-generator",
			Description:  "做 PPT",
			SystemPrompt: strings.Repeat("X", bulkSkillBodyBytes+1),
			BundleURI:    "s3://b/skills/t/pptx/abc.zip",
		},
		{
			Name:         "internal-comms",
			Description:  "写内部沟通材料",
			SystemPrompt: "SMALL-BODY-MARKER",
			BundleURI:    "s3://b/skills/t/comms/def.zip",
		},
	}))
}

func TestExpertGetsDeferralInsteadOfBulkBody(t *testing.T) {
	d := NewGatewaySkillDiscloser(true)
	d.SetSet(bulkSkillSet())

	txt := discloserText(discloseAs(t, d, "pptx-generator", true))
	if strings.Contains(txt, strings.Repeat("X", 64)) {
		t.Fatal("大 skill 的正文进了专家上下文 —— 拦截失效，这笔开销已经付了")
	}
	for _, want := range []string{"请派给子代理执行", "Task(", SkillRunnerName} {
		if !strings.Contains(txt, want) {
			t.Errorf("替代回复缺 %q：\n%s", want, txt)
		}
	}
}

// 小 skill 照常给正文：起子代理有固定成本（一次完整会话 + 它自己重读任务），
// 为 385 tok 的正文派活是净亏。
func TestExpertStillGetsSmallSkillBody(t *testing.T) {
	d := NewGatewaySkillDiscloser(true)
	d.SetSet(bulkSkillSet())

	txt := discloserText(discloseAs(t, d, "internal-comms", true))
	if !strings.Contains(txt, "SMALL-BODY-MARKER") {
		t.Fatalf("小 skill 不该被拦：\n%s", txt)
	}
	if strings.Contains(txt, "请派给子代理执行") {
		t.Error("小 skill 不该要求派活")
	}
}

// 这条是整套方案的支点：专家派下去的子代理拿不到 expert_session 标记
// （childToolContext 不复制 Metadata），所以它调 Skill 必须拿到完整正文 ——
// 否则活没人干，两边都在互相推。
func TestSubagentGetsFullBulkBody(t *testing.T) {
	d := NewGatewaySkillDiscloser(true)
	d.SetSet(bulkSkillSet())

	txt := discloserText(discloseAs(t, d, "pptx-generator", false))
	if !strings.Contains(txt, strings.Repeat("X", 64)) {
		t.Fatalf("子代理没拿到正文，活就没人能干：\n%s", txt[:min(len(txt), 400)])
	}
	if strings.Contains(txt, "请派给子代理执行") {
		t.Error("子代理不该再被要求往下派 —— 那会无限推诿")
	}
}

// 团长自己调 Skill 也照常拿正文：它不是专家会话，没有标记。
func TestLeaderGetsFullBulkBody(t *testing.T) {
	d := NewGatewaySkillDiscloser(true)
	d.SetSet(bulkSkillSet())

	txt := discloserText(discloseSkill(t, d, "pptx-generator"))
	if !strings.Contains(txt, strings.Repeat("X", 64)) {
		t.Fatal("团长/单 agent 会话不该被拦 —— 它没有子代理可派时就干不了活了")
	}
}
