// 服务端版的引擎内置文件工具：Read / Write / Edit / Glob / Bash。
//
// **名字必须与引擎内置工具完全一致**（tools/registry.go 里的 Read/Write/Edit/Glob/
// Grep/Bash），这不是风格问题：
//
//	internal/subagent/default.go:22  readOnlyTools = []string{"Read", "Glob", "Grep"}
//	internal/subagent/default.go:26  inspectTools  = []string{"Read", "Glob", "Grep", "Bash"}
//	internal/subagent/launcher.go:302  if def.AllowsTool(t.Name())
//
// 子代理的工具白名单按名字**精确匹配**。用 read_file 这类自造名字的后果是：工具确实
// 进了 eligibleSubagentTools 快照，但任何带白名单的子代理（内置的五个，以及以后我们
// 在 DB 里配的专家）都匹配不到 —— 名单里写着 Read，工具集里只有 read_file。
// 这类问题在主会话的 ToolList() 里看不出来，因为主会话不过这层过滤。
//
// 我们完全替代了引擎默认的本地 runtime（它装配的是操作 lea-agent 本机磁盘的工具，
// 而工作空间在 NFS/沙箱上），所以这里不是"新增工具"而是"填上那个空位"。
//
// 为什么要有这一套，而不是让模型全靠 code_interpreter 读写文件：
//
//  1. **产物归属**。文件经代码产生时是副作用，只能靠扫 /workspace 差集反推谁写了什么。
//     走工具则每次写都是一次带 toolUseID / ParentToolUseID 的显式调用，谁写的直接就有。
//  2. **子代理能拿到工具**。它们注册在 GatewayToolset.Tools() 里，落在引擎
//     build.go:222 eligibleSubagentTools 快照**之前**，所以子代理天然继承。
//     平台工具走 Options.ExtraTools，那是快照之后 append 的，子代理拿不到。
//  3. **并发约束下沉到工具侧**。每个工具声明 IsConcurrencySafe()，引擎按此串行化
//     会话内的变更类调用；跨子代理的互斥由 lea-tools 侧的路径锁保证。
//
// 前四个工具**不进沙箱**：/workspace 是 NFS host-mount（宿主
// {NFS_ROOT}/tenants/{tid}/sessions/{sid}/workspace），lea-tools 进程直接读写，
// 毫秒级，且不受沙箱冷启动（实测 >10s）影响。只有 bash 必须进沙箱。
//
// 新鲜度校验（防覆盖别人的改动）：读文件时记下 (mtime_ns, size)，写回时带上，
// 服务端比对。引擎自己有同样的机制（tool.Context.ReadSet + RequireOverwritable），
// 但那道闸门在 PermissionMode=flight 下被 TrustedWrites() 跳过了，而我们必须用
// flight（服务端没有终端可交互）。所以这里自己维护 —— 反正文件在 NFS 上、不在
// lea-agent 本地盘，引擎的 ReadSet 记录的路径对我们也没意义。
package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"path"
	"strings"
	"sync"
	"time"

	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
)

// 文件工具的 HTTP 客户端。不共用 config_loader 那个 10s client：读写走 NFS 通常是
// 毫秒级，但 sandbox 回落模式要经 envd 往返，且 bash 自带最长 120s 的执行超时。
var fileToolClient = &http.Client{Timeout: 150 * time.Second}

// bash 输出上限。lea-tools 侧 run_code 有超时但 stdout 是无界累加的
// （_cube_sandbox.py 里 `stdout += ...`），而 shell 的惯用法本身就是往 stdout 倒
// 东西（find / cat 大文件 / ls -R）—— 不截断就会把几十 MB 灌进模型上下文。
const maxBashOutputBytes = 30 * 1024

// 单次读取返回给模型的上限。超过就截断并说明，避免一个文件吃掉整个上下文窗口。
const maxReadBytes = 60 * 1024

// fileToolBase 是五个工具共用的会话身份。
type fileToolBase struct {
	tenantID  string
	userID    string
	sessionID string

	// readSet 记录本会话读过的文件版本，写回时用于新鲜度校验。
	// 一个会话内多个工具共享同一份（指针传递），因为 Read 读的和
	// Write 要写的是同一个文件 —— 分开存就等于没有校验。
	readSet *readSet
}

// readSet 是"本会话读过哪些文件的哪一版"。
//
// 并发安全是必需的而不是保险：引擎的执行器会并行跑 IsConcurrencySafe()==true 的
// 工具，Read 正是其中之一，所以多个 read 会同时写这张表。
type readSet struct {
	mu sync.Mutex
	m  map[string]fileStat
}

type fileStat struct {
	Size    int64 `json:"size"`
	MtimeNs int64 `json:"mtime_ns"`
}

func newReadSet() *readSet { return &readSet{m: map[string]fileStat{}} }

func (r *readSet) put(path string, st fileStat) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.m[path] = st
}

func (r *readSet) get(path string) (fileStat, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	st, ok := r.m[path]
	return st, ok
}

// BuildFileTools 返回服务端文件工具集。共享一个 readSet，所以
// "Read 之后 Write"能通过新鲜度校验，而"没读就写"会被拒。
//
// sandbox 决定要不要给 Bash —— 只有它进沙箱（走 /sandbox/sessions/{sid}/exec）。
// 前四个走 /api/files/*，直读共享存储，所以一个不跑代码的 agent 也该有它们：
// 读 reference、写 markdown 报告全程不需要沙箱。详见 gateway.go 的 Tools()。
func BuildFileTools(tenantID, userID, sessionID string, sandbox bool) []tool.Tool {
	base := fileToolBase{
		tenantID:  tenantID,
		userID:    userID,
		sessionID: sessionID,
		readSet:   newReadSet(),
	}
	tools := []tool.Tool{
		&ReadFileTool{base},
		&WriteFileTool{base},
		&EditFileTool{base},
		&GlobTool{base},
	}
	if sandbox {
		tools = append(tools, &BashTool{fileToolBase: base})
	}
	return tools
}

// callFiles 打 lea-tools 的 /api/files/* 端点。
//
// 4xx 一律作为**工具错误**返回（IsError + 原文），不作为 Go error：这些是模型能
// 自己纠正的情况（重读后重试、加上下文让匹配唯一、换个路径），把服务端写好的中文
// 指引原样交给它比抛一个技术错误有用得多。Go error 只留给"引擎该重试或放弃"的
// 传输层故障。
func (b fileToolBase) callFiles(ctx context.Context, method, path string, query url.Values, body any) (json.RawMessage, string, error) {
	target := toolsGatewayURL + path
	if query != nil {
		target += "?" + query.Encode()
	}
	var reader *bytes.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return nil, "", err
		}
		reader = bytes.NewReader(raw)
	} else {
		reader = bytes.NewReader(nil)
	}
	req, err := http.NewRequestWithContext(ctx, method, target, reader)
	if err != nil {
		return nil, "", err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Tenant-ID", b.tenantID)

	resp, err := fileToolClient.Do(req)
	if err != nil {
		return nil, "", fmt.Errorf("lea-tools unreachable: %w", err)
	}
	defer resp.Body.Close()

	var buf bytes.Buffer
	if _, err := buf.ReadFrom(resp.Body); err != nil {
		return nil, "", err
	}
	if resp.StatusCode >= 400 {
		var e struct {
			Detail string `json:"detail"`
		}
		_ = json.Unmarshal(buf.Bytes(), &e)
		detail := e.Detail
		if detail == "" {
			detail = strings.TrimSpace(buf.String())
		}
		if detail == "" {
			detail = fmt.Sprintf("HTTP %d", resp.StatusCode)
		}
		return nil, detail, nil
	}
	return buf.Bytes(), "", nil
}

// errResult 把服务端的说明原样交给模型。
func errResult(text string) tool.Result {
	return tool.Result{IsError: true, Content: []tool.ResultContent{
		{Type: tool.ResultContentTypeText, Text: text},
	}}
}

func textResult(text string) tool.Result {
	return tool.Result{Content: []tool.ResultContent{
		{Type: tool.ResultContentTypeText, Text: text},
	}}
}

// ── Read ───────────────────────────────────────────────

type ReadFileTool struct{ fileToolBase }

func (t *ReadFileTool) Name() string { return "Read" }
func (t *ReadFileTool) Description() string {
	return "读取工作空间（/workspace）里的一个文本文件。修改文件前必须先读 —— " +
		"未读过的文件不允许覆盖，这可以防止抹掉你没看过的内容。"
}

// IsConcurrencySafe 为 true：只读，多个并行读互不影响。
func (t *ReadFileTool) IsConcurrencySafe() bool { return true }

// ResultRetention 让引擎能在这条结果变旧后把它压缩掉。
//
// 这是个**可选接口**（tool/retention.go:72）—— 不实现的话 history.Reduce 直接跳过，
// 于是专家读过的每个文件正文都永久留在它的上下文里。缺了它，"委派给子代理省上下文"
// 的收益会被子代理自己的历史膨胀吃掉一部分。
//
// Reproducible=true 的依据：同样的 path 再读一次就能拿回全文。所以旧结果可以只留
// 一句"用 Read 重新取"。Identity 让同一路径的多次读折叠成最后一次 —— 前面几次描述
// 的是同一个文件的同一份内容，重复发给模型没有意义。
func (t *ReadFileTool) ResultRetention(raw json.RawMessage) tool.Retention {
	var in struct {
		Path string `json:"path"`
	}
	if err := json.Unmarshal(raw, &in); err != nil || in.Path == "" {
		return tool.Retention{}
	}
	return tool.Retention{
		Reproducible: true,
		Recovery:     fmt.Sprintf("Read(path=%q)", in.Path),
		Identity:     fmt.Sprintf("Read:%s", in.Path),
	}
}

func (t *ReadFileTool) InputSchema() tool.Schema {
	return tool.Schema{
		Type: tool.SchemaTypeObject,
		Properties: map[string]tool.Schema{
			"path": {Type: tool.SchemaTypeString, Description: "工作空间内的相对路径，如 output/report.md"},
		},
		Required: []string{"path"},
	}
}

func (t *ReadFileTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var p struct {
		Path string `json:"path"`
	}
	if err := json.Unmarshal(input, &p); err != nil {
		return tool.Result{}, fmt.Errorf("parse Read input: %w", err)
	}
	if p.Path == "" {
		return errResult("path 不能为空"), nil
	}

	q := url.Values{
		"session_id": {t.sessionID},
		"tenant_id":  {t.tenantID},
		"path":       {p.Path},
	}
	raw, detail, err := t.callFiles(ctx, http.MethodGet, "/api/files/read", q, nil)
	if err != nil {
		return errResult(fmt.Sprintf("读取 %s 失败：%v", p.Path, err)), nil
	}
	if detail != "" {
		return errResult(detail), nil
	}

	var res struct {
		Content string    `json:"content"`
		Stat    *fileStat `json:"stat"`
	}
	if err := json.Unmarshal(raw, &res); err != nil {
		return errResult(fmt.Sprintf("解析读取结果失败：%v", err)), nil
	}
	// 记住这一版 —— Write / Edit 靠它证明"我读过当前内容"。
	if res.Stat != nil {
		t.readSet.put(p.Path, *res.Stat)
	}

	content := res.Content
	truncated := ""
	if len(content) > maxReadBytes {
		content = content[:maxReadBytes]
		truncated = fmt.Sprintf("\n\n[内容已截断：仅显示前 %d 字节，共 %d 字节。"+
			"需要完整处理请用 code_interpreter 分块读]", maxReadBytes, len(res.Content))
	}

	emitToolResult(out, t.Name(), tctx.ToolUseID,
		fmt.Sprintf("读取 %s（%d 字节）", p.Path, len(res.Content)), nil)
	return textResult(content + truncated), nil
}

// ── Write ──────────────────────────────────────────────

type WriteFileTool struct{ fileToolBase }

func (t *WriteFileTool) Name() string { return "Write" }
func (t *WriteFileTool) Description() string {
	return "把完整内容写入工作空间的一个文件（新建或整体覆盖）。" +
		"覆盖已存在的文件前必须先用 Read 读过它，否则会被拒绝。" +
		"只改动其中一小段时用 Edit 更省。"
}

// IsConcurrencySafe 为 false：整文件覆盖，两个并行写同一路径结果不可预期。
// 引擎据此把会话内的写调用串行化。
func (t *WriteFileTool) IsConcurrencySafe() bool { return false }

// ResultRetention 用 InputStub 把已落盘的正文从历史里换掉。
//
// 这是五个工具里收益最大的一处：写一个 50KB 的报告，那 50KB 会作为**调用参数**留在
// 历史里，之后每一轮请求都原样发给模型。而文件已经在盘上了 —— 想看内容 Read 一下就有。
//
// 只换 content、保留 path：path 是这次调用的身份，换掉它历史就读不懂了。这也是
// 为什么 stub 由工具自己构造而不是引擎代劳 —— 只有工具知道自己哪个字段是身份、
// 哪个是载荷（tool/retention.go:55-57）。
//
// 引擎只在调用**成功后**才应用 stub，失败的调用参数原样保留 —— 那时盘上没有东西可以
// 用来重建它。
func (t *WriteFileTool) ResultRetention(raw json.RawMessage) tool.Retention {
	var in struct {
		Path    *string `json:"path"`
		Content *string `json:"content"`
	}
	if err := json.Unmarshal(raw, &in); err != nil || in.Path == nil || in.Content == nil {
		return tool.Retention{}
	}
	stub, err := json.Marshal(map[string]string{
		"path": *in.Path,
		"content": fmt.Sprintf(
			"<%d 字节已从此记录中省略以节省上下文；文件已写入 —— Read(path=%q) 可查看当前内容>",
			len(*in.Content), *in.Path),
	})
	if err != nil {
		return tool.Retention{}
	}
	return tool.Retention{InputStub: stub}
}

func (t *WriteFileTool) InputSchema() tool.Schema {
	return tool.Schema{
		Type: tool.SchemaTypeObject,
		Properties: map[string]tool.Schema{
			"path":    {Type: tool.SchemaTypeString, Description: "工作空间内的相对路径。父目录会自动创建"},
			"content": {Type: tool.SchemaTypeString, Description: "文件的完整内容"},
		},
		Required: []string{"path", "content"},
	}
}

func (t *WriteFileTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var p struct {
		Path    string `json:"path"`
		Content string `json:"content"`
	}
	if err := json.Unmarshal(input, &p); err != nil {
		return tool.Result{}, fmt.Errorf("parse Write input: %w", err)
	}
	if p.Path == "" {
		return errResult("path 不能为空"), nil
	}

	body := map[string]any{
		"session_id": t.sessionID,
		"tenant_id":  t.tenantID,
		"path":       p.Path,
		"content":    p.Content,
	}
	// 读过就带上版本；没读过就不带 —— 服务端据此区分"有据可依的覆盖"和"盲写"。
	// 目标不存在时（新建）服务端不会因缺少版本而拒绝。
	if st, ok := t.readSet.get(p.Path); ok {
		body["expect_mtime_ns"] = st.MtimeNs
		body["expect_size"] = st.Size
	}

	raw, detail, err := t.callFiles(ctx, http.MethodPost, "/api/files/write", nil, body)
	if err != nil {
		return errResult(fmt.Sprintf("写入 %s 失败：%v", p.Path, err)), nil
	}
	if detail != "" {
		// 冲突时清掉本地记录，逼模型重新 read 再来 —— 留着旧版本它可能原样重试，
		// 然后再撞一次同样的错。
		t.readSet.mu.Lock()
		delete(t.readSet.m, p.Path)
		t.readSet.mu.Unlock()
		return errResult(detail), nil
	}

	var res struct {
		BytesWritten int       `json:"bytes_written"`
		Stat         *fileStat `json:"stat"`
	}
	_ = json.Unmarshal(raw, &res)
	// 写完即持有权威内容，记下新版本，这样紧接着再改一次不会被判过期。
	if res.Stat != nil {
		t.readSet.put(p.Path, *res.Stat)
	}

	summary := fmt.Sprintf("写入 %s（%d 字节）", p.Path, res.BytesWritten)
	emitToolResult(out, t.Name(), tctx.ToolUseID, summary, map[string]any{
		"path": p.Path, "bytes": res.BytesWritten,
	})
	return textResult(summary), nil
}

// ── Edit ───────────────────────────────────────────────

type EditFileTool struct{ fileToolBase }

func (t *EditFileTool) Name() string { return "Edit" }
func (t *EditFileTool) Description() string {
	return "把文件里的一段文本替换成另一段。old_string 必须在文件中恰好出现一次 —— " +
		"出现 0 次或多次都会报错而不会随便挑一处改，所以请带足上下文使其唯一。"
}

// IsConcurrencySafe 为 false：读-改-写序列，并行执行会丢改动。
func (t *EditFileTool) IsConcurrencySafe() bool { return false }

// ResultRetention 同 Write：改动落盘后，old_string 和 new_string 都能从文件重建。
// 保留 path 和两段的字节数，"我把这个文件里 N 字节换成了 M 字节"这个记录还在，
// 只有字面文本消失。
func (t *EditFileTool) ResultRetention(raw json.RawMessage) tool.Retention {
	var in struct {
		Path      *string `json:"path"`
		OldString *string `json:"old_string"`
		NewString *string `json:"new_string"`
	}
	if err := json.Unmarshal(raw, &in); err != nil ||
		in.Path == nil || in.OldString == nil || in.NewString == nil {
		return tool.Retention{}
	}
	stub, err := json.Marshal(map[string]string{
		"path":       *in.Path,
		"old_string": fmt.Sprintf("<%d 字节已省略以节省上下文>", len(*in.OldString)),
		"new_string": fmt.Sprintf("<%d 字节已省略以节省上下文；改动已生效 —— Read(path=%q) 可查看结果>",
			len(*in.NewString), *in.Path),
	})
	if err != nil {
		return tool.Retention{}
	}
	return tool.Retention{InputStub: stub}
}

func (t *EditFileTool) InputSchema() tool.Schema {
	return tool.Schema{
		Type: tool.SchemaTypeObject,
		Properties: map[string]tool.Schema{
			"path":       {Type: tool.SchemaTypeString, Description: "工作空间内的相对路径"},
			"old_string": {Type: tool.SchemaTypeString, Description: "要被替换的原文，须唯一匹配（含空白与缩进）"},
			"new_string": {Type: tool.SchemaTypeString, Description: "替换后的文本。传空串表示删除这段"},
		},
		Required: []string{"path", "old_string", "new_string"},
	}
}

func (t *EditFileTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var p struct {
		Path      string `json:"path"`
		OldString string `json:"old_string"`
		NewString string `json:"new_string"`
	}
	if err := json.Unmarshal(input, &p); err != nil {
		return tool.Result{}, fmt.Errorf("parse Edit input: %w", err)
	}
	if p.Path == "" || p.OldString == "" {
		return errResult("path 和 old_string 都不能为空；新建文件请用 Write"), nil
	}

	body := map[string]any{
		"session_id": t.sessionID,
		"tenant_id":  t.tenantID,
		"path":       p.Path,
		"old_string": p.OldString,
		"new_string": p.NewString,
	}
	if st, ok := t.readSet.get(p.Path); ok {
		body["expect_mtime_ns"] = st.MtimeNs
		body["expect_size"] = st.Size
	}

	raw, detail, err := t.callFiles(ctx, http.MethodPost, "/api/files/edit", nil, body)
	if err != nil {
		return errResult(fmt.Sprintf("修改 %s 失败：%v", p.Path, err)), nil
	}
	if detail != "" {
		t.readSet.mu.Lock()
		delete(t.readSet.m, p.Path)
		t.readSet.mu.Unlock()
		return errResult(detail), nil
	}

	var res struct {
		BytesDelta int       `json:"bytes_delta"`
		Stat       *fileStat `json:"stat"`
	}
	_ = json.Unmarshal(raw, &res)
	if res.Stat != nil {
		t.readSet.put(p.Path, *res.Stat)
	}

	summary := fmt.Sprintf("修改 %s（%+d 字节）", p.Path, res.BytesDelta)
	emitToolResult(out, t.Name(), tctx.ToolUseID, summary, map[string]any{
		"path": p.Path, "bytes_delta": res.BytesDelta,
	})
	return textResult(summary), nil
}

// ── Glob ────────────────────────────────────────────────────

type GlobTool struct{ fileToolBase }

// Name 是 "Glob" 而不是 list_files：引擎内置的 Glob 就是"列目录/找文件"那一个
// （见其 Description：列目录用 "**/*"），而 readOnlyTools 白名单里写的正是它。
// 叫别的名字，只读子代理就列不了文件。
func (t *GlobTool) Name() string { return "Glob" }
func (t *GlobTool) Description() string {
	return "按 glob 模式查找工作空间里的文件。列出全部文件用一次递归调用（pattern=\"**/*\"），" +
		"不要再额外发一次浅层 \"*\"。返回路径和大小。"
}

// IsConcurrencySafe 为 true：只遍历目录，不改任何东西。
func (t *GlobTool) IsConcurrencySafe() bool { return true }

// ResultRetention 同 Read：重跑同一个 pattern 就能拿回列表，所以可压缩、可折叠。
// Identity 带上 pattern 和 show_hidden —— 换了任一个就是另一次观察，不该折叠。
func (t *GlobTool) ResultRetention(raw json.RawMessage) tool.Retention {
	var in struct {
		Pattern    string `json:"pattern"`
		ShowHidden bool   `json:"show_hidden"`
	}
	if err := json.Unmarshal(raw, &in); err != nil {
		return tool.Retention{}
	}
	if strings.TrimSpace(in.Pattern) == "" {
		in.Pattern = "**/*"
	}
	return tool.Retention{
		Reproducible: true,
		Recovery:     fmt.Sprintf("Glob(pattern=%q)", in.Pattern),
		Identity:     fmt.Sprintf("Glob:%s@%t", in.Pattern, in.ShowHidden),
	}
}

func (t *GlobTool) InputSchema() tool.Schema {
	return tool.Schema{
		Type: tool.SchemaTypeObject,
		Properties: map[string]tool.Schema{
			"pattern": {
				Type:        tool.SchemaTypeString,
				Description: "斜杠分隔的 glob 模式，如 \"**/*\"（全部）、\"output/*.xlsx\"。省略等于 \"**/*\"",
			},
			"show_hidden": {
				Type:        tool.SchemaTypeBoolean,
				Description: "是否包含缓存/隐藏文件（__pycache__、.log 等），默认不含",
				Default:     false,
			},
		},
	}
}

func (t *GlobTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var p struct {
		Pattern    string `json:"pattern"`
		ShowHidden bool   `json:"show_hidden"`
	}
	_ = json.Unmarshal(input, &p)
	if strings.TrimSpace(p.Pattern) == "" {
		p.Pattern = "**/*"
	}

	q := url.Values{
		"session_id":  {t.sessionID},
		"tenant_id":   {t.tenantID},
		"show_hidden": {fmt.Sprintf("%t", p.ShowHidden)},
	}
	raw, detail, err := t.callFiles(ctx, http.MethodGet, "/api/files/list", q, nil)
	if err != nil {
		return errResult(fmt.Sprintf("查找文件失败：%v", err)), nil
	}
	if detail != "" {
		return errResult(detail), nil
	}

	var res struct {
		Files []struct {
			Path  string `json:"path"`
			Size  int64  `json:"size"`
			Mtime int64  `json:"mtime"`
		} `json:"files"`
		Truncated bool `json:"truncated"`
	}
	if err := json.Unmarshal(raw, &res); err != nil {
		return errResult(fmt.Sprintf("解析目录结果失败：%v", err)), nil
	}

	// 服务端的 /list 返回整棵树，pattern 在这里过滤。放在客户端是因为 /list 已经
	// 有截断上限，而 glob 的语义（尤其 ** 跨层匹配）跟服务端那套隐藏文件规则正交。
	var sb strings.Builder
	matched := 0
	for _, f := range res.Files {
		if !globMatch(p.Pattern, f.Path) {
			continue
		}
		matched++
		fmt.Fprintf(&sb, "%s\t%d bytes\n", f.Path, f.Size)
	}
	if matched == 0 {
		emitToolResult(out, t.Name(), tctx.ToolUseID,
			fmt.Sprintf("无匹配（pattern=%s，共 %d 个文件）", p.Pattern, len(res.Files)), nil)
		if len(res.Files) == 0 {
			return textResult("工作空间当前没有文件。"), nil
		}
		return textResult(fmt.Sprintf("没有匹配 %q 的文件（工作空间共 %d 个文件，"+
			"用 pattern=\"**/*\" 可列出全部）。", p.Pattern, len(res.Files))), nil
	}
	if res.Truncated {
		sb.WriteString("[列表已截断：文件数超过上限]\n")
	}
	emitToolResult(out, t.Name(), tctx.ToolUseID,
		fmt.Sprintf("匹配 %d 个文件（pattern=%s）", matched, p.Pattern), nil)
	return textResult(sb.String()), nil
}

// globMatch 做斜杠分隔的 glob 匹配，支持 ** 跨层。
//
// 不用 path.Match：它的 * 会匹配 /，于是 "output/*.xlsx" 会命中
// "output/sub/deep.xlsx" —— 模型据此以为文件在 output/ 下，实际在子目录里。
func globMatch(pattern, name string) bool {
	return matchSegments(strings.Split(pattern, "/"), strings.Split(name, "/"))
}

func matchSegments(pat, seg []string) bool {
	for len(pat) > 0 {
		if pat[0] == "**" {
			// ** 可以吃掉零个或多个层级，逐一试探
			if len(pat) == 1 {
				return true
			}
			for i := 0; i <= len(seg); i++ {
				if matchSegments(pat[1:], seg[i:]) {
					return true
				}
			}
			return false
		}
		if len(seg) == 0 {
			return false
		}
		if ok, err := pathMatchOne(pat[0], seg[0]); err != nil || !ok {
			return false
		}
		pat, seg = pat[1:], seg[1:]
	}
	return len(seg) == 0
}

// pathMatchOne 在单个路径段内做通配匹配。用 path.Match 而不是 filepath.Match：
// 后者在 Windows 上把 \ 当分隔符，而我们的路径一律是斜杠分隔的 POSIX 形式。
// 段内已经没有 /，所以 path.Match 的 * 不跨段的问题在这一层不存在。
func pathMatchOne(pattern, name string) (bool, error) {
	return path.Match(pattern, name)
}

// ── bash ────────────────────────────────────────────────────

type BashTool struct {
	fileToolBase

	// 沙箱是否已确认存在。exec 端点（api/sandbox.py 的 exec_code）**只查不建** ——
	// 查不到直接 404 {"detail":"sandbox session not found"}，所以第一次执行前必须
	// 自己建一次。
	//
	// 之前没有这一步，Bash 能不能跑取决于 code_interpreter 有没有恰好先跑过一次
	// （只有它的 ensureSandbox 会建）。pptx-generator 这类 skill 正好反过来：先
	// 逐页写 slides/*.js，最后一步用 shell 跑 Node 合成 —— 于是 Bash 是第一个进
	// 沙箱的，必然 404。表现是"11 页都生成好了，最后一步失败"，看起来像 skill 的
	// 问题，实际是工具缺了初始化。
	sandboxMu    sync.Mutex
	sandboxReady bool
}

func (t *BashTool) Name() string { return "Bash" }
func (t *BashTool) Description() string {
	return "在沙箱里执行 shell 命令，工作目录是 /workspace。适合文件整理、格式转换、" +
		"查看环境等一行能说清的操作。要写多行程序、装依赖或做数据分析请用 code_interpreter。"
}

// IsConcurrencySafe 为 false：命令内容任意，可能改动文件系统。
func (t *BashTool) IsConcurrencySafe() bool { return false }

// ResultRetention 只设 KeepTail，跟引擎的 Bash 一致（tools/bash/bash.go:123）。
//
// **不能设 Reproducible**：命令输出无法靠重跑拿回来 —— 同一条命令两次的结果可能不同，
// 而且重跑可能有副作用（删文件、装依赖、调外部 API）。引擎在
// tool/retention.go:16-19 明确说了这一点，所以 bash 的输出只会被"超长时截断"，
// 永远不会因为变旧而被丢弃。
//
// KeepTail=true 是因为失败信息落在**末尾**：报错和退出码在最后，掐掉尾巴等于把唯一
// 有用的部分扔了。文件读取相反，要留开头。
func (t *BashTool) ResultRetention(json.RawMessage) tool.Retention {
	return tool.Retention{KeepTail: true}
}

func (t *BashTool) InputSchema() tool.Schema {
	return tool.Schema{
		Type: tool.SchemaTypeObject,
		Properties: map[string]tool.Schema{
			"command": {Type: tool.SchemaTypeString, Description: "要执行的 shell 命令"},
			"timeout": {Type: tool.SchemaTypeInteger, Description: "超时秒数，默认 60，最大 120", Default: 60},
		},
		Required: []string{"command"},
	}
}

// ensureSandbox 确保本 session 的沙箱存在。幂等：lea-tools 侧
// SandboxSessionManager.create 命中已有 session 时直接 touch 并返回，所以重复调用
// 只是一次快速往返，不会重建。
//
// 不传 skills：skill 目录由会话创建时的 ProvisionSessionSkills 落盘（走
// /api/files/skills/provision），host-mount 在沙箱创建那一刻把它带进去。这里再传一遍
// 是多余的下载。
//
// 用 sandboxCreateClient（180s）而不是 fileToolClient：这是唯一要等 Cube 冷启动的
// 调用，实测 >10s。
func (t *BashTool) ensureSandbox(ctx context.Context) error {
	t.sandboxMu.Lock()
	defer t.sandboxMu.Unlock()
	if t.sandboxReady {
		return nil
	}

	body, _ := json.Marshal(map[string]any{
		"session_id": t.sessionID,
		"tenant_id":  t.tenantID,
		"user_id":    t.userID,
	})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		toolsGatewayURL+"/sandbox/sessions", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := sandboxCreateClient.Do(req)
	if err != nil {
		return fmt.Errorf("沙箱服务不可达: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("创建沙箱返回 %d", resp.StatusCode)
	}
	t.sandboxReady = true
	return nil
}

func (t *BashTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var p struct {
		Command string `json:"command"`
		Timeout int    `json:"timeout"`
	}
	if err := json.Unmarshal(input, &p); err != nil {
		return tool.Result{}, fmt.Errorf("parse bash input: %w", err)
	}
	if strings.TrimSpace(p.Command) == "" {
		return errResult("command 不能为空"), nil
	}
	if p.Timeout <= 0 {
		p.Timeout = 60
	}
	if p.Timeout > 120 {
		p.Timeout = 120
	}

	if err := t.ensureSandbox(ctx); err != nil {
		return errResult(fmt.Sprintf("沙箱不可用：%v", err)), nil
	}

	body := map[string]any{
		"code":     p.Command,
		"language": "bash",
		"timeout":  p.Timeout,
	}
	// 路径前缀是 /sandbox/sessions，不是 /sandbox —— 见 api/sandbox.py:10
	// `APIRouter(prefix="/sandbox/sessions")`。写错的表现是 404 {"detail":"Not Found"}，
	// 跟 session 查不到的 404 {"detail":"sandbox session not found"} 长得很像，容易误判成
	// 沙箱生命周期问题（真机验证时我就先误判了一轮）。
	raw, detail, err := t.callFiles(ctx, http.MethodPost,
		"/sandbox/sessions/"+url.PathEscape(t.sessionID)+"/exec", nil, body)
	if err != nil {
		return errResult(fmt.Sprintf("执行命令失败：%v", err)), nil
	}
	if detail != "" {
		return errResult(detail), nil
	}

	var res struct {
		Stdout     string `json:"stdout"`
		Stderr     string `json:"stderr"`
		ExitCode   int    `json:"exit_code"`
		DurationMs int    `json:"duration_ms"`
	}
	if err := json.Unmarshal(raw, &res); err != nil {
		return errResult(fmt.Sprintf("解析执行结果失败：%v", err)), nil
	}

	// 截断必须在这里做：lea-tools 侧 stdout 是无界累加的，一条 `find /` 就能
	// 产出几十 MB，原样进上下文会挤掉真正有用的历史。
	stdout, outCut := truncateOutput(res.Stdout)
	stderr, errCut := truncateOutput(res.Stderr)

	var sb strings.Builder
	if stdout != "" {
		sb.WriteString(stdout)
		if outCut > 0 {
			fmt.Fprintf(&sb, "\n[stdout 已截断：共 %d 字节]", outCut)
		}
	}
	if stderr != "" {
		if sb.Len() > 0 {
			sb.WriteString("\n")
		}
		sb.WriteString("stderr: " + stderr)
		if errCut > 0 {
			fmt.Fprintf(&sb, "\n[stderr 已截断：共 %d 字节]", errCut)
		}
	}
	if res.ExitCode != 0 {
		fmt.Fprintf(&sb, "\n[退出码 %d]", res.ExitCode)
	}
	text := sb.String()
	if strings.TrimSpace(text) == "" {
		text = "命令执行完毕，没有输出。"
	}

	emitToolResult(out, t.Name(), tctx.ToolUseID,
		fmt.Sprintf("执行 %s（退出码 %d，%dms）", firstLine(p.Command), res.ExitCode, res.DurationMs),
		map[string]any{"exit_code": res.ExitCode, "duration_ms": res.DurationMs})

	// 退出码非 0 标成工具错误，模型才知道要处理而不是当成成功继续。
	if res.ExitCode != 0 {
		return errResult(text), nil
	}
	return textResult(text), nil
}

// truncateOutput 把输出截到上限，返回截断后的文本和原始长度（未截断时返回 0）。
func truncateOutput(s string) (string, int) {
	if len(s) <= maxBashOutputBytes {
		return s, 0
	}
	// 按字节截会切断 UTF-8 多字节字符，末尾出现乱码。回退到最近的字符边界。
	cut := maxBashOutputBytes
	for cut > 0 && !utf8Start(s[cut]) {
		cut--
	}
	return s[:cut], len(s)
}

// utf8Start 判断某字节是否是一个 UTF-8 字符的起始字节（非续接字节 10xxxxxx）。
func utf8Start(b byte) bool { return b&0xC0 != 0x80 }

// firstLine 取命令的第一行做事件摘要，长的截断 —— 时间线上一行显示得下。
func firstLine(s string) string {
	if i := strings.IndexAny(s, "\r\n"); i >= 0 {
		s = s[:i]
	}
	s = strings.TrimSpace(s)
	if len(s) > 60 {
		return s[:60] + "…"
	}
	return s
}
