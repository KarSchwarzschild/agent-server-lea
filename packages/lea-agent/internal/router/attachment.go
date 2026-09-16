package router

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/runtime"
)

const attachmentMaxBytes int64 = 30 * 1024 * 1024

// parserExts 与 lea-parser dispatcher SUPPORTED_FORMATS 保持一致（同步维护）。
//
// ⚠️ 这张表现在只决定**要不要打 parser 抽文本**，不再决定"能不能上传"。
//
// 原来不在表里就直接 415 拒绝，而那是把两件事混在了一起：抽不出文本的文件（.zip /
// .sqlite / .ipynb）照样可以落地让模型在沙箱里自己处理 —— 这正是 Anthropic 让
// .docx/.xlsx 走 container_upload 而不是 document 的思路：**解析推迟到消费点**。
// 一个 415 就这样变成一个能干的活。
var parserExts = map[string]string{
	// 文档 → text
	"pdf": "text", "docx": "text", "doc": "text", "pptx": "text", "ppt": "text",
	"md": "text", "txt": "text", "csv": "text", "json": "text",
	// 图片 → image（parser 只返 base64，md_content 为空；注入时按 image 分支说明）
	"png": "image", "jpg": "image", "jpeg": "image", "webp": "image",
	"gif": "image", "bmp": "image",
	// 音频 / 视频 → 分类保留，本 sprint 仍按 text 处理拼进 message
	"mp3": "audio", "wav": "audio", "m4a": "audio", "flac": "audio", "ogg": "audio",
	"mp4": "video", "avi": "video", "mkv": "video", "mov": "video",
	"wmv": "video", "flv": "video", "webm": "video",
}

// toolsBaseURL 是 lea-tools 的地址。落 blob 和物化都打它。
func toolsBaseURL() string {
	if v := strings.TrimSpace(os.Getenv("LEA_TOOLS_URL")); v != "" {
		return strings.TrimRight(v, "/")
	}
	// 默认端口是 8400 —— 跟 runtime/gateway.go、router/files.go、router/proxy.go 里
	// 那三处默认值必须一致。这里曾经写着 8200，于是不配 LEA_TOOLS_URL 时（本地开发、
	// 单机起服务）上传的三步全打在一个没人监听的端口上，而症状跟"lea-tools 没起来"
	// 一模一样，看不出是这一行写错了。
	return "http://localhost:8400"
}

// newToolsClient 造一个打 lea-tools 的 HTTP client，**出厂就带服务间鉴权头**。
//
// ## 为什么必须是工厂，不是"记得给每个 client 加上"
//
// runtime/internal_token.go 的 init 只换掉**那个包里**几个共享 client 的 Transport。
// 住在 router 包的漏在外面 —— 而它的注释恰好预言了这种漏法："漏掉的那个只在开启
// 鉴权之后才炸，而且炸的是某一条具体功能，排查时很难联想到鉴权"。
//
// 真链路验收里就是这么炸的：
//
//	upload_blob_failed ... status=401 {"error":"invalid or missing internal token"}
//
// 而 blob 失败**不阻断上传**（三步各自降级），于是表现成一串看起来无关的症状：
// 上传返回 200 但 editable=false、staged_path 一直是空、图片直接 502
// （没文本 + 没落地 = "三步全废"）。三个症状都指向"沙箱没开"，没有一个指向鉴权。
//
// 修完那次之后 files.go 里的两个 client 仍然是裸的（它们在 handler 工厂里现造，
// 不在 init 扫到的那张表里）—— 同一个 bug 又躺了一轮。所以现在没有"那张表"了：
// 打 lea-tools 只有这一个造 client 的入口，新调用点自动带上头。
func newToolsClient(timeout time.Duration) *http.Client {
	c := &http.Client{Timeout: timeout}
	// 没配就当这套机制不存在，跟被调方对称 —— 滚动更新期间两边版本共存不会断链。
	if token := strings.TrimSpace(os.Getenv("LEA_INTERNAL_TOKEN")); token != "" {
		c.Transport = &internalTokenTransport{token: token}
	}
	return c
}

// blobClient 的超时要够 30MB 文件写进 OBS。跟 parser 那个 120s 分开：这条路没有
// 模型推理，纯 IO，但 OBS 在跨机房时可能很慢。
var blobClient = newToolsClient(180 * time.Second)

// stageClient 物化是 OBS→NFS，lea-tools 侧自己拉，不经过我们。给宽松些。
var stageClient = newToolsClient(120 * time.Second)

// internalTokenTransport 给出站请求带上 X-Internal-Token。
//
// 跟 runtime 包那个同名类型是一份重复实现。没有提到公共包里，是因为把它导出会让
// "谁该带这个头"变成一个可选项 —— 而正确答案是"所有发往内网服务的请求，无例外"。
// 两个包各自在 init 里兜住自己的 client，漏一个的代价（见上）比重复十行代码大得多。
type internalTokenTransport struct {
	token string
	base  http.RoundTripper
}

func (t *internalTokenTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	base := t.base
	if base == nil {
		base = http.DefaultTransport
	}
	if t.token == "" {
		return base.RoundTrip(req)
	}
	// RoundTripper 的契约要求不得修改传入的 Request（调用方可能重试或记日志），
	// 所以浅拷一份再改 Header。
	clone := req.Clone(req.Context())
	clone.Header.Set(runtime.InternalTokenHeader, t.token)
	return base.RoundTrip(clone)
}

// handleUploadAttachment 接收 multipart file，走完整的三步：
//
//  1. 落 blob   → OBS uploads/<tenant>/<sha>.<ext>   长期真源
//  2. 抽文本    → lea-parser                          Read 工具从库里取它
//  3. 物化      → NFS sessions/<sid>/uploads/         RO 挂进沙箱，模型能读能处理
//
// ## 哪一步失败算数，哪一步不算
//
//	blob 失败    → 502。它是第 3 步的输入（物化从 OBS 拉字节），塌了后面都塌
//	抽文本失败    → 继续。文件还在，模型能在沙箱里自己处理；Read 会说清没有文本
//	物化失败      → 502。附件对模型完全不可用，见下
//
// 物化以前是"失败也继续"，因为那时抽出来的文本会被注入进消息 —— 有文本就还能问答。
// 现在文本只能通过 Read 从 /mnt/uploads/<staged_path> 取，物化失败意味着**文本在库里
// 但没有任何路径能读到它**。这时回 200 就是骗人：用户看到"上传成功"，问问题时模型说
// 文件用不了，两个界面都不解释为什么。
//
// 这跟 lea-config 对失效挂载项静默丢弃不是一回事：那边丢的是一个附加能力（少个工具，
// 对话照常），这边丢的是**用户刚刚交给我们的东西**。
func handleUploadAttachment() gin.HandlerFunc {
	return func(c *gin.Context) {
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		fh, err := c.FormFile("file")
		if err != nil {
			c.JSON(400, gin.H{"error": "missing file field"})
			return
		}
		if fh.Size > attachmentMaxBytes {
			c.JSON(413, gin.H{"error": "file too large (>30MB)"})
			return
		}

		filename := fh.Filename
		ext := ""
		if i := strings.LastIndex(filename, "."); i >= 0 {
			ext = strings.ToLower(filename[i+1:])
		}
		// 表里没有 = 抽不了文本，不是"不能上传"。mediaType 兜底成 "file"：它进库、
		// 进注入文本的分支判断，不能是空串。
		mediaType, canParse := parserExts[ext]
		if !canParse {
			mediaType = "file"
		}

		src, err := fh.Open()
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		defer src.Close()
		buf, err := io.ReadAll(io.LimitReader(src, attachmentMaxBytes+1))
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		if int64(len(buf)) > attachmentMaxBytes {
			c.JSON(413, gin.H{"error": "file too large (>30MB)"})
			return
		}

		sum := sha256.Sum256(buf)
		shaHex := hex.EncodeToString(sum[:])
		sessionID := c.PostForm("session_id")

		// ── 0. 归属 + 配额，两道都在动字节之前 ────────────────────
		//
		// 顺序有意义：这两道判完才去写 OBS。反过来（先落 blob 再判配额）会在超限时
		// 留下一个没有任何行指向的对象 —— 既不在用户的文件库里，也不会被引用计数
		// 算到，纯粹占空间，而且下次同一个人重试还会再造一个。
		//
		// db 取不到时两道都放行：附件三步各自降级，走到落库那步自然会失败并报错。
		// 在这里因为"查不了库"就拒绝上传，是拿一个不相关的故障挡掉主路径。
		attDB, _ := persistence.DB()
		if !sessionOwnedIfExists(c, attDB, sessionID) {
			// 404 而不是 403：跟 sessionOwnerGuard 同一条判据 —— 说"没有这个会话"
			// 才不泄露它存在。
			c.JSON(404, gin.H{"error": "session not found"})
			return
		}
		if blocked, msg := uploadQuotaBlocked(
			c.Request.Context(), attDB, tenantID, userID, shaHex, int64(len(buf)),
		); blocked {
			// 507 Insufficient Storage —— 前端据此弹"去清理文件"，而不是当成一般错误
			// 让用户重试（重试永远失败）。
			c.JSON(507, gin.H{"error": msg, "code": "storage_quota_exceeded"})
			return
		}

		// ── 1. 落 blob ────────────────────────────────────────────
		//
		// 先落地再抽文本：抽文本可能要 120 秒（MinerU / VLM），而在那之前先把字节
		// 保住，进程在解析中途挂掉时文件不会丢。
		blob, blobErr := putUploadBlob(c, tenantID, shaHex, filename, buf)
		if blobErr != nil {
			// 继续，但这一行会以 file_key='' 落库 —— 后果是它在用户文件库里
			// stored=false：不能下载、也不能导入到别的会话。不是"只走文本路径"
			// 那么轻，原件是真的没保住。
			log.Printf("upload_blob_failed tenant=%s file=%s sha=%s err=%v (继续，但原件未保存)",
				tenantID, filename, shaHex[:12], blobErr)
		}

		// ── 2. 抽文本 ─────────────────────────────────────────────
		var mdContent string
		var pageCount int
		if canParse {
			parsed, pErr := parseViaParser(c, buf, filename)
			if pErr != nil {
				// 不再 502 —— 文件已经落了 blob，沙箱里模型可以自己解析。
				// 但这条日志必须有：用户会说"它没看懂我的文件"，而那时要能查到解析失败。
				log.Printf("parser_call_failed filename=%s err=%v (继续，模型可在沙箱内自行解析)",
					filename, pErr)
			} else {
				mdContent = parsed.MdContent
				pageCount = parsed.PageCount
			}
		}

		// 共享存储是编辑和代码执行的工作副本，按需物化；上传阶段不占用它。
		stagedPath := ""

		// ── 用不了就说用不了，别回 200 ─────────────────────────────
		//
		// 判据是 stagedPath —— **不是** mdContent。
		//
		// 这一条以前是 `mdContent == "" && stagedPath == ""`（两个都废才算失败），
		// 因为那时抽出来的文本会被注入进消息，所以"有文本"本身就是一种可用状态。
		// 现在文本只能通过 Read 从 /mnt/uploads/<staged_path> 取，物化失败就意味着
		// **文本在库里但没有任何路径能读到它** —— 附件对模型完全不可用，哪怕解析
		// 得再完整。
		//
		// 所以这里必须报错。回 200 的后果是：用户看到"上传成功"，问问题时模型说
		// 文件用不了，而两个界面都不解释为什么 —— 真链路验收里就是这个形状
		// （blob 缺鉴权头被 401 挡住，于是全部附件静静地不可用）。
		//
		// 有 session_id 却没物化成，一定是故障或配置缺失，两种都该让用户知道。
		// 没带 session_id：文件落了 blob 但没有会话可挂。这不是错误（先传后开会话
		// 是允许的），但它对当前这轮对话不可用 —— 前端靠 editable/sandbox_path 判断。
		if sessionID == "" && blob == nil {
			c.JSON(502, gin.H{"error": "文件未能保存，请稍后重试"})
			return
		}

		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		fileKey := ""
		if blob != nil {
			fileKey = blob.FileKey
		}
		att := persistence.Attachment{
			TenantID:      tenantID,
			UserID:        userID,
			SessionID:     sessionID,
			Filename:      filename,
			FileKey:       fileKey,
			FileType:      ext,
			FileSize:      int64(len(buf)),
			MediaType:     mediaType,
			ExtractedText: mdContent,
			SHA256:        shaHex,
			StagedPath:    stagedPath,
			PageCount:     pageCount,
		}
		id, err := persistence.InsertAttachment(c.Request.Context(), db, att)
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}

		preview := mdContent
		if len(preview) > 200 {
			preview = preview[:200]
		}
		att.ID = id
		c.JSON(200, gin.H{
			"id":           id,
			"filename":     filename,
			"file_type":    ext,
			"file_size":    len(buf),
			"media_type":   mediaType,
			"page_count":   pageCount,
			"text_preview": preview,
			// 前端据此显示能力提示："这个文件可以改" vs "只能问"。
			// 用户在传完之后就该知道，而不是问了才发现改不了。
			"editable":       false,
			"downloadable":   fileKey != "",
			"sandbox_path":   att.SandboxPath(),
			"stage_required": true,
		})
	}
}

// uploadBlobResult 是 lea-tools /uploads/blob 的返回。
type uploadBlobResult struct {
	FileKey    string `json:"file_key"`
	SHA256     string `json:"sha256"`
	Size       int64  `json:"size"`
	StagedName string `json:"staged_name"`
}

// putUploadBlob 把原件字节交给 lea-tools 写进 OBS。
//
// sha 我们自己算好传过去让它校验：这一段字节要经过 lea-agent 内存、HTTP、OBS 三跳，
// 任何一处截断都会让 key（内容寻址的）和内容不符 —— 而那意味着以后按 sha 命中解析
// 缓存会拿到**别的文件**的结果。校验一次很便宜。
func putUploadBlob(
	c *gin.Context, tenantID, shaHex, filename string, data []byte,
) (*uploadBlobResult, error) {
	body := &bytes.Buffer{}
	w := multipart.NewWriter(body)
	part, err := w.CreateFormFile("file", filename)
	if err != nil {
		return nil, err
	}
	if _, err := part.Write(data); err != nil {
		return nil, err
	}
	if err := w.WriteField("tenant_id", tenantID); err != nil {
		return nil, err
	}
	if err := w.WriteField("sha256", shaHex); err != nil {
		return nil, err
	}
	if err := w.Close(); err != nil {
		return nil, err
	}

	req, err := http.NewRequestWithContext(c.Request.Context(), "POST",
		toolsBaseURL()+"/api/files/uploads/blob", body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", w.FormDataContentType())

	resp, err := blobClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return nil, fmt.Errorf("blob upload status=%d body=%s", resp.StatusCode, string(b))
	}
	var out uploadBlobResult
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	if out.FileKey == "" {
		return nil, fmt.Errorf("blob upload returned empty file_key")
	}
	return &out, nil
}

// stageUpload 让 lea-tools 把 OBS 上的对象物化到会话 uploads/ 目录。
//
// **只传 key，不传字节** —— lea-tools 自己从 OBS 拉。30MB 的文件不该在两个服务之间
// 走第二遍，也不需要 base64（复用 /files/write 的话就得，那个端点只收 str）。
//
// dest_name 用 blob 那步返回的 staged_name，不在这边重新算：清洗规则住在 lea-tools
// 的 safe_stage_name，两边各算一次会在边缘情况（超长中文名截断）下不一致，而症状是
// 模型拿到一个不存在的路径。
func stageUpload(c *gin.Context, tenantID, sessionID, fileKey, destName string) (string, error) {
	payload, err := json.Marshal(map[string]string{
		"tenant_id":  tenantID,
		"session_id": sessionID,
		"file_key":   fileKey,
		"dest_name":  destName,
	})
	if err != nil {
		return "", err
	}
	req, err := http.NewRequestWithContext(c.Request.Context(), "POST",
		toolsBaseURL()+"/api/files/uploads/stage", bytes.NewReader(payload))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := stageClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return "", fmt.Errorf("stage status=%d body=%s", resp.StatusCode, string(b))
	}
	var out struct {
		StagedName string `json:"staged_name"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return "", err
	}
	if out.StagedName == "" {
		return "", fmt.Errorf("stage returned empty staged_name")
	}
	return out.StagedName, nil
}

// handleDownloadAttachment 把用户上传的原件回给他。
//
// ## 为什么单独一个端点，不塞进 /files/download
//
// 那个是按 (session_id, path) 找**产物**的，而上传件的权威位置是 OBS 上的 blob，
// 不是会话目录：会话归档后 NFS 那份已经清理掉了，blob 还在。走 attachment_id 才能
// 正确回落到 OBS。
//
// ## 不需要 Anthropic 那种 downloadable:false
//
// 它们的文件作用域是 workspace 级、任何 API key 都能读同 workspace 的文件，所以必须
// 靠"只有生成的产物可下载"来防止 API 被当成任意文件中转站。我们这里按
// (tenant, user, id) 查库，**用户下载自己上传的文件本来就是正当的**。
func handleDownloadAttachment() gin.HandlerFunc {
	return func(c *gin.Context) {
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		var id int64
		if _, err := fmt.Sscanf(c.Param("id"), "%d", &id); err != nil || id <= 0 {
			c.JSON(400, gin.H{"error": "bad attachment id"})
			return
		}

		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		att, err := persistence.GetAttachmentForDownload(
			c.Request.Context(), db, tenantID, userID, id)
		if err != nil {
			// 不区分"不存在"和"不是你的"——附件 id 是自增的，区分开就是一个可枚举的
			// 存在性预言。
			c.JSON(404, gin.H{"error": "attachment not found"})
			return
		}
		if att.FileKey == "" {
			// 这一行是 blob 层上线**之前**传的，或者当时 OBS 不可达。文本还在（问答
			// 照常），但原件从来没保存过 —— 说清楚，别让用户以为是临时故障。
			c.JSON(410, gin.H{"error": "该附件上传时未保存原件，无法下载"})
			return
		}

		u := toolsBaseURL() + "/api/files/uploads/blob?file_key=" +
			url.QueryEscape(att.FileKey) + "&filename=" + url.QueryEscape(att.Filename)
		req, err := http.NewRequestWithContext(c.Request.Context(), "GET", u, nil)
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		resp, err := blobClient.Do(req)
		if err != nil {
			c.JSON(502, gin.H{"error": "storage unreachable: " + err.Error()})
			return
		}
		defer resp.Body.Close()
		if resp.StatusCode >= 400 {
			b, _ := io.ReadAll(io.LimitReader(resp.Body, 256))
			c.JSON(resp.StatusCode, gin.H{"error": strings.TrimSpace(string(b))})
			return
		}

		for _, h := range []string{"Content-Type", "Content-Length", "Content-Disposition"} {
			if v := resp.Header.Get(h); v != "" {
				c.Writer.Header().Set(h, v)
			}
		}
		c.Status(resp.StatusCode)
		if _, err := io.Copy(c.Writer, resp.Body); err != nil {
			log.Printf("attachment_download_copy_failed id=%d err=%v", id, err)
		}
	}
}

// parserResult 是 lea-parser /parse 的返回里我们要的部分。
type parserResult struct {
	MdContent string `json:"md_content"`
	// PageCount 是分页格式的总页数，其余为 0。注入文本里报"共 N 页"，也用来区分
	// "文件是空的"和"有 N 页但一个字都没抽出来"（扫描件降级到 pymupdf 兜底时）。
	PageCount int `json:"page_count"`
}

// parseViaParser POST multipart 到 lea-parser/parse?chunk=false 拿 md_content。
// 透传 Authorization header（lea-parser 用同一份 JWT SECRET）—— 它那边的配额闸
// （check_rate_limit / check_file_size）按 JWT 里的身份计数，所以这个头不能省。
func parseViaParser(c *gin.Context, fileBytes []byte, filename string) (*parserResult, error) {
	baseURL := os.Getenv("LEA_PARSER_URL")
	if baseURL == "" {
		baseURL = "http://localhost:8600"
	}
	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)
	part, err := writer.CreateFormFile("file", filename)
	if err != nil {
		return nil, err
	}
	if _, err := part.Write(fileBytes); err != nil {
		return nil, err
	}
	if err := writer.Close(); err != nil {
		return nil, err
	}

	req, err := http.NewRequestWithContext(c.Request.Context(), "POST",
		baseURL+"/parse?chunk=false", body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())
	if auth := c.GetHeader("Authorization"); auth != "" {
		req.Header.Set("Authorization", auth)
	}

	client := &http.Client{Timeout: 120 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return nil, fmt.Errorf("parser status=%d body=%s", resp.StatusCode, string(b))
	}
	var out parserResult
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	return &out, nil
}
