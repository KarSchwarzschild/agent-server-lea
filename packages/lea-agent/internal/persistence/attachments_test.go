package persistence

import (
	"strings"
	"testing"
)

func TestBuildContentWithAttachments(t *testing.T) {
	t.Run("no attachments passes message through", func(t *testing.T) {
		got := BuildContentWithAttachments("你好", nil)
		if got != "你好" {
			t.Fatalf("got %q", got)
		}
	})

	t.Run("single attachment with extracted text", func(t *testing.T) {
		atts := []Attachment{{ID: 1, Filename: "note.txt", ExtractedText: "abc"}}
		got := BuildContentWithAttachments("总结一下", atts)
		want := "[文件: note.txt]\n--- 内容 ---\nabc\n--- 内容结束 ---\n\n总结一下"
		if got != want {
			t.Fatalf("got %q want %q", got, want)
		}
	})

	t.Run("missing extracted text falls back to placeholder", func(t *testing.T) {
		atts := []Attachment{{ID: 2, Filename: "image.png", ExtractedText: ""}}
		got := BuildContentWithAttachments("看看", atts)
		if !strings.Contains(got, "[文件: image.png（无法提取文本内容）]") {
			t.Fatalf("got %q", got)
		}
		if !strings.HasSuffix(got, "\n\n看看") {
			t.Fatalf("suffix wrong: %q", got)
		}
	})

	t.Run("multiple attachments joined by blank line", func(t *testing.T) {
		atts := []Attachment{
			{ID: 1, Filename: "a.txt", ExtractedText: "AAA"},
			{ID: 2, Filename: "b.txt", ExtractedText: "BBB"},
		}
		got := BuildContentWithAttachments("对比一下", atts)
		if strings.Count(got, "--- 内容结束 ---") != 2 {
			t.Fatalf("expected 2 blocks: %q", got)
		}
		// b.txt 块跟真实消息之间必须只有一个空行
		if !strings.HasSuffix(got, "BBB\n--- 内容结束 ---\n\n对比一下") {
			t.Fatalf("tail wrong: %q", got)
		}
	})

	t.Run("deriveTitle round-trip: preamble is strippable", func(t *testing.T) {
		atts := []Attachment{{ID: 1, Filename: "x.txt", ExtractedText: "irrelevant body"}}
		content := BuildContentWithAttachments("请分析成绩表", atts)
		if got := deriveTitle(content); got != "请分析成绩表" {
			t.Fatalf("deriveTitle after Build = %q, want %q", got, "请分析成绩表")
		}
	})
}
