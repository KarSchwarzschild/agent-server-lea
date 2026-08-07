package runtime

import (
	"context"
	"encoding/json"
	"fmt"
	"path"
	"strings"
	"sync"

	"gitlab.ouc-online.com.cn/aibase/agentloop/skill"
	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
)

// skillMountRoot 是 skill bundle 在沙箱里的挂载点，也是 Read 认的路径前缀
// （lea-tools 侧 file_store.py 的 _SKILL_MOUNT 把它映射到宿主的
// sessions/{sid}/skills）。两处必须一致。
const skillMountRoot = "/opt/skills"

// ToEngineSkills 把 DB 里的 skill 转成引擎的 skill.Skill。
//
// **Path 指向沙箱内的 bundle 目录，不是 lea-agent 本机路径。** 它只用来给模型显示
// "Skill directory: X"（引擎 skill/tool.go 的 locationHeader 取 filepath.Dir），模型
// 据此去读引用文件 —— 而读文件走我们的 Read 工具，落在共享存储上。填本机路径会让模型
// 去读一个它访问不到的目录。
//
// 只转有 bundle 的那些用 mount 路径；纯 prompt 型 skill（没打包 reference）Path 留空，
// locationHeader 会因此跳过整段 —— 那正确，它没有目录可言。
func ToEngineSkills(defs []SkillDef) []skill.Skill {
	out := make([]skill.Skill, 0, len(defs))
	for _, d := range defs {
		s := skill.Skill{
			Name:        d.Name,
			Description: d.Description,
			Body:        d.SystemPrompt,
			Source:      skill.SourceUser,
		}
		if d.BundleURI != "" {
			// path 而非 filepath：这是 POSIX 沙箱路径，在 Windows 上开发时
			// filepath.Join 会产生反斜杠。
			s.Path = path.Join(skillMountRoot, d.Name, "SKILL.md")
		}
		out = append(out, s)
	}
	return out
}

// GatewaySkillDiscloser 是引擎 Skill 工具的服务端版本。
//
// 它嵌 *skill.Tool 拿到全部默认行为（schema、set 管理、locationHeader、截断提示、
// bulk-reference 建议、ResultRetention），只在结果后追加一段"用哪个工具读引用文件"的
// 引导。
//
// 为什么必须追加：引擎的 locationHeader 说的是 "resolve them against it and read them
// directly"，没点名工具。而实测结论是**不点明模型会跳过依赖这些文件的步骤** ——
// pptx 的调研/配色/页型规划全被跳过，只剩 SKILL.md 正文里的步骤。这段结论原先记在
// activate_skill 里，随它一起搬过来。
//
// 也试过在这儿加"缺内容就该读"的通用判据，想让模型补上被跳过的 Step 7 QA（SKILL.md
// 那步正文只有一个裸链接 `See [QA Process](references/pitfalls.md)`）。实测无效且有害：
// pitfalls.md 照旧不读，反而多读了 13KB 用不上的 pptxgenjs.md。原因是模型对那一步的
// 误判不是"我判断不需要读"，而是"我以为我已经在做 QA 了"—— 判据管不到一个它并不认为
// 存在的缺口。结论：这类跳过的根因在 SKILL.md 把整步内容全外置，得在 skill 侧修，
// 工具提示词这一层碰不到。
type GatewaySkillDiscloser struct {
	*skill.Tool
	// canReadFiles 为 false 时不给读引用文件的引导 —— 那会让模型去调一个没注册的
	// 工具。见 gateway.go 的 Tools()：文件工具现在不挂 SandboxEnabled，所以这里通常
	// 是 true；留这个开关是为了 cfg 为 nil 那类边缘路径。
	canReadFiles bool

	// set 是自己留的一份副本。引擎的 *skill.Tool 把它的 set 存在私有字段里
	// （currentSet 不导出），而我们要读 Skill.Path 才知道该不该追加引导、追加什么路径。
	// SetSet 是 Discloser 契约的一部分，正好是截获点。
	mu  sync.RWMutex
	set *skill.Set
}

// NewGatewaySkillDiscloser 建一个服务端 discloser。set 由引擎在 Build 时通过 SetSet
// push 下来，这里给个空的占位。
func NewGatewaySkillDiscloser(canReadFiles bool) *GatewaySkillDiscloser {
	empty := skill.NewSet(nil)
	return &GatewaySkillDiscloser{
		Tool:         skill.NewTool(empty),
		canReadFiles: canReadFiles,
		set:          empty,
	}
}

// SetSet 转发给内嵌的默认实现，同时留一份副本供 Run 查 Path。
//
// 必须两边都设：默认实现靠它解析 skill body，我们靠它取 Path。只设一边的后果是
// 引导路径对不上模型实际拿到的 skill（或反过来，工具解析不出 skill）。
func (d *GatewaySkillDiscloser) SetSet(set *skill.Set) {
	d.Tool.SetSet(set)
	d.mu.Lock()
	d.set = set
	d.mu.Unlock()
}

func (d *GatewaySkillDiscloser) currentSet() *skill.Set {
	d.mu.RLock()
	defer d.mu.RUnlock()
	return d.set
}

// bulkSkillBodyBytes 是"专家自己别加载、派给子代理"的阈值。
//
// 阈值而不是一律派活：起一个子代理有固定成本（一次完整会话的 system prompt + 工具
// schema，加上它自己也要重读一遍任务），小 skill 派下去是净亏。实测
// internal-comms 的正文 1,543 字符（约 385 tok），专家自己读掉就完了；
// pptx-generator 8,131 字符、pdf 8,349 字符（各约 2k tok），而且它们的 reference
// 还要再读 30k+ 字符 —— 那一份进了专家上下文，之后二三十轮每轮重传。
//
// 4096 字节落在这两档中间，且离两边都远，不会因为某个 skill 改几行字就跨档。
const bulkSkillBodyBytes = 4096

// deferralNotice 是专家会话请求大 skill 时替代正文的回复。
//
// 为什么不直接把正文给它再劝它派活：正文一旦返回就进了上下文，劝不劝都已经付过钱了。
// 拦在返回之前是唯一能省下这笔的时机。
func deferralNotice(name string, bodyBytes int) string {
	return fmt.Sprintf(
		"技能 %q 的执行规范约 %d 字节，直接加载会占用你后续每一轮的上下文。\n\n"+
			"**请派给子代理执行**，它在独立上下文里加载规范并完成活，只把结果回给你：\n"+
			"`Task(subagent_type=\"%s\", prompt=\"用 %s 技能完成……（写清输入文件、产出文件名和格式要求）\")`\n\n"+
			"你手上的信息足够写清任务：要产出什么文件、什么格式、依据哪个输入。规范的细节由它去读。",
		name, bodyBytes, SkillRunnerName, name)
}

// Run 先走默认披露，再追加读引用文件的引导。
//
// 失败结果不追加：那时模型要做的是换一个 skill 名字，不是去读文件。
//
// **专家会话请求大 skill 时不给正文**，改回一句"派给子代理"。这是规则而不是提示词
// 引导 —— 实测提示词层面的判据对这类决策无效（见上面 Step 7 QA 那段）。
//
// 判定靠 tctx.ExpertSession()：引擎给专家会话的 tool.Context 打了标记，而
// childToolContext 不复制 Metadata，所以专家派下去的子代理**拿不到**这个标记，它调
// Skill 照常拿到完整正文。整套方案就落在这个不对称上。
func (d *GatewaySkillDiscloser) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var in struct {
		Name string `json:"name"`
	}
	nameOK := json.Unmarshal(input, &in) == nil && strings.TrimSpace(in.Name) != ""

	// 拦在 d.Tool.Run 之前 —— 它一返回正文就已经在 Result 里了。
	if nameOK && tctx.ExpertSession() {
		if sk, ok := d.currentSet().Get(strings.TrimSpace(in.Name)); ok && len(sk.Body) > bulkSkillBodyBytes {
			return tool.Result{Content: []tool.ResultContent{{
				Type: tool.ResultContentTypeText,
				Text: deferralNotice(sk.Name, len(sk.Body)),
			}}}, nil
		}
	}

	res, err := d.Tool.Run(ctx, input, tctx, out)
	if err != nil || res.IsError || !d.canReadFiles {
		return res, err
	}

	if !nameOK {
		return res, err
	}
	// 只在这个 skill 真有 bundle 目录时才提读文件 —— 没有 Path 的纯 prompt 型 skill
	// 没有引用文件可读，那段引导会让模型去读不存在的路径。
	sk, ok := d.currentSet().Get(in.Name)
	if !ok || strings.TrimSpace(sk.Path) == "" {
		return res, err
	}

	root := path.Dir(sk.Path)
	guide := fmt.Sprintf("\n\n**上面规范里提到某个 reference 文件时，先用 Read 读它再继续**，例如：\n"+
		"`Read(path=\"%s/references/slide-types.md\")`\n"+
		"（换成当前步骤实际引用的文件名 —— 按规范里写的相对路径拼在 `%s/` 后面）",
		root, root)

	res.Content = append(res.Content, tool.ResultContent{
		Type: tool.ResultContentTypeText,
		Text: guide,
	})
	return res, err
}
