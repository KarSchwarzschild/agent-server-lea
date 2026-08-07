package persistence

import "testing"

func TestDeriveTitle(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{"empty", "", "新对话"},
		{"whitespace only", "  \n\t  ", "新对话"},
		{"short chinese", "帮我梳理一下最近的教育技术趋势", "帮我梳理一下最近的教育技术趋势"},
		{"trim", "  hello world  ", "hello world"},
		{
			"long chinese cuts at rune boundary",
			"这是一个非常长的中文消息用来验证按 rune 而不是按 byte 截取的行为不会破坏字符",
			// 24 rune 截断
			"这是一个非常长的中文消息用来验证按 rune 而",
		},
		{
			"strip attachment preamble",
			"[文件: 期末成绩.xlsx]\n--- 内容 ---\n<binary noise>\n--- 内容结束 ---\n\n帮我分析一下这份成绩",
			"帮我分析一下这份成绩",
		},
		{
			"no attachment marker leaves message intact",
			"随便说一句 --- 内容 --- 但没有结束标",
			"随便说一句 --- 内容 --- 但没有结束标",
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := deriveTitle(c.in); got != c.want {
				t.Fatalf("deriveTitle(%q) = %q, want %q", c.in, got, c.want)
			}
		})
	}
}
