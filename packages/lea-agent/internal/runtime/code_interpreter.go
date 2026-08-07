package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"time"

	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
)

// sandboxCreateClient 专给建沙箱用 —— 它是这条链路上唯一会等 Cube 冷启动的调用。
//
// 原先复用 httpClient（10s，那是给 LoadAgentConfig 配的读配置超时）。Cube 冷启动实测
// >10s，于是**请求其实成功了、沙箱也建起来了，只是客户端先放弃**，模型看到
// "sandbox service unreachable: context deadline exceeded"。这个错误信息指向"服务不可
// 达"，而真相是"服务很慢且我等得不够久" —— 排查时会去查网络和防火墙，方向全错。
//
// 更糟的是它偶发：同一 session 第二次调用命中已建好的沙箱，0.7s 返回。所以症状是
// "第一次失败、重试就好"，看起来像网络抖动。
//
// Destroy 那处仍用 httpClient：DELETE 不等冷启动，10s 够。
var sandboxCreateClient = &http.Client{Timeout: 180 * time.Second}

type CodeInterpreterTool struct {
	tenantID  string
	userID    string
	sessionID string
	skillID   string
	// agent 绑定的 skill —— 建沙箱时传给 lea-tools，它按这个列表把 bundle
	// 投递到 session 目录并 RO 挂成 /opt/skills。没列进去的沙箱里不存在。
	skills []SkillDef

	mu            sync.Mutex
	sandboxReady  bool
	sandboxSessID string
}

func (t *CodeInterpreterTool) Name() string { return "code_interpreter" }

// Description 是 LLM 唯一能看到的工作区说明。
//
// 定位：只讲**环境事实**和**环境特有的约束**，不讲产物该放哪。
// 产物落点归 skill 管（skill 规定了就按 skill 的来），没 skill 约定时归模型
// 自行决定 —— 这是产品设计，不是工具层该插手的事。
//
// 落点这条曾来回改过三版，教训值得留着：
//  1. 只有"在安全沙箱中执行代码"一句 → 模型不知道 output/ 存在，依赖和中间
//     文件全堆在一起。
//  2. 改成"产物必须写到 /workspace/output/" → 跟 skill 自带的目录约定打架
//     （pptx-generator 规定 slides/output/），模型先按 skill 生成再 copy 一份。
//  3. 退回"不强制落点"但仍在这儿介绍 output/ 的语义 → 模型把它当成"交付目的地"，
//     照 skill 写完还要再 shutil.copy 过去一份，同一内容两个路径。
//     根因是这段说明本身：只要工具层提 output/ 是"给用户看的地方"，模型就会
//     觉得不搬过去等于没交付。加硬规则（"禁止 copy"）只压住症状，删掉整段才
//     去根 —— 整个 /workspace 都会展示给用户，本来就没有"主产物目录"这回事。
//
// Node 那段留着，因为它挡的是真实故障而不是审美问题：
//  4. npm 反复把 node_modules 装进 /workspace（一次 352 个文件），撑爆 500MB
//     配额并拖慢共享存储。光说"别在工作区装"没用 —— skill 原文写的是就地
//     `npm install`，模型照原文走，所以必须给出替代命令。
//  5. 上一版只给 shell 写法 `NODE_PATH=... node x.js`，模型翻译成 Python 的
//     subprocess.run(env={'NODE_PATH': ...})，而 env= 传字典是整体替换、PATH
//     被清空 → FileNotFoundError: 'node'。模型据此判定"沙箱没装 Node.js"，
//     改用 pip 装 Python 库从头重做，产物质量掉一半。
//     结论：示范不能留跨语言翻译的空间（两种语言各给一份），且要明说
//     "找不到 node ≠ 没装"，掐掉那条错误的降级路径。示例里的包名/脚本名用
//     占位符 —— 写死某个 skill 的具体文件名会让其他场景照抄。
func (t *CodeInterpreterTool) Description() string {
	return `在安全沙箱中执行代码（Python / Bash / Node）。

工作区 /workspace 持久保存，整个目录都会展示给用户浏览下载。初始 cwd 是 /workspace。
写文件一律用绝对路径 —— 跨多次调用 cwd 不保证不变，相对路径会落到意想不到的地方。

## Node 依赖（硬规则）

npm install 一律在 /tmp/npm-work 里做，node_modules 绝不能落进 /workspace
—— 几千个小文件会撑爆工作区配额（500MB）并拖慢共享存储。skill 的说明里若写着
就地 ` + "`npm install`" + `，也按下面来：那是本地环境的假设，这里的工作区在共享存储上。

用 bash 跑最省事（language="bash"）：

    mkdir -p /tmp/npm-work && cd /tmp/npm-work && npm install <包名>
    cd <脚本目录> && NODE_PATH=/tmp/npm-work/node_modules node <脚本.js>

用 Python 的 subprocess 调 node 时，环境变量必须**在 os.environ 基础上追加**：

    import os, subprocess
    env = {**os.environ, 'NODE_PATH': '/tmp/npm-work/node_modules'}
    subprocess.run(['node', '<脚本.js>'], cwd='<脚本目录>', env=env, check=True)

写成 env={'NODE_PATH': ...} 会**整体替换**环境变量、把 PATH 一起清空，随后报
FileNotFoundError: 'node'。这不是"沙箱没装 Node"（node v20 在 /usr/local/bin/node），
是这行写错了 —— 修这一行就好，别改用 Python 库重做整件事。

Python 依赖：pip install <pkg> 直接跑，装到系统 site-packages，不占工作区。`
}
func (t *CodeInterpreterTool) IsConcurrencySafe() bool { return false }

func (t *CodeInterpreterTool) InputSchema() tool.Schema {
	return tool.Schema{
		Type: tool.SchemaTypeObject,
		Properties: map[string]tool.Schema{
			"code":     {Type: tool.SchemaTypeString, Description: "要执行的代码"},
			"language": {Type: tool.SchemaTypeString, Description: "语言: python/bash", Default: "python"},
		},
		Required: []string{"code"},
	}
}

func (t *CodeInterpreterTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var params struct {
		Code     string `json:"code"`
		Language string `json:"language"`
	}
	json.Unmarshal(input, &params)
	if params.Language == "" {
		params.Language = "python"
	}

	if err := t.ensureSandbox(ctx); err != nil {
		return tool.Result{
			IsError: true,
			Content: []tool.ResultContent{{Type: tool.ResultContentTypeText, Text: fmt.Sprintf("sandbox init failed: %v", err)}},
		}, nil
	}

	result, err := t.execCode(ctx, params.Code, params.Language)
	if err != nil {
		return tool.Result{
			IsError: true,
			Content: []tool.ResultContent{{Type: tool.ResultContentTypeText, Text: fmt.Sprintf("sandbox exec failed: %v", err)}},
		}, nil
	}

	// H5.C: 在返回前把 sandbox 结构化信息 (artifacts/stdout/stderr/duration_ms)
	// 挂在一条 Progress 事件的 Data 上，供 collector 汇总到 chat_messages.extra —
	// 引擎的 Completed 事件 Message 只是字面 "completed"，Data 只用于文件编辑，
	// 拿不到沙箱产物元数据。
	if out != nil && tctx != nil {
		var payload map[string]any
		if err := json.Unmarshal(result, &payload); err == nil && payload != nil {
			select {
			case out <- tool.Event{
				Type:      tool.EventTypeProgress,
				ToolName:  t.Name(),
				ToolUseID: tctx.ToolUseID,
				Data:      payload,
			}:
			default:
			}
		}
	}

	return tool.Result{
		Content: []tool.ResultContent{{Type: tool.ResultContentTypeJSON, Data: result}},
	}, nil
}

func (t *CodeInterpreterTool) ensureSandbox(ctx context.Context) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.sandboxReady {
		return nil
	}

	// 只传 name + bundle_uri：lea-tools 拿 uri 去 S3 取包，name 当目录名。
	// SKILL.md 正文不必传 —— 那个走 activate_skill 注入 prompt，两条路各管一头。
	skills := make([]map[string]string, 0, len(t.skills))
	for _, s := range t.skills {
		if s.BundleURI == "" {
			continue // 没打包的 skill 无从投递，跳过而不是传个空 uri 让下游报错
		}
		skills = append(skills, map[string]string{
			"name":       s.Name,
			"bundle_uri": s.BundleURI,
		})
	}

	payload := map[string]any{
		"session_id": t.sessionID,
		"skill_id":   t.skillID,
		"user_id":    t.userID,
		"tenant_id":  t.tenantID,
		"skills":     skills,
	}
	body, _ := json.Marshal(payload)

	req, err := http.NewRequestWithContext(ctx, "POST",
		toolsGatewayURL+"/sandbox/sessions",
		bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := sandboxCreateClient.Do(req)
	if err != nil {
		return fmt.Errorf("sandbox service unreachable: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return fmt.Errorf("sandbox create returned %d", resp.StatusCode)
	}

	var result struct {
		SandboxSessionID string `json:"sandbox_session_id"`
	}
	json.NewDecoder(resp.Body).Decode(&result)
	t.sandboxSessID = result.SandboxSessionID
	t.sandboxReady = true
	return nil
}

func (t *CodeInterpreterTool) execCode(ctx context.Context, code, language string) (json.RawMessage, error) {
	params := map[string]any{
		"code":       code,
		"language":   language,
		"session_id": t.sandboxSessID,
		"timeout":    180,
	}

	result, err := ExecuteRemoteTool(ctx, "code_interpreter", params, t.tenantID, t.userID)
	if err != nil {
		return nil, err
	}
	if result.IsError {
		msg := "sandbox exec failed"
		for _, c := range result.Content {
			if c.Type == tool.ResultContentTypeText && c.Text != "" {
				msg = c.Text
				break
			}
		}
		return nil, fmt.Errorf("%s", msg)
	}
	for _, c := range result.Content {
		if c.Type != tool.ResultContentTypeJSON {
			continue
		}
		if raw, ok := c.Data.(json.RawMessage); ok && len(raw) > 0 {
			return raw, nil
		}
		if c.Data != nil {
			return json.Marshal(c.Data)
		}
	}
	return json.RawMessage(`{}`), nil
}

func (t *CodeInterpreterTool) Destroy(ctx context.Context) {
	if !t.sandboxReady {
		return
	}
	url := fmt.Sprintf("%s/sandbox/sessions/%s", toolsGatewayURL, t.sandboxSessID)
	req, _ := http.NewRequestWithContext(ctx, "DELETE", url, nil)
	httpClient.Do(req)
}
