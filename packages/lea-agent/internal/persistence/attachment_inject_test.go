package persistence

import (
	"strings"
	"testing"
)

// 注入文本现在只做一件事：**告诉模型有这个文件、它在哪、怎么取内容**。正文不再拼进
// 消息（由 Read 工具按需取，见 runtime/file_tools.go 的 readUpload）。
//
// 所以这里断言的是"路由信息完整且不误导"，而不是"该说的话都说到了" —— 后者是上一版的
// 契约，那时这段文本要独自承担"模型会怎么处理文件"的全部决定。
//
// 每个测试对应一个真实场景，注释写清"不这么说会怎样"。

func TestInject_NoAttachments(t *testing.T) {
	if got := BuildContentWithAttachments("你好", nil); got != "你好" {
		t.Fatalf("无附件时应原样返回，得到 %q", got)
	}
}

// 场景 1：上传文档问问题 / 出报告。
//
// 三件事必须都在：marker（标题剥离靠它）、路径（取内容和改文件都要）、用户原话。
// 正文**不该**在 —— 那是这一版的核心改动。
func TestInject_GivesPathNotContent(t *testing.T) {
	got := BuildContentWithAttachments("根据这个出一份报告", []Attachment{{
		Filename:      "调研.pdf",
		FileType:      "pdf",
		MediaType:     "text",
		ExtractedText: "第一章 背景……",
		StagedPath:    "a3f19c7e-调研.pdf",
		FileSize:      2048,
		PageCount:     12,
	}})

	mustContain(t, got, "[文件: 调研.pdf", "marker 要在 —— 标题剥离靠它")
	mustContain(t, got, "共 12 页", "有页数就报，让模型知道文件规模")
	mustContain(t, got, "2.0 KB", "报大小，给模型一个'读起来贵不贵'的量感")
	mustContain(t, got, "/mnt/uploads/a3f19c7e-调研.pdf", "要给沙箱路径")
	mustContain(t, got, "根据这个出一份报告", "用户原话要保留")

	mustNotContain(t, got, "第一章 背景……",
		"正文不再注入 —— 模型要内容时自己调 Read")
	mustNotContain(t, got, "--- 内容 ---", "不该有正文块")
}

// 工具名必须是 Read。
//
// 引擎内置工具叫 `Read`（file_tools.go 顶部：子代理白名单按名字**精确匹配**）。
// 写 read_file 的后果是模型去调一个不存在的工具，然后在"没有这个工具"上打转 ——
// 而它手里明明有能用的那个。这个 bug 在这一版的初稿里真的写出来过。
func TestInject_NamesTheRealToolName(t *testing.T) {
	got := BuildContentWithAttachments("总结一下", []Attachment{{
		Filename: "x.pdf", FileType: "pdf", MediaType: "text",
		ExtractedText: "内容", StagedPath: "aa-x.pdf",
	}})

	mustContain(t, got, "用 Read 读", "要点名真实的工具名")
	for _, wrong := range []string{"read_file", "readFile", "read-file", "ReadFile"} {
		mustNotContain(t, got, wrong,
			"工具真名是 Read；写错名字会让模型调一个不存在的工具")
	}
}

// 修改引导必须是**条件式**的。
//
// 同一个 PDF 可能是"照它写份大纲"（要生成新东西）也可能是"改第 3 页"（要原地改）。
// 命令式的"修改方式：…"是在指挥模型做一件用户没要求的事 —— dump 出来的老版本在
// 用户说"写报告"时劝它不要重新生成，而重新生成正是要做的事。
func TestInject_GuidanceIsConditionalNotImperative(t *testing.T) {
	got := BuildContentWithAttachments("根据这份调研写一篇总结报告", []Attachment{{
		Filename: "调研.pdf", FileType: "pdf", MediaType: "text",
		PageCount: 32, ExtractedText: "第一章 市场概况……",
		StagedPath: "a3f1-调研.pdf",
	}})

	mustContain(t, got, "如需修改", "必须是条件式 —— 用户可能只想读它写新东西")
	mustNotContain(t, got, "修改方式：",
		"命令式措辞会在'照这个写报告'这类需求上把模型往错方向推")
}

// 改文件时"别整篇重新生成"要说。
//
// 不说的话模型会照读到的文本重做一份，母版/主题/没被要求改的那些页全丢，而且它会
// 报告"已完成" —— 用户拿到手才发现不是自己那份文件。
//
// 措辞对所有类型通用：不写"母版"这类 PPT 专有词，它对 PDF 和 xlsx 没有意义，而一句
// 用错概念的指令会让模型怀疑自己认错了文件类型。
func TestInject_SaysDontRegenerate(t *testing.T) {
	for _, ext := range []string{"pdf", "xlsx", "docx", "pptx"} {
		got := BuildContentWithAttachments("改一下", []Attachment{{
			Filename: "x." + ext, FileType: ext, MediaType: "text",
			ExtractedText: "内容", StagedPath: "aa-x." + ext,
		}})
		mustContain(t, got, "不要整篇重新生成",
			"不点明的话模型会重做一份，丢掉未改动的部分，还报告成功")
		mustContain(t, got, "保留未涉及的内容", "要说清'别丢掉没让你改的部分'")
		mustContain(t, got, "/workspace/", "要说清产物写哪儿 —— 只有那里会进文件抽屉")
		mustNotContain(t, got, "母版", ext+" 没有母版这个概念（那是 PPT 的词）")
	}
}

// 原件只读要说明，否则模型会尝试原地写然后失败
// （lea-tools 的 _resolve_readable 只在读路径上放行 uploads）。
func TestInject_SaysReadOnly(t *testing.T) {
	got := BuildContentWithAttachments("改一下", []Attachment{{
		Filename: "x.pptx", FileType: "pptx", MediaType: "text",
		ExtractedText: "内容", StagedPath: "aa-x.pptx",
	}})
	mustContain(t, got, "只读", "原件只读要说明")
}

// ── 可配置项绝不能出现在注入文本里 ──────────────────────────────
//
// 这是这块逻辑最要紧的一条约束。区别在于：
//
//	结构性事实（可以说）    改二进制文档要走代码；产物写 /workspace 才进文件抽屉
//	可配置项（不能说）      装了哪个库 / 有没有配 skill / OCR 服务在不在
//
// 说了右边而它恰好不在，模型会**在同一条死路上反复重试**（一直 import 一个装不上的
// 库），而不是换条路。说左边则总是安全的：给出约束和理由，手段由模型在真实环境里
// 自己探明 —— 它看得到 import 报错，我们看不到。
//
// 这条曾经写错过：上一版写"用 python-pptx 读…若该库不可用可先 pip install"。而文档
// 只确认 template 装了 pptx（SANDBOX_NFS_STORAGE.md:148），docx/openpyxl/pypdf 只在
// "未来重打 template"的草稿里。那句"若不可用"就是在给一个无法保证的断言打补丁。
func TestInject_NeverNamesConfigurableThings(t *testing.T) {
	// 具体库名 —— 装没装取决于 sandbox template 版本
	libs := []string{
		"python-pptx", "python-docx", "openpyxl", "pypdf", "pandas",
		"PIL", "Pillow", "pip install", "pytesseract", "ffmpeg",
	}
	// skill 相关 —— skill 走渐进披露，模型自己选；绑没绑、叫什么都随 agent 配置变
	skillWords := []string{"skill", "Skill", "技能"}

	cases := []Attachment{
		{Filename: "a.pptx", FileType: "pptx", MediaType: "text",
			ExtractedText: "x", StagedPath: "s-a.pptx", PageCount: 3},
		{Filename: "b.xlsx", FileType: "xlsx", MediaType: "text",
			ExtractedText: "x", StagedPath: "s-b.xlsx"},
		{Filename: "c.pdf", FileType: "pdf", MediaType: "text",
			PageCount: 20, StagedPath: "s-c.pdf"},
		{Filename: "d.png", FileType: "png", MediaType: "image",
			StagedPath: "s-d.png"},
		{Filename: "e.mp4", FileType: "mp4", MediaType: "video",
			ExtractedText: "字幕", StagedPath: "s-e.mp4"},
		{Filename: "f.zip", FileType: "zip", MediaType: "file",
			StagedPath: "s-f.zip"},
		{Filename: "g.md", FileType: "md", MediaType: "text",
			ExtractedText: "# x", StagedPath: "s-g.md"},
		{Filename: "h.docx", FileType: "docx", MediaType: "text",
			ExtractedText: "x", FileKey: "uploads/t/h.docx"}, // 没物化
	}

	for _, a := range cases {
		t.Run(a.FileType, func(t *testing.T) {
			got := BuildContentWithAttachments("处理一下", []Attachment{a})
			for _, lib := range libs {
				mustNotContain(t, got, lib,
					"库/工具是否可用是可配置项 —— 点名一个不在的会让模型在死路上重试")
			}
			for _, w := range skillWords {
				mustNotContain(t, got, w,
					"skill 走渐进披露且随 agent 配置变，注入文本不能点名")
			}
		})
	}
}

// ## 一套措辞覆盖所有类型
//
// 上一版按类型分了五档（routeCode / routeText / routeVisual / routeMedia /
// routeOpaque），每档一句不同的话。那套东西存在的理由是**注入文本要独自决定模型怎么
// 处理文件** —— 而现在决定权移交给了 Read：它知道自己拿到的是文本、是图片、还是
// 什么都没有，也只有它知道。
//
// 所以注入侧不再需要区分类型，只要保证每种类型都拿到了路径和取法。
func TestInject_UniformAcrossTypes(t *testing.T) {
	for _, tc := range []struct{ name, ext, media string }{
		{"二进制文档", "pptx", "text"},
		{"纯文本", "md", "text"},
		{"表格", "xlsx", "text"},
		{"图片", "png", "image"},
		{"视频", "mp4", "video"},
		{"音频", "mp3", "audio"},
		{"未知格式", "zip", "file"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := BuildContentWithAttachments("处理一下", []Attachment{{
				Filename: "x." + tc.ext, FileType: tc.ext, MediaType: tc.media,
				StagedPath: "aa-x." + tc.ext,
			}})
			mustContain(t, got, "/mnt/uploads/aa-x."+tc.ext, "每种类型都要给路径")
			mustContain(t, got, "用 Read 读", "每种类型都要说怎么取内容")
		})
	}
}

// 每个能力只说一次。重复的指令稀释掉它的分量，而且 dump 里读起来像模板拼接。
func TestInject_NoDuplicateGuidance(t *testing.T) {
	got := BuildContentWithAttachments("统计一下", []Attachment{{
		Filename: "表.xlsx", FileType: "xlsx", MediaType: "text",
		ExtractedText: "a | b", StagedPath: "f9-表.xlsx",
	}})

	for _, phrase := range []string{"/workspace/", "如需修改", "用 Read 读"} {
		if n := strings.Count(got, phrase); n > 1 {
			t.Errorf("%q 说了 %d 次，应只说一次：\n%s", phrase, n, got)
		}
	}
}

// 沙箱没开 / 没共享存储：有 blob 但没物化。
//
// 必须说出来，否则模型会去 Read 一个不存在的路径，然后在一个它无法理解的错误上打转
// （文件不存在？路径错了？权限？）—— 而根因是这个部署形态下物化做不到。
//
// 而且**绝不能给路径** —— 给了就是让它去开一个不存在的文件。
func TestInject_NotStagedProvidesReadID(t *testing.T) {
	got := BuildContentWithAttachments("改一下", []Attachment{{
		ID:            42,
		Filename:      "报告.docx",
		FileType:      "docx",
		MediaType:     "text",
		ExtractedText: "正文……",
		FileKey:       "uploads/t1/abc.docx",
	}})

	mustContain(t, got, "path=attachment:42", "没物化也要提供 Read 入口")
	mustNotContain(t, got, "/mnt/uploads", "没物化时不能给沙箱路径")
	mustContain(t, got, "需要了解内容时用 Read", "应引导模型按需读取")
}

// 页数为 0 时不能报"共 0 页"—— 0 的含义是"这个格式没有页的概念"（md/txt/图片），
// 不是"零页"。报了用户会以为文件坏了。
//
// ⚠️ 从库里读回来的附件 PageCount 恒为 0：chat_attachments **没有 page_count 列**
// （019 只加了 sha256/staged_path，InsertAttachment 的 INSERT 里也没有它）。所以
// "共 N 页"这一支目前只在上传那一刻的内存对象上会走到。
func TestInject_ZeroPageCountNotReported(t *testing.T) {
	got := BuildContentWithAttachments("总结", []Attachment{{
		Filename:      "笔记.md",
		FileType:      "md",
		MediaType:     "text",
		ExtractedText: "# 标题",
		PageCount:     0,
	}})

	mustNotContain(t, got, "共 0 页", "0 页是'无分页概念'，不该报出来")
	mustContain(t, got, "[文件: 笔记.md]", "头一行仍要有")
}

// 大小为 0 时不报 —— 同理，"0 B" 看起来像文件传坏了。
func TestInject_ZeroSizeNotReported(t *testing.T) {
	got := BuildContentWithAttachments("看看", []Attachment{{
		Filename: "x.txt", FileType: "txt", MediaType: "text",
		StagedPath: "aa-x.txt",
	}})
	mustNotContain(t, got, "0 B", "大小未知时不该报 0")
}

// 多附件：每个都要有自己完整的一段，不能互相污染。
// 一个可读一个不可读时，模型必须能分清哪个是哪个。
func TestInject_MultipleAttachmentsIndependent(t *testing.T) {
	got := BuildContentWithAttachments("对比这两份", []Attachment{
		{
			Filename: "旧版.pptx", FileType: "pptx", MediaType: "text",
			ExtractedText: "旧内容", StagedPath: "aa11-旧版.pptx", PageCount: 5,
		},
		{
			Filename: "新版.pptx", FileType: "pptx", MediaType: "text",
			ExtractedText: "新内容", FileKey: "uploads/t/x.pptx", // 没物化
		},
	})

	mustContain(t, got, "旧版.pptx", "第一个附件要在")
	mustContain(t, got, "新版.pptx", "第二个附件要在")
	mustContain(t, got, "/mnt/uploads/aa11-旧版.pptx", "可读那份要有路径")
	mustContain(t, got, "path=attachment:0", "未物化附件也要提供 ID 入口")
	if n := strings.Count(got, "[文件: "); n != 2 {
		t.Errorf("两个附件应各有一个 marker，得到 %d 个", n)
	}
	if !strings.HasSuffix(got, "对比这两份") {
		t.Error("用户消息应在末尾")
	}
}

// 注入段现在很短。这条钉住那个收益 —— 上一版一份 60KB 的教材会让这段文本超过 60KB，
// 而现在无论文件多大都是几百字节。
//
// 这不是"顺便变小了"：正文进消息意味着**每一轮请求都重发一遍**（引擎存的是拼过前缀
// 的那条消息），20 轮对话就是 20 份。
func TestInject_StaysSmallRegardlessOfFileSize(t *testing.T) {
	huge := strings.Repeat("第一章 学习动机理论：内在动机与外在动机的区分……\n", 1000) // ≈ 70KB
	got := BuildContentWithAttachments("总结这份教材", []Attachment{{
		Filename: "教育心理学.pdf", FileType: "pdf", MediaType: "text",
		PageCount: 210, ExtractedText: huge, StagedPath: "5e6f-教育心理学.pdf",
		FileSize: 8 * 1024 * 1024,
	}})

	if len(got) > 1024 {
		t.Errorf("注入段应该只有几百字节，得到 %d 字节：\n%s", len(got), got)
	}
	mustNotContain(t, got, "学习动机理论", "70KB 的正文不该出现在消息里")
}

// SandboxPath 的拼接必须跟 lea-tools 的 UPLOADS_MOUNT 一致 —— 对不上的后果是模型
// 拿着一个不存在的路径去 open()，而报错会说"文件不存在"，看起来像物化失败。
func TestSandboxPath(t *testing.T) {
	if got := (Attachment{StagedPath: "abc-x.pptx"}).SandboxPath(); got != "/mnt/uploads/abc-x.pptx" {
		t.Errorf("SandboxPath = %q", got)
	}
	if got := (Attachment{}).SandboxPath(); got != "" {
		t.Errorf("没物化时应返回空串，得到 %q", got)
	}
}

// deriveTitle 的 stripAttachmentPreamble 靠 marker 剥掉附件段。改了注入格式就必须
// 确认它还认得 —— 不然会话标题会变成一大段文件说明。
//
// ⚠️ 这一版**去掉了 `--- 内容结束 ---`**（没有正文块了），而 stripAttachmentPreamble
// 以前优先按那个标记切。所以现在所有附件都走它的"无正文"分支（按最后一个空行切），
// 这条测试因此比上一版更要紧。
func TestInject_TitleStrippingStillWorks(t *testing.T) {
	cases := []struct {
		name string
		atts []Attachment
		user string
	}{
		{
			name: "单个文档",
			atts: []Attachment{{
				Filename: "课件.pptx", FileType: "pptx", MediaType: "text",
				ExtractedText: "内容", StagedPath: "x-课件.pptx", PageCount: 3,
			}},
			user: "帮我改第 3 页",
		},
		{
			name: "图片",
			atts: []Attachment{{
				Filename: "题目.png", FileType: "png", MediaType: "image",
				StagedPath: "y-题目.png",
			}},
			user: "这道题怎么做",
		},
		{
			name: "多附件混合",
			atts: []Attachment{
				{Filename: "a.pptx", FileType: "pptx", MediaType: "text",
					ExtractedText: "aa", StagedPath: "p-a.pptx"},
				{Filename: "b.png", FileType: "png", MediaType: "image",
					StagedPath: "q-b.png"},
			},
			user: "对比这两个",
		},
		{
			name: "没物化（没有路径行）",
			atts: []Attachment{{
				Filename: "x.docx", FileType: "docx", MediaType: "text",
				ExtractedText: "内容", FileKey: "uploads/t/x.docx",
			}},
			user: "总结一下这份文件",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			msg := BuildContentWithAttachments(tc.user, tc.atts)
			title := deriveTitle(msg)
			for _, leak := range []string{"[文件:", "mnt/uploads", "用 Read 读", "如需修改", "未放入工作区"} {
				if strings.Contains(title, leak) {
					t.Errorf("标题泄漏了注入文本 %q：%q\n完整消息：\n%s", leak, title, msg)
				}
			}
			if !strings.Contains(title, tc.user) {
				t.Errorf("标题应含用户原话 %q，得到 %q", tc.user, title)
			}
		})
	}
}

// 用户消息里本身带空行时不能被切坏 —— stripAttachmentPreamble 的无正文分支按最后一个
// 空行切，所以必须先确认这是附件段才动手。
func TestStripPreamble_PlainMessageWithBlankLineUntouched(t *testing.T) {
	msg := "第一段\n\n第二段"
	if got := stripAttachmentPreamble(msg); got != msg {
		t.Errorf("无附件的消息不该被切，得到 %q", got)
	}
}

func mustContain(t *testing.T, got, want, why string) {
	t.Helper()
	if !strings.Contains(got, want) {
		t.Errorf("注入文本缺少 %q\n原因：%s\n实际内容：\n%s", want, why, got)
	}
}

func mustNotContain(t *testing.T, got, unwanted, why string) {
	t.Helper()
	if strings.Contains(got, unwanted) {
		t.Errorf("注入文本不该含 %q\n原因：%s\n实际内容：\n%s", unwanted, why, got)
	}
}
