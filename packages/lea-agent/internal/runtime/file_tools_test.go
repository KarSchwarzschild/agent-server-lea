package runtime

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	engine "gitlab.ouc-online.com.cn/aibase/agentloop"
	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
)

// 工具名必须与引擎内置工具完全一致 —— 子代理白名单
// （subagent/default.go: readOnlyTools/inspectTools）按名字精确匹配，
// launcher.go:302 `def.AllowsTool(t.Name())`。名字不对的后果是工具进了快照但被
// 白名单过滤掉，而主会话的 ToolList() 看起来完全正常。
func TestFileToolNamesMatchEngineBuiltins(t *testing.T) {
	// 引擎 tools/registry.go 里的内置名，抄在这里作为约定的显式记录
	engineNames := map[string]bool{
		"Read": true, "Write": true, "Edit": true, "Glob": true, "Bash": true,
	}
	for _, tl := range BuildFileTools("t", "u", "s", true) {
		if !engineNames[tl.Name()] {
			t.Errorf("工具名 %q 不在引擎内置名单里 —— 带白名单的子代理会拿不到它", tl.Name())
		}
	}

	// 只读子代理（readOnlyTools = Read/Glob/Grep）必须能拿到 Read 和 Glob。
	// 这是内置 researcher 那类代理能读文件的前提。
	readOnly := map[string]bool{"Read": true, "Glob": true, "Grep": true}
	granted := 0
	for _, tl := range BuildFileTools("t", "u", "s", true) {
		if readOnly[tl.Name()] {
			granted++
		}
	}
	if granted != 2 {
		t.Errorf("只读白名单应放行 Read + Glob 两个，实际 %d 个", granted)
	}
}

// 并发声明是这套设计的核心契约：引擎按它决定哪些调用能并行。声明错了不会编译失败、
// 不会测试失败，只在并发时偶发丢数据 —— 所以用测试钉住。
func TestFileToolConcurrencyDeclarations(t *testing.T) {
	want := map[string]bool{
		"Read":  true,  // 只读
		"Glob":  true,  // 只遍历目录
		"Write": false, // 整文件覆盖
		"Edit":  false, // 读-改-写序列
		"Bash":  false, // 命令任意，可能改文件系统
	}
	tools := BuildFileTools("tenant-a", "user-a", "sess-a", true)
	if len(tools) != len(want) {
		t.Fatalf("工具数量 = %d, 期望 %d", len(tools), len(want))
	}
	for _, tl := range tools {
		expect, known := want[tl.Name()]
		if !known {
			t.Errorf("多出未预期的工具 %q", tl.Name())
			continue
		}
		if got := tl.IsConcurrencySafe(); got != expect {
			t.Errorf("%s.IsConcurrencySafe() = %v, 期望 %v", tl.Name(), got, expect)
		}
		delete(want, tl.Name())
	}
	for name := range want {
		t.Errorf("缺少工具 %q", name)
	}
}

// 文件工具必须落在引擎给子代理的工具快照内。
//
// 这条是整个方案能省掉一处上游改动的根据：引擎在 build.go 里先 Provision（并入
// sessionTools）、再快照 eligibleSubagentTools、最后才 append ExtraTools。所以
// Provision 来的工具子代理继承，ExtraTools 来的拿不到。
//
// 这里断言的是 GatewayToolset 确实把工具交给了 Provision 通道 —— 而不是我对
// build.go 顺序的阅读理解。顺序本身由引擎自己的测试保证。
func TestGatewayToolsetProvisionsFileTools(t *testing.T) {
	rt := NewGatewayRuntime(&AgentConfig{SandboxEnabled: true}, "tenant-a", "user-a", "sess-a")
	ts, err := rt.Provision(context.Background(), engine.ToolsetSpec{CWD: "/tmp"})
	if err != nil {
		t.Fatalf("Provision: %v", err)
	}
	names := map[string]bool{}
	for _, tl := range ts.Tools() {
		names[tl.Name()] = true
	}
	for _, want := range []string{"Read", "Write", "Edit", "Glob", "Bash"} {
		if !names[want] {
			t.Errorf("Provision 的工具集里缺 %q —— 缺了子代理就拿不到它", want)
		}
	}
}

// 没开沙箱时只少 Bash，前四个照给。
//
// 这条以前断言的是"一个都不给"，理由写着"workspace 与沙箱是同一份资源"—— 那个理由是
// 错的：workspace 是 NFS host-mount，lea-tools 直接读写，沙箱只是另一个挂载它的地方。
// 按沙箱开关一起关掉的后果是一个纯文本产出的 agent（读 reference、写 markdown 报告，
// 全程不跑代码）连 Read 都没有，而 activate_skill 的引导又让它去 Read —— 模型于是调
// 一个没注册的工具。
func TestGatewayToolsetWithoutSandboxKeepsFileToolsButDropsBash(t *testing.T) {
	rt := NewGatewayRuntime(&AgentConfig{SandboxEnabled: false}, "tenant-a", "user-a", "sess-a")
	ts, _ := rt.Provision(context.Background(), engine.ToolsetSpec{})

	names := map[string]bool{}
	for _, tl := range ts.Tools() {
		names[tl.Name()] = true
	}
	for _, want := range []string{"Read", "Write", "Edit", "Glob"} {
		if !names[want] {
			t.Errorf("未开沙箱也该有 %q —— 它不进沙箱", want)
		}
	}
	if names["Bash"] {
		t.Error("未开沙箱不该有 Bash —— 只有它进沙箱")
	}

	// 开了沙箱则五个齐全。按名字断言而不数总数 —— Provision 还带平台工具
	// （code_interpreter 等），数量会随配置变。
	rtOn := NewGatewayRuntime(&AgentConfig{SandboxEnabled: true}, "tenant-a", "user-a", "sess-a")
	tsOn, _ := rtOn.Provision(context.Background(), engine.ToolsetSpec{})
	onNames := map[string]bool{}
	for _, tl := range tsOn.Tools() {
		onNames[tl.Name()] = true
	}
	for _, want := range []string{"Read", "Write", "Edit", "Glob", "Bash"} {
		if !onNames[want] {
			t.Errorf("开沙箱应有 %q", want)
		}
	}

	// nil cfg 也不能 panic —— debug 路径可能传进来
	rt2 := NewGatewayRuntime(nil, "t", "u", "s")
	ts2, _ := rt2.Provision(context.Background(), engine.ToolsetSpec{})
	if got := ts2.Tools(); len(got) != 0 {
		t.Errorf("cfg 为 nil 时应无文件工具，实际有 %d 个", len(got))
	}
}

// 平台工具必须落在 Provision 通道里，否则子代理拿不到。
//
// 这条是"专家把 skill 执行派给子代理"能不能成立的前提：派下去的子代理必须能调
// code_interpreter，否则派了活却没有干活的手段。这些工具此前全在 ExtraTools 里
// （快照之后 append），子代理和专家一个都拿不到 —— 那时这套委派只是看起来可行。
//
// memory_add 例外：写用户长期记忆只该主会话做，它留在 ExtraTools（BuildMainSessionTools）。
func TestProvisionCarriesPlatformToolsExceptMemoryAdd(t *testing.T) {
	cfg := &AgentConfig{
		SandboxEnabled: true,
		KBMode:         "on",
		KBIDs:          []int{1},
		MemoryMode:     "on",
	}
	rt := NewGatewayRuntime(cfg, "tenant-a", "user-a", "sess-a")
	ts, err := rt.Provision(context.Background(), engine.ToolsetSpec{})
	if err != nil {
		t.Fatalf("Provision: %v", err)
	}
	names := map[string]bool{}
	for _, tl := range ts.Tools() {
		names[tl.Name()] = true
	}
	// get_plan 在这份里：转交链上执行第 3 步的专家，得看得到整体计划才不会跑偏。
	//
	// WebSearch / WebFetch 也在：它们是引擎自带的，但我们此前一个都没注册 ——
	// build.go 一看到 opts.ToolRuntime 非 nil 就整个替换掉本地 runtime，于是
	// tools.Builtins() 那批全没进来。规划态下它们是放行的（network 不算 mutating），
	// 所以对一个只配了业务 API 的 agent，这是规划阶段唯一的外部信息来源。
	for _, want := range []string{
		"code_interpreter", "kb_search", "memory_search", "get_plan",
		"WebSearch", "WebFetch",
	} {
		if !names[want] {
			t.Errorf("Provision 的工具集里缺 %q —— 子代理会拿不到它", want)
		}
	}
	if names["memory_add"] {
		t.Error("memory_add 不该在 Provision 里 —— 子代理是临时的，不该写用户长期记忆")
	}
	// 计划的两个写工具反过来：子代理拿到就能改用户批准过的计划、或者替一个没人
	// 验收的产出标"完成"。
	for _, forbidden := range []string{"save_plan", "update_plan_step"} {
		if names[forbidden] {
			t.Errorf("%q 不该在 Provision 里 —— 子代理不该改用户批准过的计划", forbidden)
		}
	}

	// 而 memory_add 必须在主会话那份里，否则谁都写不了记忆。
	//
	// 计划的两个**写**工具（save_plan / update_plan_step）也在这里，且**始终注册** ——
	// 计划模式是所有 agent 都支持的能力，没有按 agent 的开关。只读的 get_plan 在
	// Provision 那份里，专家要看得到整体计划才不会跑偏 —— 上面的 want 列表钉着它。
	mainOnly := BuildMainSessionTools(cfg, "tenant-a", "user-a", "sess-a")
	if len(mainOnly) != 3 {
		t.Fatalf("主会话专属工具应有 3 个 (save_plan + update_plan_step + memory_add)，实际 %d: %v",
			len(mainOnly), toolNamesOf(mainOnly))
	}
	names = map[string]bool{}
	for _, tl := range mainOnly {
		names[tl.Name()] = true
	}
	if !names["save_plan"] {
		t.Error("主会话专属工具应包含 save_plan")
	}
	if !names["update_plan_step"] {
		t.Error("主会话专属工具应包含 update_plan_step")
	}
	if !names["memory_add"] {
		t.Error("主会话专属工具应包含 memory_add")
	}

	// memory 关掉时只剩计划那两个 —— 它们的注册不跟着 memory 配置走，
	// 计划持久化跟记忆是两回事。
	got := toolNamesOf(BuildMainSessionTools(&AgentConfig{MemoryMode: "off"}, "t", "u", "s"))
	if !reflect.DeepEqual(got, []string{"save_plan", "update_plan_step"}) {
		t.Errorf("memory_mode=off 时应只剩 save_plan + update_plan_step，实际 %v", got)
	}
}

func toolNamesOf(ts []tool.Tool) []string {
	out := make([]string, 0, len(ts))
	for _, t := range ts {
		out = append(out, t.Name())
	}
	return out
}

// bash 输出必须截断。lea-tools 侧 stdout 是无界累加的，一条 find / 就能产出几十 MB，
// 原样进上下文会挤掉真正有用的历史。
func TestTruncateOutput(t *testing.T) {
	short := strings.Repeat("a", 100)
	if got, cut := truncateOutput(short); got != short || cut != 0 {
		t.Errorf("短输出不应截断：cut=%d", cut)
	}

	long := strings.Repeat("b", maxBashOutputBytes+5000)
	got, cut := truncateOutput(long)
	if cut != len(long) {
		t.Errorf("截断应报告原始长度 %d，实际 %d", len(long), cut)
	}
	if len(got) > maxBashOutputBytes {
		t.Errorf("截断后 %d 字节，超过上限 %d", len(got), maxBashOutputBytes)
	}
}

// 按字节截断会切断 UTF-8 多字节字符，末尾变乱码 —— 中文输出必然踩到。
func TestTruncateOutputKeepsValidUTF8(t *testing.T) {
	// 每个汉字 3 字节，凑到刚好跨过上限，保证切点落在字符中间
	long := strings.Repeat("测", maxBashOutputBytes)
	got, cut := truncateOutput(long)
	if cut == 0 {
		t.Fatal("应当发生截断")
	}
	for i, r := range got {
		if r == '�' {
			t.Fatalf("截断产生了非法 UTF-8（偏移 %d）", i)
		}
	}
	if !strings.HasSuffix(got, "测") {
		t.Error("截断后应停在完整字符边界上")
	}
}

// ResultRetention 是**可选接口**（tool/retention.go:72）：类型断言失败就静默返回零值，
// history.Reduce 跳过该工具。所以漏实现、改错方法名、或不小心用值接收器导致断言失败，
// 都不会有任何编译或运行错误 —— 只表现为"上下文莫名其妙涨得快"。用测试钉住。
func TestAllFileToolsDeclareRetention(t *testing.T) {
	for _, tl := range BuildFileTools("t", "u", "s", true) {
		if _, ok := tl.(tool.ResultRetentionProvider); !ok {
			t.Errorf("%s 没有实现 ResultRetentionProvider —— 它的历史永远不会被压缩", tl.Name())
		}
	}
}

// 各工具的 retention 策略必须对应其语义，选错字段会造成两类相反的错误：
// 该压的不压（上下文白涨）、不该压的压了（模型拿到一个无法重建的空壳）。
func TestRetentionPolicies(t *testing.T) {
	byName := toolsByName(BuildFileTools("t", "u", "s", true))

	// Read / Glob：重跑能拿回原样结果，可压缩、可按 Identity 折叠
	for _, c := range []struct{ name, input string }{
		{"Read", `{"path":"a/b.md"}`},
		{"Glob", `{"pattern":"**/*.csv"}`},
	} {
		r := tool.RetentionOf(byName[c.name], json.RawMessage(c.input))
		if !r.Reproducible {
			t.Errorf("%s 应标记 Reproducible —— 否则变旧后不会被压缩", c.name)
		}
		if r.Recovery == "" {
			t.Errorf("%s 缺 Recovery —— 模型不知道怎么重新取回被省略的内容", c.name)
		}
		if r.Identity == "" {
			t.Errorf("%s 缺 Identity —— 同一路径的多次读不会折叠", c.name)
		}
	}

	// Identity 必须区分不同的观察对象，否则会把两个不同文件的读折叠成一个
	rA := tool.RetentionOf(byName["Read"], json.RawMessage(`{"path":"a.md"}`))
	rB := tool.RetentionOf(byName["Read"], json.RawMessage(`{"path":"b.md"}`))
	if rA.Identity == rB.Identity {
		t.Error("不同路径的 Read 拿到了相同 Identity —— 会把两个文件的内容折叠成一个")
	}

	// Bash：命令输出不可重建（重跑结果可能不同，还可能有副作用），
	// 必须 KeepTail（错误信息在末尾）且绝不可标 Reproducible
	rBash := tool.RetentionOf(byName["Bash"], json.RawMessage(`{"command":"ls"}`))
	if !rBash.KeepTail {
		t.Error("Bash 应 KeepTail —— 报错和退出码在输出末尾，截头会丢掉唯一有用的部分")
	}
	if rBash.Reproducible {
		t.Error("Bash 绝不能标 Reproducible —— 命令无法靠重跑拿回输出，且可能有副作用")
	}

	// Write / Edit：正文落盘后可从文件重建，用 InputStub 换掉，但必须保留 path
	for _, c := range []struct{ name, input string }{
		{"Write", `{"path":"out/r.md","content":"` + strings.Repeat("x", 5000) + `"}`},
		{"Edit", `{"path":"out/r.md","old_string":"` + strings.Repeat("y", 3000) + `","new_string":"z"}`},
	} {
		r := tool.RetentionOf(byName[c.name], json.RawMessage(c.input))
		if len(r.InputStub) == 0 {
			t.Errorf("%s 缺 InputStub —— 大段正文会永久留在历史里", c.name)
			continue
		}
		if len(r.InputStub) >= len(c.input) {
			t.Errorf("%s 的 stub（%d 字节）没比原始参数（%d 字节）小",
				c.name, len(r.InputStub), len(c.input))
		}
		var stub map[string]string
		if err := json.Unmarshal(r.InputStub, &stub); err != nil {
			t.Errorf("%s 的 stub 不是合法 JSON：%v", c.name, err)
			continue
		}
		// path 是这次调用的身份，换掉历史就读不懂了
		if stub["path"] != "out/r.md" {
			t.Errorf("%s 的 stub 丢了 path（得到 %q）", c.name, stub["path"])
		}
		// 要告诉模型怎么找回来
		if !strings.Contains(string(r.InputStub), "Read(") {
			t.Errorf("%s 的 stub 没告诉模型可以用 Read 取回内容", c.name)
		}
	}

	// 参数畸形时返回零值而不是 panic —— 失败的调用也会走到这里
	for _, name := range []string{"Read", "Write", "Edit", "Glob"} {
		if r := tool.RetentionOf(byName[name], json.RawMessage(`{`)); r.Reproducible || len(r.InputStub) > 0 {
			t.Errorf("%s 对畸形输入应返回零值 Retention", name)
		}
	}
}

// glob 匹配的核心约定：* 不跨 /，** 跨层。
//
// 用 path.Match 直接匹整条路径会让 "output/*.xlsx" 命中
// "output/sub/deep.xlsx" —— 模型据此以为文件就在 output/ 下，实际在子目录里。
func TestGlobMatch(t *testing.T) {
	cases := []struct {
		pattern, name string
		want          bool
	}{
		{"**/*", "a.txt", true},
		{"**/*", "output/deep/r.csv", true},
		{"*", "a.txt", true},
		{"*", "output/a.txt", false}, // * 不跨 /
		{"output/*.xlsx", "output/r.xlsx", true},
		{"output/*.xlsx", "output/sub/r.xlsx", false}, // 关键：不能跨层命中
		{"output/**/*.xlsx", "output/sub/r.xlsx", true},
		{"output/**", "output/sub/r.xlsx", true},
		{"**/*.csv", "a/b/c/d.csv", true},
		{"**/*.csv", "a/b/c/d.txt", false},
		{"report.md", "report.md", true},
		{"report.md", "sub/report.md", false},
		{"?.txt", "a.txt", true},
		{"?.txt", "ab.txt", false},
	}
	for _, c := range cases {
		if got := globMatch(c.pattern, c.name); got != c.want {
			t.Errorf("globMatch(%q, %q) = %v, 期望 %v", c.pattern, c.name, got, c.want)
		}
	}
}

func TestFirstLine(t *testing.T) {
	if got := firstLine("ls -la\nrm -rf /"); got != "ls -la" {
		t.Errorf("firstLine 应只取第一行，得到 %q", got)
	}
	long := strings.Repeat("x", 200)
	if got := firstLine(long); len([]rune(got)) > 61 {
		t.Errorf("长命令应截断，得到 %d 个字符", len([]rune(got)))
	}
}

// ── 新鲜度校验的端到端行为（用假 lea-tools） ──────────────────

// 盲写必须被拒：没读过就覆盖已存在的文件，说明模型不知道自己要删掉什么。
// 关键在于 write_file 不能自己捏一个 expect 出来 —— 那等于把校验绕过去。
func TestWriteWithoutPriorReadSendsNoExpect(t *testing.T) {
	var gotBody map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		_, _ = w.Write([]byte(`{"bytes_written":3,"stat":{"size":3,"mtime_ns":111}}`))
	}))
	defer srv.Close()
	restore := swapGatewayURL(srv.URL)
	defer restore()

	tools := toolsByName(BuildFileTools("t1", "u1", "s1", true))
	res, err := tools["Write"].Run(context.Background(),
		json.RawMessage(`{"path":"a.txt","content":"abc"}`),
		&tool.Context{ToolUseID: "tu-1"}, nil)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.IsError {
		t.Fatalf("写入不该报错：%+v", res.Content)
	}
	if _, present := gotBody["expect_mtime_ns"]; present {
		t.Error("没读过就写时不能带 expect —— 服务端要靠它的缺失判定为盲写")
	}
}

// 读过之后写，必须带上读到的版本，服务端才知道这次覆盖有据可依。
func TestReadThenWriteCarriesExpect(t *testing.T) {
	var writeBody map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/read"):
			_, _ = w.Write([]byte(`{"content":"old","stat":{"size":3,"mtime_ns":424242}}`))
		case strings.HasSuffix(r.URL.Path, "/write"):
			_ = json.NewDecoder(r.Body).Decode(&writeBody)
			_, _ = w.Write([]byte(`{"bytes_written":3,"stat":{"size":3,"mtime_ns":999}}`))
		}
	}))
	defer srv.Close()
	restore := swapGatewayURL(srv.URL)
	defer restore()

	// 同一批工具共享 readSet —— read_file 记下的版本 write_file 要能看到。
	tools := toolsByName(BuildFileTools("t1", "u1", "s1", true))
	tctx := &tool.Context{ToolUseID: "tu-1"}
	if _, err := tools["Read"].Run(context.Background(),
		json.RawMessage(`{"path":"a.txt"}`), tctx, nil); err != nil {
		t.Fatalf("read: %v", err)
	}
	if _, err := tools["Write"].Run(context.Background(),
		json.RawMessage(`{"path":"a.txt","content":"new"}`), tctx, nil); err != nil {
		t.Fatalf("write: %v", err)
	}
	if got := writeBody["expect_mtime_ns"]; got != float64(424242) {
		t.Errorf("expect_mtime_ns = %v，期望读到的 424242", got)
	}
}

// 冲突（409）后必须丢掉本地记录，否则模型原样重试会再撞一次同样的错。
func TestConflictClearsReadSet(t *testing.T) {
	writes := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/read"):
			_, _ = w.Write([]byte(`{"content":"old","stat":{"size":3,"mtime_ns":1}}`))
		case strings.HasSuffix(r.URL.Path, "/write"):
			writes++
			var body map[string]any
			_ = json.NewDecoder(r.Body).Decode(&body)
			if writes == 1 {
				w.WriteHeader(409)
				_, _ = w.Write([]byte(`{"detail":"a.txt 在你读取之后被改动过"}`))
				return
			}
			// 第二次：本地记录应已清空，所以不该再带旧版本上来
			if _, present := body["expect_mtime_ns"]; present {
				t.Error("冲突后仍带着旧 expect 重试 —— 会再撞同一个错")
			}
			_, _ = w.Write([]byte(`{"bytes_written":3,"stat":{"size":3,"mtime_ns":2}}`))
		}
	}))
	defer srv.Close()
	restore := swapGatewayURL(srv.URL)
	defer restore()

	tools := toolsByName(BuildFileTools("t1", "u1", "s1", true))
	tctx := &tool.Context{ToolUseID: "tu-1"}
	_, _ = tools["Read"].Run(context.Background(), json.RawMessage(`{"path":"a.txt"}`), tctx, nil)

	res, err := tools["Write"].Run(context.Background(),
		json.RawMessage(`{"path":"a.txt","content":"x"}`), tctx, nil)
	if err != nil {
		t.Fatalf("第一次写: %v", err)
	}
	if !res.IsError {
		t.Fatal("409 应表现为工具错误，让模型看到并处理")
	}
	// 服务端写好的中文指引要原样传给模型，而不是替换成技术错误
	if txt := resultText(res); !strings.Contains(txt, "被改动过") {
		t.Errorf("应把服务端的说明交给模型，得到 %q", txt)
	}
	_, _ = tools["Write"].Run(context.Background(),
		json.RawMessage(`{"path":"a.txt","content":"x"}`), tctx, nil)
}

// Bash 打的 URL 必须是 /sandbox/sessions/{sid}/exec。
//
// 真机验证时这里写错过（少了 /sessions），表现是 404 {"detail":"Not Found"} ——
// 跟 session 不存在时的 404 {"detail":"sandbox session not found"} 几乎一样，
// 我因此误判成沙箱生命周期问题排查了好一阵。用测试钉住路径，别再靠肉眼。
func TestBashUsesCorrectSandboxRoute(t *testing.T) {
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		_, _ = w.Write([]byte(`{"stdout":"ok","exit_code":0,"duration_ms":5}`))
	}))
	defer srv.Close()
	restore := swapGatewayURL(srv.URL)
	defer restore()

	tools := toolsByName(BuildFileTools("t1", "u1", "sess-abc", true))
	if _, err := tools["Bash"].Run(context.Background(),
		json.RawMessage(`{"command":"ls"}`), &tool.Context{ToolUseID: "tu-1"}, nil); err != nil {
		t.Fatalf("Run: %v", err)
	}
	// 对齐 lea-tools 的 api/sandbox.py:10 APIRouter(prefix="/sandbox/sessions")
	if want := "/sandbox/sessions/sess-abc/exec"; gotPath != want {
		t.Errorf("请求路径 = %q，期望 %q", gotPath, want)
	}
}

// 退出码非 0 要标成工具错误，否则模型会把失败的命令当成功继续往下做。
func TestBashNonZeroExitIsToolError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"stdout":"","stderr":"no such file","exit_code":2,"duration_ms":12}`))
	}))
	defer srv.Close()
	restore := swapGatewayURL(srv.URL)
	defer restore()

	tools := toolsByName(BuildFileTools("t1", "u1", "s1", true))
	res, err := tools["Bash"].Run(context.Background(),
		json.RawMessage(`{"command":"cat missing.txt"}`),
		&tool.Context{ToolUseID: "tu-1"}, nil)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if !res.IsError {
		t.Error("退出码非 0 应标记为工具错误")
	}
	if txt := resultText(res); !strings.Contains(txt, "no such file") {
		t.Errorf("stderr 应传给模型，得到 %q", txt)
	}
}

// ── helpers ─────────────────────────────────────────────────

func swapGatewayURL(u string) func() {
	old := toolsGatewayURL
	toolsGatewayURL = u
	return func() { toolsGatewayURL = old }
}

func toolsByName(ts []tool.Tool) map[string]tool.Tool {
	m := make(map[string]tool.Tool, len(ts))
	for _, t := range ts {
		m[t.Name()] = t
	}
	return m
}

func resultText(r tool.Result) string {
	var sb strings.Builder
	for _, c := range r.Content {
		sb.WriteString(c.Text)
	}
	return sb.String()
}
