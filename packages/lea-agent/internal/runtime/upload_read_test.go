package runtime

import (
	"strings"
	"testing"
	"unicode/utf8"

	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
)

// Read 读 /mnt/uploads 时不碰字节，查的是上传时就抽好的那份文本
// （chat_attachments.extracted_text）。这组测的是**拿到那一行之后说什么**。
//
// 为什么这一层值得测：/mnt/uploads 下躺的是原始文件（pptx 是 zip、pdf 是二进制），
// 按文本读会得到服务端 415，而模型收到"不是 UTF-8 文本文件"之后通常会去开沙箱重新
// 解析一遍 —— 慢，而且它未必装得上库。这里每个分支都在避免模型走上一条错路。
//
// 查库那一步不在这里测：persistence.DB() 是读环境变量的 sync.Once 单例，没有注入口
// （同 session/ownership_test.go 的处理）。

const uploadPath = "/mnt/uploads/aa11-讲义.pdf"

func TestRenderUpload_TextIsReturnedVerbatim(t *testing.T) {
	att := persistence.Attachment{
		Filename: "讲义.pdf", FileType: "pdf", MediaType: "text",
		ExtractedText: "第一章 绪论\n本章介绍……",
	}
	text, summary := renderUpload(att, uploadPath)

	if text != "第一章 绪论\n本章介绍……" {
		t.Errorf("装得下时应原样返回，得到 %q", text)
	}
	if strings.Contains(text, "截断") {
		t.Error("没截断不该有截断提示 —— 那会让模型无谓地去补读一次")
	}
	if !strings.Contains(summary, "讲义.pdf") {
		t.Errorf("摘要要带文件名（前端时间线显示这个），得到 %q", summary)
	}
}

// 截断了就**必须**说清楚，并给出取剩下部分的办法。
//
// 不说的话模型会以为自己看到了全文，然后在一份只有前 60KB 的文档上做总结 —— 而它会
// 像看过全文一样自信。这是这里最坏的失败形态：静默，且从输出看不出来。
func TestRenderUpload_TruncationIsAnnouncedWithRecovery(t *testing.T) {
	long := strings.Repeat("第一章 学习动机理论与教学意义……\n", 3000) // ≈ 90KB
	att := persistence.Attachment{
		Filename: "教材.pdf", FileType: "pdf", MediaType: "text",
		ExtractedText: long,
	}
	text, _ := renderUpload(att, uploadPath)

	if len(text) > maxReadBytes+500 {
		t.Errorf("截断后仍然太长：%d 字节", len(text))
	}
	for _, want := range []string{"已截断", "全文共", uploadPath, "需要通读"} {
		if !strings.Contains(text, want) {
			t.Errorf("截断说明缺少 %q：\n%s", want, text[len(text)-400:])
		}
	}
}

// 切碎 UTF-8 会产生非法字符串，之后 json 序列化和日志都会出奇怪的错，而错误信息
// 完全指不到"读取时截断切坏了"这个真因。
//
// maxReadBytes 是 61440，能被 3 整除 —— 所以纯中文正文的切点**正好落在字符边界上**。
// 这里刻意覆盖切点在字符内部（偏移 0/1/2）的三种情况：用一个 ASCII 前缀把整段推移
// 一格、两格。初版的边界回退写法在偏移 0 那一档反而切出了非法序列。
func TestRenderUpload_TruncationNeverBreaksMultibyteChar(t *testing.T) {
	for _, pad := range []string{"", "a", "ab"} {
		// 25000 个"中"= 75000 字节，加上 pad 稳超 maxReadBytes
		att := persistence.Attachment{
			Filename: "x.pdf", MediaType: "text",
			ExtractedText: pad + strings.Repeat("中", 25000),
		}
		text, _ := renderUpload(att, uploadPath)
		body := strings.SplitN(text, "\n\n[内容已截断", 2)[0]
		if len(body) == len(att.ExtractedText) {
			t.Fatalf("pad=%q 没有触发截断，这条用例白跑了", pad)
		}
		if !utf8.ValidString(body) {
			t.Errorf("pad=%q（切点偏移 %d）截出了非法 UTF-8", pad, len(pad))
		}
	}
}

// 没文本的三种情况必须分开说 —— 模型该走的路完全不同，笼统一句"读不到"会让它在
// 错误的方向上重试。
func TestRenderUpload_NoTextCasesAreDistinguished(t *testing.T) {
	cases := []struct {
		name      string
		att       persistence.Attachment
		wantWords []string
		notWords  []string
	}{
		{
			// 图片没有文本层是这类文件的**性质**，不是提取失败。
			// 老措辞"无法提取文本内容"不含任何可行动信息，模型只能回"我看不到图片"。
			name: "图片",
			att:  persistence.Attachment{Filename: "题目.jpg", MediaType: "image"},
			wantWords: []string{
				"是图片", "没有文本层", "视觉处理", uploadPath,
			},
			notWords: []string{"解析失败", "扫描件"},
		},
		{
			name: "视频",
			att:  persistence.Attachment{Filename: "第五讲.mp4", MediaType: "video"},
			wantWords: []string{
				"视频文件", "没有可用的转写文本", uploadPath,
			},
			notWords: []string{"图片", "扫描件"},
		},
		{
			name: "音频",
			att:  persistence.Attachment{Filename: "录音.mp3", MediaType: "audio"},
			wantWords: []string{
				"音频文件", "没有可用的转写文本",
			},
			notWords: []string{"视频", "图片"},
		},
		{
			// 扫描件要跟"文件是空的"区分开：后者模型会回"你的文件好像是空的"，
			// 而实际上可能有 20 页内容，只是没有文本层。
			name: "扫描件/解析失败",
			att:  persistence.Attachment{Filename: "合同.pdf", MediaType: "text"},
			wantWords: []string{
				"没有提取到文本", "扫描件", "解析失败", "图像",
			},
			notWords: []string{"是图片", "转写"},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			text, summary := renderUpload(tc.att, uploadPath)
			for _, w := range tc.wantWords {
				if !strings.Contains(text, w) {
					t.Errorf("缺少 %q：\n%s", w, text)
				}
			}
			for _, w := range tc.notWords {
				if strings.Contains(text, w) {
					t.Errorf("不该出现 %q（那是另一种情况的说法）：\n%s", w, text)
				}
			}
			if !strings.Contains(summary, tc.att.Filename) {
				t.Errorf("摘要要带文件名，得到 %q", summary)
			}
		})
	}
}

// 可配置项绝不能点名 —— 装没装取决于 sandbox template 版本，点名一个不在的东西会让
// 模型在同一个 import 上打转，而不是换条路。跟注入侧是同一条约束
// （persistence/attachment_inject_test.go 的 TestInject_NeverNamesConfigurableThings）。
func TestRenderUpload_NeverNamesConfigurableThings(t *testing.T) {
	atts := []persistence.Attachment{
		{Filename: "a.jpg", MediaType: "image"},
		{Filename: "b.mp4", MediaType: "video"},
		{Filename: "c.mp3", MediaType: "audio"},
		{Filename: "d.pdf", MediaType: "text"},
		{Filename: "e.pdf", MediaType: "text", ExtractedText: strings.Repeat("x", 70000)},
	}
	banned := []string{
		"python-pptx", "python-docx", "openpyxl", "pypdf", "pandas",
		"PIL", "Pillow", "pip install", "pytesseract", "ffmpeg", "OCR",
		"whisper", "Whisper", "skill", "Skill",
	}
	for _, att := range atts {
		text, _ := renderUpload(att, uploadPath)
		for _, w := range banned {
			if strings.Contains(text, w) {
				t.Errorf("%s 的回复点名了可配置项 %q：\n%s", att.Filename, w, text)
			}
		}
	}
}

// 路径必须原样带回去。模型要靠它进沙箱做后续处理，拼错了它会去 open 一个不存在的
// 文件，而报错会说"文件不存在"，看起来像物化失败。
func TestRenderUpload_EchoesPathForSandboxFollowup(t *testing.T) {
	const p = "/mnt/uploads/ff99-期末成绩表.xlsx"
	for _, att := range []persistence.Attachment{
		{Filename: "成绩.xlsx", MediaType: "image"},
		{Filename: "成绩.xlsx", MediaType: "video"},
		{Filename: "成绩.xlsx", MediaType: "text"},
		{Filename: "成绩.xlsx", MediaType: "text", ExtractedText: strings.Repeat("x", 70000)},
	} {
		text, _ := renderUpload(att, p)
		if !strings.Contains(text, p) {
			t.Errorf("mediaType=%s 的回复没带路径：\n%s", att.MediaType, text)
		}
	}
}

// uploadsPrefix 必须跟 persistence.UploadsMount 和 lea-tools 的 UPLOADS_MOUNT 一致。
// 对不上的后果是 Read 认不出这是上传件，落到普通文件读，然后 415。
func TestUploadsPrefixMatchesPersistence(t *testing.T) {
	want := persistence.UploadsMount + "/"
	if uploadsPrefix != want {
		t.Errorf("uploadsPrefix = %q, persistence.UploadsMount 推出来是 %q", uploadsPrefix, want)
	}
	// 一个真实的 SandboxPath 必须能被这个前缀认出来
	p := persistence.Attachment{StagedPath: "aa-x.pdf"}.SandboxPath()
	if !strings.HasPrefix(p, uploadsPrefix) {
		t.Errorf("SandboxPath %q 不以 uploadsPrefix %q 开头", p, uploadsPrefix)
	}
}
