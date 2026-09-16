package runtime

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path"
	"strconv"
	"strings"

	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"

	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
)

// ── DeliverArtifact Tool ──────────────────────────────────────
//
// session_artifacts 表的**唯一写入者**，跟 save_plan 之于 session_plans 是同一条线。
//
// ## 它取代的是一套猜
//
// 会话工作区里，一份 40 页的报告和模型 mkdir 出来的临时目录在文件系统层面长得
// 一模一样。现有代码里唯一的判据是 `in_output = path.startswith("output/")`
// （lea-tools 的 FileEntry.as_dict）—— 它算出来、传给前端、然后前端一行都没用，
// 因为它不可信：模型没有任何理由把成品写进 output/，而它也确实经常不写。
//
// 试过的替代方案都更差：
//
//	扩展名白名单     .pptx 是成品，.csv 是中间产物？模型交付数据集时正好反过来。
//	最后修改时间     最后写的往往是调试脚本。
//	让另一个模型判   给每轮加一次调用和一份不可解释的误判。
//
// 换成声明之后，**零行 = 这轮没有成品**，而不是"我们没识别出来"。这个区别在
// 用户看空文件库时是全部：前者他知道该让模型交付一下，后者他不知道该干什么。
//
// ## 为什么不自动交付"所有 output/ 里的文件"
//
// 那等于把判据从"猜"换成"让模型往某个目录里写就算数"，而模型并不知道这条规矩
// 意味着什么 —— 它会把中间产物也写进去（因为那个目录看起来就是放输出的）。
// 显式调用才有"我确认这是给用户的"这层意思。

type DeliverArtifactTool struct {
	base      fileToolBase
	projectID sql.NullInt64
}

func (t *DeliverArtifactTool) Name() string { return "deliver_artifact" }

func (t *DeliverArtifactTool) Description() string {
	return "把工作区里的一个文件交付给用户，作为本次任务的成品。" +
		"交付后它会进入用户的长期文件库——用户在文件列表里能看到它，也能在别的对话里引用它。" +
		"只交付用户真正要的最终产物（报告、图表、数据集、代码包）；" +
		"中间脚本、临时数据、调试输出不要交付。" +
		"文件必须已经写好——本工具不创建文件，只是把已有的文件登记成成品。" +
		"同一个文件改好后可以再交付一次，会覆盖上一次的登记。"
}

func (t *DeliverArtifactTool) IsConcurrencySafe() bool { return true }

func (t *DeliverArtifactTool) InputSchema() tool.Schema {
	return tool.Schema{
		Type: tool.SchemaTypeObject,
		Properties: map[string]tool.Schema{
			"path": {
				Type:        tool.SchemaTypeString,
				Description: "文件在工作区里的相对路径，如 output/report.pptx 或 analysis.xlsx",
			},
			"title": {
				Type:        tool.SchemaTypeString,
				Description: "给人看的标题，如「2025 年三季度销售分析报告」。用户在文件库里看到的就是这个，写得具体一点——他几周后回来找它时靠的是这句话。",
			},
			"description": {
				Type:        tool.SchemaTypeString,
				Description: "一句话说明这份成品是什么、包含哪些内容。可选。",
			},
		},
		Required: []string{"path", "title"},
	}
}

func (t *DeliverArtifactTool) Run(ctx context.Context, input json.RawMessage, tctx *tool.Context, out chan<- tool.Event) (tool.Result, error) {
	var params struct {
		Path        string `json:"path"`
		Title       string `json:"title"`
		Description string `json:"description"`
	}
	if err := json.Unmarshal(input, &params); err != nil {
		return toolError(fmt.Sprintf("参数解析失败: %v", err)), nil
	}
	relPath := strings.TrimSpace(params.Path)
	if relPath == "" {
		return toolError("path 不能为空"), nil
	}
	relPath = strings.TrimPrefix(relPath, "./")

	db, err := persistence.DB()
	if err != nil || db == nil {
		// 登记不了就别去 OBS 写字节 —— 那会留下一个没有任何行指向的对象，
		// 既不在用户的文件库里，也不会被任何引用计数算到，纯粹占空间。
		return toolError("交付登记不可用（数据库未配置），文件仍在工作区里"), nil
	}

	// ── 1. 发布到 OBS ───────────────────────────────────────
	//
	// 先发布再登记：登记那一行要写 obs_key/sha256/size，而这三个值只有摸过字节的人
	// 算得出来。反过来（先登记再发布）会在发布失败时留一行 obs_key 为空的记录，
	// 而那正是我们要区分开的"没存住"降级 —— 用同一个形状表达"还没存"和"存不了"
	// 就分不出来了。
	pub, pubErr := t.publish(ctx, relPath)
	if pubErr != nil {
		// 4xx 是模型能自己纠正的（路径不对、文件是空的、超大小上限），原样交回去。
		return toolError(pubErr.Error()), nil
	}

	// ── 2. 配额：软放行 ─────────────────────────────────────
	//
	// 跟上传路径刻意相反。上传超配额是**硬拒**（507）—— 用户还拿着原件，删几个
	// 文件重传很便宜。交付超配额是**软放行** —— 这份产物背后是几分钟的模型劳动，
	// 为了配额把它丢掉换不来任何东西，而 NFS 上那份还在（30 天内用户仍能下载）。
	//
	// 所以超限时：行照落，obs_key 留空，给模型一句降级说明让它转述给用户。
	degraded := ""
	if pub != nil {
		if over, msg := t.overQuota(ctx, db, pub.Size, pub.SHA256); over {
			degraded = msg
			// 已经写进 OBS 的那个对象怎么办：留着。它是内容寻址的，删了之后
			// 用户腾出空间再交付一次还得重新上传同样的字节；而留着它不会被
			// 任何配额算到（配额数的是**库里的行**，这一行的 obs_key 是空的）。
			// 代价是一个孤儿对象，由后续对账收。
			pub.OBSKey = ""
		}
	}

	// ── 3. 登记 ─────────────────────────────────────────────
	a := persistence.Artifact{
		TenantID:    t.base.tenantID,
		UserID:      t.base.userID,
		SessionID:   t.base.sessionID,
		ProjectID:   t.projectID,
		RelPath:     relPath,
		Filename:    path.Base(relPath),
		Title:       strings.TrimSpace(params.Title),
		Description: strings.TrimSpace(params.Description),
	}
	if pub != nil {
		a.OBSKey = pub.OBSKey
		a.SHA256 = pub.SHA256
		a.Size = pub.Size
		a.MimeType = pub.MimeType
		if pub.Filename != "" {
			a.Filename = pub.Filename
		}
	}

	id, err := persistence.UpsertArtifact(ctx, db, a)
	if err != nil {
		return toolError(fmt.Sprintf("交付登记失败: %v（文件仍在工作区里）", err)), nil
	}
	a.ID = id

	summary := persistence.ArtifactSummaryLine(a)
	emitToolResult(out, t.Name(), tctx.ToolUseID, summary, map[string]any{
		"id":       id,
		"filename": a.Filename,
		"title":    a.Title,
		"size":     a.Size,
		"stored":   a.OBSKey != "",
	})

	text := "已交付：" + summary
	if degraded != "" {
		text += "\n" + degraded
	}
	return tool.Result{Content: []tool.ResultContent{
		{Type: tool.ResultContentTypeText, Text: text},
	}}, nil
}

type publishResult struct {
	OBSKey   string `json:"obs_key"`
	SHA256   string `json:"sha256"`
	Size     int64  `json:"size"`
	Filename string `json:"filename"`
	MimeType string `json:"mime_type"`
}

// publish 让 lea-tools 把字节从会话工作区搬到 OBS。
//
// 字节不经过 lea-agent：文件已经在共享存储上，lea-tools 读得到。让这边先 download
// 再 POST 上去，一份 200MB 的 PPT 要在两个服务之间多走一趟网络和两趟内存，
// 而这边一个字节都不需要看。
func (t *DeliverArtifactTool) publish(ctx context.Context, relPath string) (*publishResult, error) {
	body := map[string]string{
		"tenant_id":  t.base.tenantID,
		"session_id": t.base.sessionID,
		"path":       relPath,
	}
	raw, detail, err := t.base.callFiles(ctx, http.MethodPost, "/api/files/artifacts/publish", nil, body)
	if err != nil {
		// 传输层故障：lea-tools 不可达。这时**不该**登记 —— 见 Run 里的注释。
		return nil, fmt.Errorf("文件服务不可达，未能交付：%v", err)
	}
	if detail != "" {
		return nil, fmt.Errorf("%s", detail)
	}
	var res publishResult
	if err := json.Unmarshal(raw, &res); err != nil {
		return nil, fmt.Errorf("交付响应解析失败: %v", err)
	}
	return &res, nil
}

// overQuota 判用户和租户两级配额。返回 (是否超, 给模型的降级说明)。
//
// ## 为什么把已有同 sha 的情况排除掉
//
// 内容寻址：这份内容如果库里已经有了（用户上传过、或上一轮交付过），它在 OBS 上
// **不占新空间**。这时按 size 判配额会把一个零成本的操作挡掉，而用户删什么都
// 缓解不了 —— 那些字节本来就是复用的。
//
// ## 判不出来的时候放行
//
// 数据库查询失败时返回"没超"。配额是个软约束（真正的硬边界是对象存储本身），
// 而查询失败就拒绝交付，等于一次数据库抖动让用户丢掉几分钟的模型劳动。
// 反过来的风险是短暂超一点点配额，下次查得动时自然收紧。
func (t *DeliverArtifactTool) overQuota(ctx context.Context, db *sql.DB, size int64, sha string) (bool, string) {
	if size <= 0 {
		return false, ""
	}

	// 同 sha 已存在 → 不占新空间，直接放行。
	if sha != "" {
		if n, err := persistence.CountSHARefs(ctx, db, t.base.tenantID, sha); err == nil && n > 0 {
			return false, ""
		}
	}

	userQuota := UserStorageQuotaBytes()
	if userQuota > 0 {
		u, err := persistence.UserStorageUsage(ctx, db, t.base.tenantID, t.base.userID, userQuota)
		if err == nil && u.TotalBytes+size > userQuota {
			return true, fmt.Sprintf(
				"⚠️ 这份成品没能存进用户的文件库：他的存储已用 %dMB / 上限 %dMB。"+
					"文件还在本次会话的工作区里（30 天内可下载），但别的对话引用不到它。"+
					"请告诉用户可以在文件管理里删掉不需要的文件后，让你重新交付一次。",
				u.TotalBytes/1048576, userQuota/1048576)
		}
	}

	tenantQuota := TenantStorageQuotaBytes()
	if tenantQuota > 0 {
		used, err := persistence.TenantStorageUsage(ctx, db, t.base.tenantID)
		if err == nil && used+size > tenantQuota {
			return true, fmt.Sprintf(
				"⚠️ 这份成品没能存进文件库：所在租户的存储配额已满"+
					"（已用 %dMB / 上限 %dMB）。文件还在本次会话的工作区里（30 天内可下载）。"+
					"这不是用户个人能解决的，请告诉他联系管理员。",
				used/1048576, tenantQuota/1048576)
		}
	}
	return false, ""
}

// UserStorageQuotaBytes 单个用户的文件库上限。0 = 不限。
//
// 默认给一个具体的数（2GB）而不是"不限"：不限的后果（一个用户把 bucket 占满）
// 要到出事那天才被发现，而那天所有人一起写不进去。
//
// 导出是因为**判配额的地方有两处，口径必须是同一个**：上传端点（硬拒 507）和
// deliver_artifact（软放行）。各读各的环境变量的话，两边不一致时会出现
// "交付说满了、上传说没满"这种自相矛盾的提示。
func UserStorageQuotaBytes() int64 { return quotaBytes("USER_STORAGE_QUOTA_MB", 2048) }

// TenantStorageQuotaBytes 整个租户的文件库上限。默认 0 = 不限。
//
// 这里默认不限跟用户级相反：租户配额是运维按 bucket 实际容量算出来的数，我们猜不出，
// 而猜一个太小的值会在部署当天把整个租户挡住。
func TenantStorageQuotaBytes() int64 { return quotaBytes("TENANT_STORAGE_QUOTA_MB", 0) }

// quotaBytes 读一个以 MB 为单位的配额环境变量，返回字节数。0 或非法值 = 不限。
func quotaBytes(name string, defaultMB int64) int64 {
	mb := defaultMB
	if raw := strings.TrimSpace(os.Getenv(name)); raw != "" {
		if v, err := strconv.ParseInt(raw, 10, 64); err == nil && v >= 0 {
			mb = v
		}
	}
	return mb * 1024 * 1024
}

// nullInt64FromPtr 把会话的 *int project_id 转成入库用的 NullInt64。
// nil（不属于任何项目）→ 一个 SQL NULL，而不是 0 —— 0 会撞上某个真实的项目 id。
func nullInt64FromPtr(p *int) sql.NullInt64 {
	if p == nil {
		return sql.NullInt64{}
	}
	return sql.NullInt64{Int64: int64(*p), Valid: true}
}

func toolError(msg string) tool.Result {
	return tool.Result{IsError: true, Content: []tool.ResultContent{
		{Type: tool.ResultContentTypeText, Text: msg},
	}}
}
