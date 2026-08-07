package session

import "testing"

// contextWindowFor 决定引擎何时开始回收历史。它填 0 的后果是 Reduce 整个不跑
// （repl/session.go 的 `if s.maxContextTokens <= 0` 直接返回），而那不会编译失败、
// 不会有任何报错 —— 只表现为"上下文莫名其妙涨得快"。所以用测试钉住它永远返回正数。
func TestContextWindowNeverZero(t *testing.T) {
	for _, model := range []string{
		"qwen3-max", "glm-5.1", "qwen3-8b", "",
		"某个还没出现的模型", "GPT-5-Turbo",
	} {
		if got := contextWindowFor(model); got <= 0 {
			t.Errorf("contextWindowFor(%q) = %d，必须为正 —— 0 会让历史回收整个不跑", model, got)
		}
	}
}

// 小模型必须拿到比缺省小的窗口。给 qwen3-8b 填 128k 的后果是 Reduce 永远不触发
// （它的真实窗口远小于此），然后在模型上限处直接撞墙 —— 这正是"估大了"的失败模式。
func TestSmallModelGetsSmallerWindow(t *testing.T) {
	small := contextWindowFor("qwen3-8b")
	if small >= defaultContextWindow {
		t.Errorf("qwen3-8b 拿到 %d，应当小于缺省 %d", small, defaultContextWindow)
	}
}

// 前缀匹配的意义在于模型名多一个后缀时不掉进缺省分支。这条钉住"同系列新版本自动生效"。
func TestPrefixMatchSurvivesVersionSuffix(t *testing.T) {
	cases := []struct {
		model string
		want  int
	}{
		{"qwen3-max", defaultContextWindow},
		{"qwen3-max-2026-01", defaultContextWindow},
		{"glm-5.1", defaultContextWindow},
		{"glm-4-plus", defaultContextWindow},
		{"qwen3-8b-instruct", 32_000}, // 带后缀也要落进小模型分支
	}
	for _, c := range cases {
		if got := contextWindowFor(c.model); got != c.want {
			t.Errorf("contextWindowFor(%q) = %d, 期望 %d", c.model, got, c.want)
		}
	}
}

// 大小写不该影响判断 —— 模型名来自 DB，谁都可能填成 Qwen3-Max。
func TestContextWindowIsCaseInsensitive(t *testing.T) {
	if contextWindowFor("QWEN3-8B") != contextWindowFor("qwen3-8b") {
		t.Error("大小写不同的同一模型应得到同一窗口")
	}
}
