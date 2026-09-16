package persistence

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
)

// Attachment 覆盖读和写两条路径。
//
// ## 三个消费口，各自独立
//
//	ExtractedText   注入进对话（问答、总结）—— 一直有
//	StagedPath      NFS 会话副本的文件名，RO 挂进沙箱 /mnt/uploads（python 处理）
//	FileKey         OBS 上的长期真源（下载、会话重建时重新物化）
//
// 后两个以前是缺的：FileKey 一直写空串（"H5 版本不上 OBS"），于是**上传的字节流在
// HTTP 响应返回后就被丢了**。后果不是"少个下载功能"，是模型结构上无法处理文件本身 ——
// 沙箱里 ls 不到，Read 也读不到，只能照着抽出来的文本重写一份。
//
// 空串的含义要分清，注入文本靠它决定说什么：
//
//	FileKey == ""      没落对象存储（OBS 不可达）—— 文件没保住，只有文本
//	StagedPath == ""   没物化（沙箱没开、没共享存储、或配额满）—— 能问不能改
type Attachment struct {
	ID            int64
	TenantID      string
	UserID        string
	SessionID     string
	Filename      string
	FileKey       string
	FileType      string
	FileSize      int64
	MediaType     string
	ExtractedText string

	// SHA256 是内容指纹。它同时是 FileKey 里的文件名、解析结果的缓存键、以及
	// StagedPath 的前缀 —— 但**别从那些字符串里反解它**，读这一列。
	SHA256 string

	// StagedPath 是会话 uploads/ 目录下的文件名（不是完整路径）。沙箱里的绝对路径
	// 由 UploadsMount + "/" + StagedPath 拼出来，见 SandboxPath。
	//
	// 存下来而不是从 SHA256+Filename 现算：清洗规则（超长中文名按字节截断、特殊字符
	// 替换）住在 lea-tools 的 safe_stage_name，两边各实现一遍会在边缘情况下不一致，
	// 而症状是模型拿到一个不存在的路径、报错说"文件不存在"——看起来像物化失败。
	StagedPath string

	// PageCount 是分页格式（pdf / pptx）的总页数，其余格式为 0。
	// 0 = "这个格式没有页的概念"，不是"零页"。
	PageCount int

	// CreatedAt 只有列表路径（ListUserUploads）会填。其余查询不取这一列，
	// 那里拿到的是空串 —— 别拿它做判断，它的含义是"这次查询没要"。
	CreatedAt string
}

// GetAttachmentForRead loads an attachment that belongs to the current tenant
// and session. The session check keeps attachment IDs from being reused across
// conversations while allowing Read to work before staging.
func GetAttachmentForRead(
	ctx context.Context, db *sql.DB, tenantID, sessionID string, id int64,
) (Attachment, error) {
	var a Attachment
	if db == nil {
		return a, sql.ErrNoRows
	}
	err := db.QueryRowContext(ctx,
		`SELECT id, filename, COALESCE(file_key, ''), COALESCE(file_type, ''),
		        COALESCE(file_size, 0), COALESCE(media_type, 'text'),
		        COALESCE(extracted_text, ''), COALESCE(sha256, ''),
		        COALESCE(staged_path, '')
		   FROM chat_attachments
		  WHERE id = ? AND tenant_id = ? AND session_id = ?`,
		id, tenantID, sessionID,
	).Scan(&a.ID, &a.Filename, &a.FileKey, &a.FileType, &a.FileSize,
		&a.MediaType, &a.ExtractedText, &a.SHA256, &a.StagedPath)
	return a, err
}

// UploadsMount 是用户上传原件在沙箱里的挂载点。
//
// ⚠️ 必须跟 lea-tools 的 file_store.UPLOADS_MOUNT 一致 —— 那边用它做 host-mount 的
// mountPath 和读侧的路径前缀，这边用它拼注入文本里那句"文件在 X"。对不上的后果是
// 模型拿着一个不存在的路径去 open()，而报错会是"文件不存在"，看起来像物化没成功。
const UploadsMount = "/mnt/uploads"

// SandboxPath 返回这个附件在沙箱里的绝对路径；没物化则返回空串。
func (a Attachment) SandboxPath() string {
	if a.StagedPath == "" {
		return ""
	}
	return UploadsMount + "/" + a.StagedPath
}

// InsertAttachment 落一行 chat_attachments，返回自增 id。
// created_at 由 MySQL default now() 填。
func InsertAttachment(ctx context.Context, db *sql.DB, a Attachment) (int64, error) {
	if db == nil {
		return 0, nil
	}
	if a.MediaType == "" {
		a.MediaType = "text"
	}
	res, err := db.ExecContext(ctx,
		`INSERT INTO chat_attachments
		    (tenant_id, user_id, session_id, filename, file_key,
		     file_type, file_size, media_type, extracted_text, sha256, staged_path)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		a.TenantID, a.UserID, a.SessionID, a.Filename, a.FileKey,
		a.FileType, a.FileSize, a.MediaType, a.ExtractedText, a.SHA256, a.StagedPath,
	)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

// LoadAttachments 按 id 列表读 chat_attachments，用 tenant 做越权护栏。
// Python 侧 chat.py 没做 tenant 过滤（隐患），Go 侧顺手补上。
// 空 id 列表 → 返回 nil。ids 里查不到的行静默忽略（跟 Python 语义一致）。
//
// 多取了 media_type / staged_path / file_type：注入文本要按"有没有物化"和"是什么类型"
// 分档（表格类偏向进沙箱算、图片要说明文本层为空），光有 extracted_text 判不出来。
func LoadAttachments(ctx context.Context, db *sql.DB, tenantID string, ids []int64) ([]Attachment, error) {
	if db == nil || len(ids) == 0 {
		return nil, nil
	}
	placeholders := make([]string, len(ids))
	args := make([]any, 0, len(ids)+1)
	for i, id := range ids {
		placeholders[i] = "?"
		args = append(args, id)
	}
	args = append(args, tenantID)

	q := fmt.Sprintf(
		`SELECT id, filename, COALESCE(extracted_text, ''),
		        COALESCE(media_type, 'text'), COALESCE(file_type, ''),
		        COALESCE(staged_path, ''), COALESCE(file_key, ''),
		        COALESCE(file_size, 0)
		   FROM chat_attachments
		  WHERE id IN (%s) AND tenant_id = ?`,
		strings.Join(placeholders, ","),
	)
	rows, err := db.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Attachment
	for rows.Next() {
		var a Attachment
		if err := rows.Scan(&a.ID, &a.Filename, &a.ExtractedText,
			&a.MediaType, &a.FileType, &a.StagedPath, &a.FileKey, &a.FileSize); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

// LoadSessionAttachmentsForStage returns the current session's upload records.
func LoadSessionAttachmentsForStage(ctx context.Context, db *sql.DB, tenantID, sessionID string) ([]Attachment, error) {
	if db == nil {
		return nil, nil
	}
	rows, err := db.QueryContext(ctx,
		`SELECT id, filename, COALESCE(file_key, ''), COALESCE(file_type, ''),
		        COALESCE(file_size, 0), COALESCE(media_type, 'text'),
		        COALESCE(staged_path, '')
		   FROM chat_attachments
		  WHERE tenant_id = ? AND session_id = ?
		  ORDER BY id`, tenantID, sessionID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Attachment
	for rows.Next() {
		var a Attachment
		if err := rows.Scan(&a.ID, &a.Filename, &a.FileKey, &a.FileType,
			&a.FileSize, &a.MediaType, &a.StagedPath); err != nil {
			return nil, err
		}
		out = append(out, a)
	}
	return out, rows.Err()
}

func MarkAttachmentStaged(ctx context.Context, db *sql.DB, tenantID, sessionID string, id int64, stagedPath string) error {
	if db == nil {
		return nil
	}
	_, err := db.ExecContext(ctx,
		`UPDATE chat_attachments SET staged_path = ?
		  WHERE id = ? AND tenant_id = ? AND session_id = ?`,
		stagedPath, id, tenantID, sessionID)
	return err
}

// GetAttachmentForDownload 读一行做下载用，同时校验归属。
//
// 归属校验在 SQL 里做（tenant + user 都要匹配），不是查出来再比 —— 那样容易漏，而且
// 这个函数的全部意义就是"确认这是他的文件再给字节"。
//
// 返回 sql.ErrNoRows 表示"不存在或不是你的"，两者故意不区分：附件 id 是自增的，
// 区分开就是一个可枚举的存在性预言（"这个 id 存在但不是我的"）。session_id 是 UUID
// 所以那边可以容忍，这里不行。
//
// ## 为什么带上 sha256
//
// 下载不需要它，删除需要。删除路径（handleDeleteUserFile）用这个函数取行，然后靠
// sha 去 CountSHARefs 判断"还有没有别的行指着同一个对象"。
//
// 少这一列的后果不是报错，是**静默失效**：sha 空串让
// `if objKey != "" && sha != ""` 整块被跳过，于是 object_deleted 恒为 false，
// 上传件的 OBS 对象一个都删不掉，删多少次都是孤儿。真链路验收里就是这么发现的
// —— 表现是"删了但对象还在"，而日志里连一条引用计数的记录都没有（那才是线索：
// 不是判断错了，是根本没判）。
//
// 多取一列的代价是零（同一行、覆盖不到索引也就多读几十字节），漏掉的代价是
// 一个只能靠对账发现的存储泄漏。所以这个函数返回"这一行的身份信息"，而不是
// 严格按调用方名字裁列。
func GetAttachmentForDownload(
	ctx context.Context, db *sql.DB, tenantID, userID string, id int64,
) (Attachment, error) {
	var a Attachment
	if db == nil {
		return a, sql.ErrNoRows
	}
	err := db.QueryRowContext(ctx,
		`SELECT id, filename, COALESCE(file_key, ''), COALESCE(file_type, ''),
		        COALESCE(file_size, 0), COALESCE(media_type, 'text'),
		        COALESCE(sha256, '')
		   FROM chat_attachments
		  WHERE id = ? AND tenant_id = ? AND user_id = ?`,
		id, tenantID, userID,
	).Scan(&a.ID, &a.Filename, &a.FileKey, &a.FileType, &a.FileSize, &a.MediaType,
		&a.SHA256)
	return a, err
}

// GetUploadForImport 读一行上传件，取的是**把它复制进另一个会话所需要的全部列**。
//
// 跟 GetAttachmentForDownload 的区别是多取 staged_path / extracted_text 两列
// （sha256 那边现在也取，见那个函数的注释），而这几列各有一个不能省的理由：
//
//	sha256          导入后新行也要带它，否则配额的 SUM(DISTINCT sha256, size) 会把
//	                同一份内容算两遍 —— 导进 10 个会话就凭空多算 10 份
//	staged_path     物化时原样传回去，新会话里的文件名才跟原会话一致。不一致本身
//	                不算错，但两个会话里同一个文件叫两个名字，排查时会以为是两份
//	extracted_text  解析结果是**按内容寻址缓存**的，同一个 sha 再解析一遍逐字节相同。
//	                复制过去 = 免费；不复制 = 用户在新对话里问这个文件的内容时，
//	                Read 落到 renderUpload 的"没提取到文本"分支，而文本明明就在库里
//
// 归属过滤同样在 SQL 里，同样不区分"不存在"和"不是你的"（见 GetAttachmentForDownload）。
func GetUploadForImport(
	ctx context.Context, db *sql.DB, tenantID, userID string, id int64,
) (Attachment, error) {
	var a Attachment
	if db == nil {
		return a, sql.ErrNoRows
	}
	err := db.QueryRowContext(ctx,
		`SELECT id, filename, COALESCE(file_key, ''), COALESCE(file_type, ''),
		        COALESCE(file_size, 0), COALESCE(media_type, 'text'),
		        COALESCE(sha256, ''), COALESCE(staged_path, ''),
		        COALESCE(extracted_text, '')
		   FROM chat_attachments
		  WHERE id = ? AND tenant_id = ? AND user_id = ?`,
		id, tenantID, userID,
	).Scan(&a.ID, &a.Filename, &a.FileKey, &a.FileType, &a.FileSize,
		&a.MediaType, &a.SHA256, &a.StagedPath, &a.ExtractedText)
	return a, err
}

// GetAttachmentByStagedPath 按沙箱内的文件名反查附件行。
//
// 这是 Read 工具读 /mnt/uploads 的入口：模型拿到的是路径，而我们要的是**上传时就已经
// 抽好的那份文本**。
//
// ## 为什么查库而不是重新解析
//
// extracted_text 在上传那一刻就写进去了（attachment.go 的第 2 步）。重新调一次
// lea-parser 意味着：MinerU 那条路最长 120 秒、要把 30MB 字节再走两跳、而结果跟库里
// 那份**逐字节相同** —— 因为解析结果本来就是按 sha256 内容寻址缓存的。
//
// 所以这张表就是解析缓存。查它是 O(1)，重新解析是 O(分钟)。
//
// tenant + session 双重过滤：staged_path 只在会话目录内唯一，不带 session 会串会话。
// 查不到返回 sql.ErrNoRows，调用方据此回落到普通文件读取（那条路能读 workspace 里
// 模型自己写的文件）。
//
// ⚠️ 不查 page_count：**那一列不存在**。019 只加了 sha256 和 staged_path，
// InsertAttachment 的 INSERT 里也没有它 —— PageCount 只活在上传响应里，落库即丢。
// 所以从库里读回来的 Attachment 永远 PageCount==0（注入文本那句"共 N 页"因此从未
// 在生产里出现过）。要修得先加一列，不在这一轮的范围内。
func GetAttachmentByStagedPath(
	ctx context.Context, db *sql.DB, tenantID, sessionID, stagedPath string,
) (Attachment, error) {
	var a Attachment
	if db == nil {
		return a, sql.ErrNoRows
	}
	err := db.QueryRowContext(ctx,
		`SELECT id, filename, COALESCE(extracted_text, ''), COALESCE(media_type, 'text'),
		        COALESCE(file_type, ''), COALESCE(file_size, 0), COALESCE(staged_path, '')
		   FROM chat_attachments
		  WHERE tenant_id = ? AND session_id = ? AND staged_path = ?
		  ORDER BY id DESC LIMIT 1`,
		tenantID, sessionID, stagedPath,
	).Scan(&a.ID, &a.Filename, &a.ExtractedText, &a.MediaType,
		&a.FileType, &a.FileSize, &a.StagedPath)
	return a, err
}

// BuildContentWithAttachments 拼装注入进用户消息的附件段。
//
// 与 lea-server chat.py::_build_message_content 的 marker 保持兼容（deriveTitle 的
// stripAttachmentPreamble 要吃到同一个 `[文件: `）。
//
// ## 只给路径和元信息，**不注入正文**
//
// 上一版把 extracted_text 拼进消息，于是这段文本必须自己回答"内容全不全""要不要去
// 补读""截断了怎么说" —— 五档路线、预算平分、按比例报百分比，两百多行都在处理
// **"我替模型决定了它该看多少"**带来的后遗症。
//
// 现在换成：文本由 Read 工具按需取（见 runtime/file_tools.go 的 readUpload，它查的
// 就是同一份 extracted_text）。于是这里只需要说三件事 ——
//
//	是什么   文件名 + 页数 + 大小
//	在哪     沙箱绝对路径
//	怎么取   用 Read 读内容；要改就进沙箱
//
// 换来的代价是**简单问答多一次往返**（问 → 调 Read → 答）。收益是那五档路线连同
// 截断话术一起消失，而且新格式只要 parser 支持就自动可用，不用回来改注入模板。
//
// ## 为什么"内容全不全"的问题跟着一起消失了
//
// 那句话（"以下为该文件的全部可提取文本，无需再次读取"）是为了消除一个歧义：模型
// 拿到一段正文时分不清"这就是全部"还是"这只是开头"。而现在它拿到的是**路径**，
// 没有正文可误解 —— 要内容它必须调 Read，Read 自己会说清截没截断。
//
// 歧义没有被更好地回答，是被移到了唯一知道答案的那一层。
//
// 无附件 → 原样返回 message。
func BuildContentWithAttachments(message string, atts []Attachment) string {
	if len(atts) == 0 {
		return message
	}
	parts := make([]string, 0, len(atts))
	for _, a := range atts {
		parts = append(parts, describeAttachment(a))
	}
	return strings.Join(parts, "\n\n") + "\n\n" + message
}

// formatSize 把字节数转换成人类可读的格式。
//
// 报大小是为了让模型对"这文件读起来贵不贵"有个量感 —— 一份 200KB 的 pdf 和一份 2KB
// 的 txt，它该做的决定不同（前者可能要分段读或先搜关键词）。
func formatSize(bytes int64) string {
	const unit = 1024
	if bytes < unit {
		return fmt.Sprintf("%d B", bytes)
	}
	div, exp := int64(unit), 0
	for n := bytes / unit; n >= unit; n /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %cB", float64(bytes)/float64(div), "KMGTPE"[exp])
}

// describeAttachment 拼一个附件的注入段。
//
// ## 只说"能做什么"，不说"去做什么"
//
// 这里**不知道用户想干什么** —— 同一个 PDF 可能是"写份报告"（要生成新东西）也可能是
// "改第 3 页"（要原地改）。所以修改引导必须是条件式的（"如需修改：…"）而不是命令式的。
//
// 早先的版本无条件写"修改方式：…不要重新生成一份"，实测 dump 出来的效果是：用户说
// "根据这份调研写报告"时，注入文本在劝它不要重新生成 —— 而重新生成正是要做的事。
// 那是把"我猜你要改文件"当成了事实。
//
// ## 工具名必须是 Read，不是 read_file
//
// 引擎内置工具的名字是 `Read`（见 runtime/file_tools.go 顶部：子代理白名单按名字
// **精确匹配**，自造名字会让工具在子代理那边匹配不到）。注入文本里写错名字的后果是
// 模型去调一个不存在的工具，然后在"没有这个工具"的错误上打转 —— 而它手里明明有能用
// 的那个。
func describeAttachment(a Attachment) string {
	var b strings.Builder

	// 头一行：文件名 + 元信息。
	//
	// 页数只在分页格式上报（PageCount>0），0 表示"这个格式没有页的概念"而不是"零页"。
	// ⚠️ 从库里读回来的附件 PageCount 恒为 0 —— chat_attachments 没有 page_count 这一
	// 列（019 只加了 sha256/staged_path，INSERT 里也没有它），所以这个分支目前只在
	// 上传那一刻的内存对象上生效。
	//
	// 大小一直有，它给模型一个"读起来贵不贵"的量感。
	b.WriteString("[文件: " + a.Filename)
	if a.PageCount > 0 {
		b.WriteString(fmt.Sprintf("，共 %d 页", a.PageCount))
	}
	if a.FileSize > 0 {
		b.WriteString("，" + formatSize(a.FileSize))
	}
	b.WriteString("]")
	b.WriteString(fmt.Sprintf("（附件 ID：%d）", a.ID))

	sandboxPath := a.SandboxPath()

	if sandboxPath != "" {
		b.WriteString("\n位置：" + sandboxPath + "（只读）")
		// 两条路各说一次，都是条件式的：
		//   读 → Read（二进制文档会自动给出解析好的文本，模型不需要知道是怎么来的）
		//   改 → 沙箱（不点名库和 skill —— 见 TestInject_NeverNamesConfigurableThings）
		//
		// "不要整篇重新生成"留着：它修的是一个真实的失败（照抽出来的文本重做一份，
		// 原有格式全丢，还报告"已完成"），而且对所有可改的文件类型都成立。
		b.WriteString("\n需要内容时用 Read 读这个路径。" +
			"如需修改：在沙箱里读原文件改写，产物写到 /workspace/ 下" +
			"（在原文件基础上改，保留未涉及的内容和原有格式，不要整篇重新生成）。")
		return b.String()
	}

	// ── 尚未物化 ────────────────────────────────────────────────────
	//
	// 只说清"这个文件用不了"，**不回落到注入正文**。
	//
	// 曾经写过那个回落（"文本在库里但 Read 够不到，所以拼进消息"），但那是拿一个 bug
	// 当场景：真链路验收里所有附件都没物化，根因是 blob 上传缺服务间鉴权头被 401 挡住
	// —— 修的应该是那个 401，不是为它造一条降级路径。
	//
	// 物化不该失败，它是上传的一部分（router/attachment.go 第 3 步）。真失败了要么是
	// 配置问题（沙箱/共享存储没配），要么是故障（NFS 满、OBS 不可达）—— 两种都该在
	// 上传时就让用户知道，而不是让每个下游都带一条"万一读不到"的分支。
	//
	// 但这一句必须说：不说的话模型会去 Read 一个不存在的路径，然后在一个它无法理解的
	// 错误上打转（文件不存在？路径错了？权限？）。
	b.WriteString("\n需要了解内容时用 Read，并传 path=attachment:" + fmt.Sprint(a.ID) +
		"；需要修改或运行代码时，再将它放入沙箱工作区。")
	return b.String()
}
