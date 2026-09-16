package persistence

import (
	"strings"
	"testing"
)

// 这组测的是注入段的**结构**（marker、分隔、能被 deriveTitle 剥掉）；
// 每种场景该说什么话在 attachment_inject_test.go 里逐条验。
//
// 注入段现在只有元信息和路径 —— 正文由 Read 工具按需取，所以以前那些关于
// `--- 内容 ---` 正文块的断言随之消失。
func TestBuildContentWithAttachments(t *testing.T) {
	t.Run("no attachments passes message through", func(t *testing.T) {
		got := BuildContentWithAttachments("你好", nil)
		if got != "你好" {
			t.Fatalf("got %q", got)
		}
	})

	t.Run("single attachment gives marker and path", func(t *testing.T) {
		atts := []Attachment{{
			ID: 1, Filename: "note.txt", ExtractedText: "abc",
			StagedPath: "aa-note.txt",
		}}
		got := BuildContentWithAttachments("总结一下", atts)

		for _, want := range []string{
			"[文件: note.txt]",
			"/mnt/uploads/aa-note.txt",
			"用 Read 读",
		} {
			if !strings.Contains(got, want) {
				t.Fatalf("缺少 %q，实际：%q", want, got)
			}
		}
		// 正文不进消息 —— 这是这一版的核心改动。
		if strings.Contains(got, "--- 内容 ---") || strings.Contains(got, "abc") {
			t.Fatalf("正文不该注入：%q", got)
		}
		// 用户原话在末尾，与附件段之间恰好一个空行（deriveTitle 按它切）。
		if !strings.HasSuffix(got, "\n\n总结一下") {
			t.Fatalf("尾部结构不对：%q", got)
		}
	})

	t.Run("attachment without staging remains readable by id", func(t *testing.T) {
		atts := []Attachment{{
			ID: 2, Filename: "image.png", MediaType: "image",
			FileKey: "uploads/t/x.png",
		}}
		got := BuildContentWithAttachments("看看", atts)

		if !strings.Contains(got, "[文件: image.png]") {
			t.Fatalf("got %q", got)
		}
		if !strings.Contains(got, "path=attachment:2") {
			t.Fatalf("应提供附件 ID：%q", got)
		}
		// 给了路径就是让模型去开一个不存在的文件。
		if strings.Contains(got, "/mnt/uploads") {
			t.Fatalf("没物化时不该给路径：%q", got)
		}
		if !strings.HasSuffix(got, "\n\n看看") {
			t.Fatalf("suffix wrong: %q", got)
		}
	})

	t.Run("unusable attachment says so regardless of blob state", func(t *testing.T) {
		// 没物化的两种内部状态（有 blob / 没 blob）对模型是同一件事：这个文件用不了。
		// 所以说同一句话 —— FileKey 有没有是我们的实现细节，不是它该分辨的东西。
		for _, name := range []string{"有 blob", "没 blob"} {
			att := Attachment{ID: 3, Filename: "orphan.pdf"}
			if name == "有 blob" {
				att.FileKey = "uploads/t/orphan.pdf"
			}
			got := BuildContentWithAttachments("这是什么", []Attachment{att})
			if !strings.Contains(got, "path=attachment:3") {
				t.Errorf("%s：要给出 Read 入口，得到 %q", name, got)
			}
			if strings.Contains(got, "/mnt/uploads") {
				t.Errorf("%s：没物化不该给路径，得到 %q", name, got)
			}
		}
	})

	t.Run("multiple attachments joined by blank line", func(t *testing.T) {
		atts := []Attachment{
			{ID: 1, Filename: "a.txt", ExtractedText: "AAA", StagedPath: "p-a.txt"},
			{ID: 2, Filename: "b.txt", ExtractedText: "BBB", StagedPath: "q-b.txt"},
		}
		got := BuildContentWithAttachments("对比一下", atts)

		if n := strings.Count(got, "[文件: "); n != 2 {
			t.Fatalf("两个附件应各有一个 marker，得到 %d：%q", n, got)
		}
		// 两段之间一个空行，末段与用户原话之间也是一个空行。
		if !strings.Contains(got, "\n\n[文件: b.txt]") {
			t.Fatalf("附件段之间应有空行：%q", got)
		}
		if !strings.HasSuffix(got, "\n\n对比一下") {
			t.Fatalf("tail wrong: %q", got)
		}
	})

	t.Run("deriveTitle round-trip: preamble is strippable", func(t *testing.T) {
		atts := []Attachment{{
			ID: 1, Filename: "x.txt", ExtractedText: "irrelevant body",
			StagedPath: "r-x.txt",
		}}
		content := BuildContentWithAttachments("请分析成绩表", atts)
		if got := deriveTitle(content); got != "请分析成绩表" {
			t.Fatalf("deriveTitle after Build = %q, want %q", got, "请分析成绩表")
		}
	})
}
