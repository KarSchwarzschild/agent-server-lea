package persistence

import (
	"fmt"
	"strings"
	"testing"
)

// TestScenarioDump 打印各场景下模型**实际收到**的文本。
//
// 跑 `go test ./internal/persistence/ -run TestScenarioDump -v` 看效果。
//
// 这不是断言测试（那些在 attachment_inject_test.go），是给人看的 —— 注入文本是模型
// 认识这个文件的唯一入口，而它的质量只能靠读。改了措辞就跑一遍这个，确认三件事：
//
//  1. 该说的都说了（有什么文件 / 在哪 / 怎么取 / 产物放哪）
//  2. 没有自相矛盾（比如又说只读又让它原地改）
//  3. 降级状态是明确的，不是沉默的
//
// ## 这一版看的重点变了
//
// 正文不再注入（内容由 Read 按需取），所以每段都该只有几行。**如果 dump 出来某一段
// 很长，那就是回归了** —— 那意味着有人把内容又拼回了消息里。
//
// 每个场景的"预期路径"写的是模型**应该走的动作序列**，读 dump 时对照着看：注入文本
// 有没有给它走这条路所需的全部信息。
func TestScenarioDump(t *testing.T) {
	scenarios := []struct {
		name   string
		desc   string
		expect string // 期望模型走哪条路
		user   string
		atts   []Attachment
	}{
		{
			name:   "1_文档出报告",
			desc:   "上传调研 PDF，让它写报告",
			expect: "Read 取正文 → 直接写 → 产物 Write 到 /workspace",
			user:   "根据这份调研写一篇总结报告",
			atts: []Attachment{{
				Filename: "行业调研.pdf", FileType: "pdf", MediaType: "text",
				PageCount: 32, SHA256: "a3f19c7e", StagedPath: "a3f19c7e-行业调研.pdf",
				FileKey: "uploads/t1/a3f19c7e.pdf", FileSize: 1_800_000,
				ExtractedText: "第一章 市场概况\n2026 年在线教育规模达……",
			}},
		},
		{
			name:   "2_改PPT某页",
			desc:   "上传课件，改第 3 页",
			expect: "进沙箱读 /mnt/uploads 改写，产物写 /workspace，不重新生成",
			user:   "帮我把第 3 页的标题改成「卷积神经网络入门」",
			atts: []Attachment{{
				Filename: "深度学习课件.pptx", FileType: "pptx", MediaType: "text",
				PageCount: 12, SHA256: "b7c21d34", StagedPath: "b7c21d34-深度学习课件.pptx",
				FileKey: "uploads/t1/b7c21d34.pptx", FileSize: 4_200_000,
				ExtractedText: "封面：深度学习导论\n\n第一节 神经网络基础\n\n卷积层与池化",
			}},
		},
		{
			name:   "3a_图片问内容",
			desc:   "上传题目照片问怎么做",
			expect: "Read → 得到「这是图片，没有文本层」→ 沙箱里做视觉处理",
			user:   "这道题怎么做？",
			atts: []Attachment{{
				Filename: "数学题.jpg", FileType: "jpg", MediaType: "image",
				SHA256: "c8d3e5f1", StagedPath: "c8d3e5f1-数学题.jpg",
				FileKey: "uploads/t1/c8d3e5f1.jpg", FileSize: 320_000,
			}},
		},
		{
			name:   "3b_视频问内容",
			desc:   "上传讲课视频问讲了什么（字幕服务已配）",
			expect: "Read → 拿到字幕文本 → 直接回答",
			user:   "这个视频讲了什么？",
			atts: []Attachment{{
				Filename: "第五讲.mp4", FileType: "mp4", MediaType: "video",
				SHA256: "d1e2f3a4", StagedPath: "d1e2f3a4-第五讲.mp4",
				FileKey: "uploads/t1/d1e2f3a4.mp4", FileSize: 88_000_000,
				ExtractedText: "大家好 今天我们讲反向传播 首先回顾一下链式法则……",
			}},
		},
		{
			name:   "4_表格要计算",
			desc:   "上传成绩表要求统计",
			expect: "进沙箱读原文件算 —— 注入里没有数字可供它偷懒",
			user:   "算一下每个班的平均分和及格率",
			atts: []Attachment{{
				Filename: "期末成绩.xlsx", FileType: "xlsx", MediaType: "text",
				SHA256: "f9a0b1c2", StagedPath: "f9a0b1c2-期末成绩.xlsx",
				FileKey: "uploads/t1/f9a0b1c2.xlsx", FileSize: 45_000,
				ExtractedText: "班级 | 姓名 | 分数\n1班 | 张三 | 91\n1班 | 李四 | 58",
			}},
		},
		{
			name:   "5_扫描件",
			desc:   "扫描版 PDF（MinerU 未配，降级到只读文本层）",
			expect: "Read → 得到「没提取到文本，可能是扫描件」→ 沙箱里识别",
			user:   "帮我把这份材料整理成文字",
			atts: []Attachment{{
				Filename: "扫描合同.pdf", FileType: "pdf", MediaType: "text",
				PageCount: 20, SHA256: "0a1b2c3d", StagedPath: "0a1b2c3d-扫描合同.pdf",
				FileKey: "uploads/t1/0a1b2c3d.pdf", FileSize: 12_000_000,
			}},
		},
		{
			name:   "6_沙箱未开",
			desc:   "同样的 PPT，但没有共享存储/沙箱未启用",
			expect: "明说读不了也改不了 —— 而不是试着读然后失败",
			user:   "帮我改第 3 页",
			atts: []Attachment{{
				Filename: "课件.pptx", FileType: "pptx", MediaType: "text",
				PageCount: 12, SHA256: "b7c21d34", FileSize: 4_200_000,
				FileKey:       "uploads/t1/b7c21d34.pptx",
				ExtractedText: "封面：深度学习导论\n\n第一节 神经网络基础",
			}},
		},
		{
			name:   "7_未知格式",
			desc:   "上传 .zip（放宽 415 之后能传了）",
			expect: "沙箱里解压处理 —— 老版本这里直接 415 拒绝上传",
			user:   "看看这个压缩包里有什么",
			atts: []Attachment{{
				Filename: "作业包.zip", FileType: "zip", MediaType: "file",
				SHA256: "4d5e6f70", StagedPath: "4d5e6f70-作业包.zip",
				FileKey: "uploads/t1/4d5e6f70.zip", FileSize: 2_400_000,
			}},
		},
		{
			name:   "8_文档加图片",
			desc:   "同时传文档和图片（混合，最容易出 bug 的形状）",
			expect: "分别 Read；会话标题仍取用户原话",
			user:   "对照这张图检查文档里的数据",
			atts: []Attachment{
				{
					Filename: "报表.docx", FileType: "docx", MediaType: "text",
					SHA256: "11aa22bb", StagedPath: "11aa22bb-报表.docx",
					FileKey: "uploads/t1/11aa22bb.docx", FileSize: 68_000,
					ExtractedText: "Q3 营收 1200 万",
				},
				{
					Filename: "图表.png", FileType: "png", MediaType: "image",
					SHA256: "33cc44dd", StagedPath: "33cc44dd-图表.png",
					FileKey: "uploads/t1/33cc44dd.png", FileSize: 210_000,
				},
			},
		},
		{
			name: "9_超长文档",
			desc: "上传 200 页教材要求总结（上一版这里会注入 60KB 正文）",
			expect: "Read → 工具侧截断并说明去哪读剩下的。" +
				"注意这一段 dump 出来应该跟场景 1 一样短",
			user: "帮我总结这份教材的核心内容",
			atts: []Attachment{{
				Filename: "教育心理学.pdf", FileType: "pdf", MediaType: "text",
				PageCount: 210, SHA256: "5e6f7081", StagedPath: "5e6f7081-教育心理学.pdf",
				FileKey: "uploads/t1/5e6f7081.pdf", FileSize: 24_000_000,
				// 约 60KB —— 接近 MySQL TEXT 上限，也是真实教材的量级
				ExtractedText: strings.Repeat(
					"第一章 学习动机理论：内在动机与外在动机的区分及其教学意义……\n", 800),
			}},
		},
	}

	for _, s := range scenarios {
		t.Run(s.name, func(t *testing.T) {
			msg := BuildContentWithAttachments(s.user, s.atts)
			title := deriveTitle(msg)

			fmt.Printf("\n%s\n场景：%s\n预期路径：%s\n%s\n",
				strings.Repeat("═", 78), s.desc, s.expect, strings.Repeat("─", 78))
			fmt.Println(msg)
			fmt.Printf("%s\n会话标题 → %q（注入段 %d 字节）\n",
				strings.Repeat("─", 78), title, len(msg)-len(s.user))

			// 每个场景都要保证的三条底线：
			// 1. 标题不能被附件说明污染（用户在侧栏看到的是这个）
			for _, leak := range []string{"[文件:", "mnt/uploads", "用 Read 读", "如需修改"} {
				if strings.Contains(title, leak) {
					t.Errorf("会话标题泄漏了注入文本 %q：%q", leak, title)
				}
			}
			// 2. 用户原话必须完整保留在末尾（模型要靠它知道要干什么）
			if !strings.HasSuffix(msg, s.user) {
				t.Errorf("用户原话不在末尾")
			}
			// 3. 注入段必须短。这条钉住这一版的核心收益 —— 长了就说明正文又被拼回来了，
			//    而那意味着每一轮请求都重发一遍那几十 KB。
			//    按附件数算：每个附件一段固定模板，多附件线性增长是正常的。
			if n, limit := len(msg)-len(s.user), 400*len(s.atts); n > limit {
				t.Errorf("注入段 %d 字节（%d 个附件，上限 %d）—— 正文是不是又被拼进消息了？",
					n, len(s.atts), limit)
			}
		})
	}
}
