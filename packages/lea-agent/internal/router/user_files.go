package router

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/runtime"
)

// 用户文件库的四个端点。
//
// ## 一个库，两张表
//
// 用户看到的是**一个**文件列表；底下是两张表，方向相反：
//
//	chat_attachments    用户 → 我们（他上传的原件）
//	session_artifacts   我们 → 用户（模型显式 deliver_artifact 的成品）
//
// 合起来展示是因为用户不关心这个区别 —— 他要的是"我的文件"。分开存是因为**降级
// 语义相反**：上传超配额要硬拒（他还拿着原件，删几个重传很便宜），交付超配额要
// 软放行（那背后是几分钟的模型劳动，丢掉换不来任何东西）。
//
// 所以 kind 这个字段必须留在 API 上：删除和导入都要知道去哪张表，而且前端也需要它
// —— "这是我传的"和"这是它给我的"在用户心里是两类东西，混成一个列表已经够了，
// 再抹掉标签就找不着了。
//
// ## 为什么挂在 /user 而不是 /sessions/:id 下面
//
// 这个库的意义就是**跨会话**：在 A 对话里生成的报告，能在 B 对话里接着用。挂在会话
// 下面等于把它重新关回会话里。

// handleUserStorage 报这个用户占了多少、上限多少。
//
// 上传超限时前端要能解释"为什么传不上去"，而 507 那条错误信息里只有数字 —— 用户
// 想知道的是"我该删哪个"。这个端点 + 文件列表一起才回答得了。
//
// upload/artifact 分开报，因为用户能分别处理它们：上传件是他自己传的（删了心里有数），
// 成品是模型生成的（删了可能还想再要一份）。一个合计数字看不出该动哪边。
func handleUserStorage() gin.HandlerFunc {
	return func(c *gin.Context) {
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		quota := runtime.UserStorageQuotaBytes()
		u, err := persistence.UserStorageUsage(c.Request.Context(), db,
			c.GetString("tenant_id"), c.GetString("user_id"), quota)
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		out := gin.H{
			"upload_bytes":   u.UploadBytes,
			"artifact_bytes": u.ArtifactBytes,
			"total_bytes":    u.TotalBytes,
			"quota_bytes":    u.QuotaBytes,
			"upload_count":   u.UploadCount,
			"artifact_count": u.ArtifactCount,
			// quota_bytes=0 时 percent 没有意义（不限）。给 0 而不是除零或者 null，
			// 前端据 quota_bytes==0 决定要不要画那根进度条。
			"percent": 0.0,
		}
		if quota > 0 {
			out["percent"] = float64(u.TotalBytes) / float64(quota) * 100
		}
		c.JSON(200, out)
	}
}

type publishWorkspaceRequest struct {
	Path        string `json:"path"`
	Title       string `json:"title"`
	Description string `json:"description"`
}

func handlePublishWorkspaceFile() gin.HandlerFunc {
	client := newToolsClient(180 * time.Second)
	return func(c *gin.Context) {
		sessionID := c.Param("id")
		tenantID, ok := assertSessionOwner(c, sessionID, missing404)
		if !ok {
			return
		}

		var body publishWorkspaceRequest
		if err := c.ShouldBindJSON(&body); err != nil {
			c.JSON(400, gin.H{"error": "bad body: " + err.Error()})
			return
		}
		body.Path = strings.TrimPrefix(strings.TrimSpace(body.Path), "./")
		if body.Path == "" {
			c.JSON(400, gin.H{"error": "path required"})
			return
		}

		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}

		payload, _ := json.Marshal(gin.H{
			"tenant_id":  tenantID,
			"session_id": sessionID,
			"path":       body.Path,
		})
		req, err := http.NewRequestWithContext(c.Request.Context(), http.MethodPost,
			toolsBaseURL()+"/api/files/artifacts/publish", bytes.NewReader(payload))
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		req.Header.Set("Content-Type", "application/json")
		resp, err := client.Do(req)
		if err != nil {
			c.JSON(502, gin.H{"error": "workspace publish unreachable"})
			return
		}
		defer resp.Body.Close()
		raw, readErr := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
		if readErr != nil {
			c.JSON(502, gin.H{"error": "workspace publish response unreadable"})
			return
		}
		if resp.StatusCode >= 400 {
			var upstream struct {
				Detail string `json:"detail"`
			}
			_ = json.Unmarshal(raw, &upstream)
			detail := strings.TrimSpace(upstream.Detail)
			if detail == "" {
				detail = strings.TrimSpace(string(raw))
			}
			if detail == "" {
				detail = "workspace publish failed"
			}
			c.JSON(resp.StatusCode, gin.H{"error": detail})
			return
		}

		var published struct {
			OBSKey   string `json:"obs_key"`
			SHA256   string `json:"sha256"`
			Size     int64  `json:"size"`
			Filename string `json:"filename"`
			MimeType string `json:"mime_type"`
		}
		if err := json.Unmarshal(raw, &published); err != nil || published.OBSKey == "" || published.SHA256 == "" {
			c.JSON(502, gin.H{"error": "workspace publish returned invalid response"})
			return
		}

		if blocked, message := uploadQuotaBlocked(c.Request.Context(), db, tenantID,
			c.GetString("user_id"), published.SHA256, published.Size); blocked {
			c.JSON(507, gin.H{"error": message})
			return
		}

		title := strings.TrimSpace(body.Title)
		if title == "" {
			title = published.Filename
		}
		artifact := persistence.Artifact{
			TenantID: tenantID, UserID: c.GetString("user_id"), SessionID: sessionID,
			RelPath: body.Path, Filename: published.Filename, Title: title,
			Description: strings.TrimSpace(body.Description), OBSKey: published.OBSKey,
			SHA256: published.SHA256, Size: published.Size, MimeType: published.MimeType,
		}
		id, err := persistence.UpsertArtifact(c.Request.Context(), db, artifact)
		if err != nil {
			c.JSON(500, gin.H{"error": "file library registration failed: " + err.Error()})
			return
		}
		c.JSON(200, gin.H{
			"status": "ok", "id": id, "filename": artifact.Filename,
			"title": artifact.Title, "size": artifact.Size,
		})
	}
}

// handleListUserFiles 合并列出用户的上传件和成品。
//
// 两张表各自分页再在内存里合并 —— 不是 UNION 一条 SQL。理由是两张表的列不一样
// （成品有 title/description，上传件有 media_type/staged_path），UNION 要么补一堆
// NULL 列、要么在 SQL 里编码展示逻辑。数量级也不需要：单用户的文件是几十到几百，
// limit 上限 200。
//
// 排序按 created_at 倒序，两边混在一起 —— 用户找文件时想的是"我上周弄的那个"，
// 不是"那是上传的还是生成的"。
func handleListUserFiles() gin.HandlerFunc {
	return func(c *gin.Context) {
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")
		limit, _ := strconv.Atoi(c.DefaultQuery("limit", "50"))
		offset, _ := strconv.Atoi(c.DefaultQuery("offset", "0"))
		if offset < 0 {
			offset = 0
		}
		kind := c.Query("kind") // 空 = 两种都要

		ctx := c.Request.Context()
		files := make([]gin.H, 0, limit*2)

		if kind == "" || kind == "upload" {
			ups, err := persistence.ListUserUploads(ctx, db, tenantID, userID, limit, offset)
			if err != nil {
				c.JSON(500, gin.H{"error": err.Error()})
				return
			}
			for _, a := range ups {
				files = append(files, gin.H{
					"kind":       "upload",
					"id":         a.ID,
					"filename":   a.Filename,
					"size":       a.FileSize,
					"file_type":  a.FileType,
					"media_type": a.MediaType,
					"session_id": a.SessionID,
					"created_at": a.CreatedAt,
					// file_key 为空 = 当时 OBS 不可达，原件没保住。前端据此把下载
					// 按钮置灰，而不是让用户点了才拿到 410。
					"stored":     a.FileKey != "",
					"can_import": a.FileKey != "",
				})
			}
		}

		if kind == "" || kind == "artifact" {
			arts, err := persistence.ListUserArtifacts(ctx, db, tenantID, userID, limit, offset)
			if err != nil {
				c.JSON(500, gin.H{"error": err.Error()})
				return
			}
			for _, a := range arts {
				files = append(files, gin.H{
					"kind":        "artifact",
					"id":          a.ID,
					"filename":    a.Filename,
					"title":       a.Title,
					"description": a.Description,
					"size":        a.Size,
					"media_type":  a.MimeType,
					"session_id":  a.SessionID,
					"created_at":  a.CreatedAt,
					// obs_key 为空 = 登记了但没存进库（配额满或 OBS 故障）。那份
					// 文件还在会话工作区里（30 天内），但跨会话用不了 —— 所以
					// stored=false 的成品**不能导入**，前端也该标出来。
					"stored":     a.OBSKey != "",
					"can_import": a.OBSKey != "",
				})
			}
		}

		// 两段各自有序，合起来要重排。created_at 是 MySQL DATETIME 的字符串形式
		// （"2026-09-09 14:03:11"），定长且字典序 == 时间序，所以直接比字符串。
		sortByCreatedDesc(files)
		c.JSON(200, gin.H{"files": files, "limit": limit, "offset": offset})
	}
}

// sortByCreatedDesc 按 created_at 倒序。插入排序 —— n 最多几百，且两段各自已经
// 有序，这种输入下它接近线性。
func sortByCreatedDesc(files []gin.H) {
	for i := 1; i < len(files); i++ {
		cur := files[i]
		j := i - 1
		for j >= 0 && asString(files[j]["created_at"]) < asString(cur["created_at"]) {
			files[j+1] = files[j]
			j--
		}
		files[j+1] = cur
	}
}

func asString(v any) string {
	s, _ := v.(string)
	return s
}

// handleDeleteUserFile 删掉文件库里的一个文件：登记行 + OBS 对象。
//
// ## 顺序是：先删行，再数引用，最后删对象
//
// 这三步的顺序不能换，每一种换法都坏：
//
//	先删对象再删行   中间挂掉 → 库里留一行指向一个不存在的对象，用户点下载拿到 410，
//	                 而他并没有删过它
//	先数引用再删行   自己那一行还在，count 永远 ≥1，于是对象永远删不掉 —— 表现是
//	                 "删了但空间没降"，而这正是配额存在时最不能接受的形状
//
// 现在这个顺序的失败模式是"行删了、对象还在"：用户看到文件没了、空间也降了
// （配额数的是行），只有 bucket 里多一个孤儿对象。那是三种里唯一不影响用户的。
//
// ## 引用计数按租户数，不按用户数
//
// 同一份课件被 40 个学生传过，库里 40 行、OBS 上一个对象（内容寻址）。甲删掉自己
// 那一行就删对象的话，另外 39 个人的文件同时消失 —— 而他们什么都没做，日志里也
// 没有任何针对他们的操作。去重边界就是租户，所以计数边界也必须是租户。
func handleDeleteUserFile() gin.HandlerFunc {
	return func(c *gin.Context) {
		kind := c.Param("kind")
		if kind != "upload" && kind != "artifact" {
			c.JSON(400, gin.H{"error": "kind 只能是 upload 或 artifact"})
			return
		}
		id, err := strconv.ParseInt(c.Param("id"), 10, 64)
		if err != nil || id <= 0 {
			c.JSON(400, gin.H{"error": "bad id"})
			return
		}
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		ctx := c.Request.Context()
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		// ── 1. 取出这一行，拿到 key 和 sha ──────────────────────
		//
		// 删之前必须先读：删完就没有 key 了，而删对象要用它。
		// 查询里带 tenant+user 过滤，不存在和不是你的都返回同一个 404 —— id 是
		// 自增的，分开报就是一个可枚举的存在性预言。
		var objKey, sha string
		var filename string
		if kind == "upload" {
			att, err := persistence.GetAttachmentForDownload(ctx, db, tenantID, userID, id)
			if err != nil {
				c.JSON(404, gin.H{"error": "file not found"})
				return
			}
			objKey, sha, filename = att.FileKey, att.SHA256, att.Filename
		} else {
			a, err := persistence.GetArtifactForUser(ctx, db, tenantID, userID, id)
			if err != nil {
				c.JSON(404, gin.H{"error": "file not found"})
				return
			}
			objKey, sha, filename = a.OBSKey, a.SHA256, a.Filename
		}

		// ── 2. 删登记行 ─────────────────────────────────────────
		var deleted bool
		if kind == "upload" {
			deleted, err = persistence.DeleteUpload(ctx, db, tenantID, userID, id)
		} else {
			deleted, err = persistence.DeleteArtifact(ctx, db, tenantID, userID, id)
		}
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		if !deleted {
			// 上一步刚读到、这一步没删掉 —— 并发下另一个请求先删了。这不是错误，
			// 用户要的结果已经成立。
			c.JSON(200, gin.H{"status": "ok", "deleted": false, "filename": filename})
			return
		}

		// ── 3. 数引用，没别人指着才真删对象 ──────────────────────
		objectDeleted := false
		if objKey != "" && sha != "" {
			n, err := persistence.CountSHARefs(ctx, db, tenantID, sha)
			switch {
			case err != nil:
				// 数不出来就**不删**。删错了是不可逆的（别人的文件凭空消失），
				// 不删的代价只是一个孤儿对象继续占空间 —— 两边不对称，选可逆的那个。
				log.Printf("user_file_delete_refcount_failed kind=%s id=%d sha=%s err=%v (对象保留)",
					kind, id, sha[:min(12, len(sha))], err)
			case n > 0:
				// 同租户内还有别的行指向同一份内容。对象留着，那些行还要用。
				log.Printf("user_file_delete_shared kind=%s id=%d refs=%d (对象保留)", kind, id, n)
			default:
				if err := deleteBlobObject(c, objKey); err != nil {
					// 行已经删了，报错也回不去。记日志让对账能捡到，但不要 500 ——
					// 对用户来说文件确实没了，一个红色报错只会让他以为没删掉、
					// 然后再点一次（第二次会 404）。
					log.Printf("user_file_blob_delete_failed kind=%s id=%d key=%s err=%v (孤儿对象)",
						kind, id, objKey, err)
				} else {
					objectDeleted = true
				}
			}
		}

		c.JSON(200, gin.H{
			"status":         "ok",
			"deleted":        true,
			"filename":       filename,
			"object_deleted": objectDeleted,
		})
	}
}

// deleteBlobObject 让 lea-tools 删掉 OBS 上的对象。
func deleteBlobObject(c *gin.Context, fileKey string) error {
	u := toolsBaseURL() + "/api/files/blob?file_key=" + url.QueryEscape(fileKey)
	req, err := http.NewRequestWithContext(c.Request.Context(), http.MethodDelete, u, nil)
	if err != nil {
		return err
	}
	resp, err := blobClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 256))
		return fmt.Errorf("status=%d body=%s", resp.StatusCode, strings.TrimSpace(string(b)))
	}
	return nil
}

// uploadQuotaBlocked 判一次上传该不该被硬拒。返回 (拒不拒, 给用户的说明)。
//
// ## 跟 deliver_artifact 刻意相反：这里是硬拒
//
// 上传超配额时用户**还拿着原件**，删几个旧文件再传一遍成本很低。而交付超配额是软放行，
// 因为那份产物背后是几分钟的模型劳动，丢掉换不来任何东西（见 artifact_tools.go 的
// overQuota）。同一条配额线，两侧的降级方向不同，因为两侧丢掉的东西价值不同。
//
// 两处读的是同一对 UserStorageQuotaBytes / TenantStorageQuotaBytes —— 各读各的环境
// 变量的话，会出现"交付说满了、上传说没满"这种自相矛盾的提示。
//
// ## 同 sha 直接放行
//
// 内容寻址：这份内容库里已经有了就不占新对象。按 size 判会把一个零成本的操作挡掉，
// 而用户删什么都缓解不了 —— 那些字节本来就是复用的。40 个学生传同一份课件，
// 第 2 到第 40 个都走这条。
//
// ## 判不出来的时候放行
//
// 数据库抖一下就让用户传不上文件，比短暂超一点配额糟。真正的硬边界是对象存储本身。
func uploadQuotaBlocked(
	ctx context.Context, db *sql.DB, tenantID, userID, sha string, size int64,
) (bool, string) {
	if db == nil || size <= 0 {
		return false, ""
	}
	if sha != "" {
		if n, err := persistence.CountSHARefs(ctx, db, tenantID, sha); err == nil && n > 0 {
			return false, ""
		}
	}

	if q := runtime.UserStorageQuotaBytes(); q > 0 {
		u, err := persistence.UserStorageUsage(ctx, db, tenantID, userID, q)
		if err == nil && u.TotalBytes+size > q {
			return true, fmt.Sprintf(
				"存储空间不足：已用 %dMB / 上限 %dMB，这个文件需要 %dMB。"+
					"请在文件管理里删掉一些不需要的文件后重试。",
				u.TotalBytes/1048576, q/1048576, (size+1048575)/1048576)
		}
	}

	if q := runtime.TenantStorageQuotaBytes(); q > 0 {
		used, err := persistence.TenantStorageUsage(ctx, db, tenantID)
		if err == nil && used+size > q {
			// 这条用户自己解决不了，所以话术不能是"请删掉一些文件" —— 他删光自己的
			// 也未必够，而且那是别人占的。指向管理员。
			return true, fmt.Sprintf(
				"所在机构的存储配额已满（已用 %dMB / 上限 %dMB），请联系管理员。",
				used/1048576, q/1048576)
		}
	}
	return false, ""
}

// sessionOwnedIfExists 校验 session_id 归调用方所有；会话还没落库时放行。
//
// ## 为什么"不存在也放行"是对的
//
// 会话行不是建会话时插的，是**第一条消息**由 TouchSession 插的（POST /sessions 只在
// 内存里建）。而用户最常见的动作恰恰是"新开对话 → 拖个文件进来 → 发第一条消息" ——
// 上传发生在行存在之前。硬要求行存在会把这条主路径整个挡死。
//
// 放行也不泄露什么：行不存在意味着没有任何人拥有这个 id，附件行落下去之后，
// TouchSession 会以**当前调用方**的身份建会话行。攻击者猜一个未使用的 uuid 只能
// 往自己将来的会话里塞自己的文件。
//
// 反过来，行存在时必须匹配 —— 少了这一段，猜一个正在用的 session_id 就能往别人的
// 对话里塞附件，而附件是会被注入进对方 prompt 的。
func sessionOwnedIfExists(c *gin.Context, db *sql.DB, sessionID string) bool {
	if db == nil || sessionID == "" {
		return true
	}
	ownerUser, ownerTenant, err := persistence.GetSessionOwner(c.Request.Context(), db, sessionID)
	if err != nil {
		// ErrNoRows = 还没落库，放行（见上）。其他错误也放行：数据库抖动不该让
		// 上传失败，而这条校验挡的是越权，不是数据完整性。
		return true
	}
	return ownerUser == c.GetString("user_id") && ownerTenant == c.GetString("tenant_id")
}

// handleImportToSession 把文件库里的一个文件物化进当前会话，让模型能读能改。
//
// ## 这是"跨对话用同一份文件"的整个实现
//
// 库里的文件住在 OBS（`uploads/<tenant>/<sha>.<ext>` 或 `library/<tenant>/<sha>.<ext>`），
// 沙箱看不到 OBS —— 它只看得到自己那份 NFS host-mount。所以"在 B 对话里用 A 对话
// 生成的报告"这件事，落到实处就是**把那个对象拷进 B 会话的 uploads/ 目录**。
//
// 走的是上传件已经在用的那条 /uploads/stage：两种 key 共享同一套内容寻址规则，
// 只差前缀，而那个端点的前缀白名单已经同时认 uploads/ 和 library/。不为导入新造
// 一条路 —— 那条路已经被真链路验收跑过了。
//
// ## 为什么导入是拷贝，不是引用
//
// 拷进去的那份是会话工作区里的普通文件，模型可以改它、可以基于它生成新东西，
// 而**改的是副本** —— 用户文件库里那份原件一个字节都不动。引用语义（比如软链）
// 会让模型在 B 对话里的一次 Edit 悄悄改掉 A 对话的产出，那是用户完全预料不到的。
//
// 代价是占会话配额，且同一个文件导进十个会话就是十份 NFS 副本。可以接受：会话工作区
// 30 天就被回收，而 OBS 那份是长期的。**OBS 上不多占一个字节** —— 新登记行带的是同一个
// sha，配额那条 SUM(DISTINCT sha256, size) 只算一次。
//
// ## 导入必须落一行 chat_attachments，否则模型看不见这个文件
//
// 光把字节拷进 uploads/ 是不够的。这个平台里"模型知道有个文件"只有一条路：
// chat_attachments 里有一行 → 前端把 id 放进下一条消息的 attachment_ids →
// LoadAttachments → BuildContentWithAttachments 注入那句"[文件: x]，位置：/mnt/uploads/x"。
//
// 没有这一行还会坏掉 Read：readUpload 用 (tenant, session, staged_path) 反查，查不到
// 就回落到普通文本读，而 pptx/pdf 是二进制 —— 模型拿到的是 415「不是 UTF-8 文本文件」，
// 一个跟真实情况毫无关系的错误。
//
// 所以导入 = 物化字节 + 在**目标会话**下补一行登记。补出来的那一行跟一次真实上传
// 长得一模一样，于是它自动走完后面所有既有链路，一个新分支都不需要。
//
// ⚠️ 成品那一侧的 extracted_text 是空的（它从没被解析过）。不在这里补解析：解析要
// 拿字节，而拿字节就得把一份可能 200MB 的 pptx 拉进 lea-agent —— 正是 publish 那条路
// 特意绕开的事。空文本的后果是可接受的：renderUpload 的兜底分支会告诉模型"没提取到
// 文本，去沙箱处理"，而对一份模型自己刚生成的文件来说，那本来就是对的做法。
// ## 不需要重建引擎
//
// 挂载类端点（挂 skill / 切专家）都要 applyAndReport 丢掉内存里的引擎，因为它们改的是
// **引擎构建时冻住的东西**（工具数组、system prompt）。文件不是：沙箱按路径实时读
// NFS，注入靠下一条消息的 attachment_ids —— 两条都不经过引擎对象。所以这里不碰
// Manager，导入在下一条消息里自然生效。
func handleImportToSession() gin.HandlerFunc {
	return func(c *gin.Context) {
		// 归属校验：这个会话必须是调用方的。少了这一段，猜一个 session_id 就能
		// 往别人的会话里塞文件。
		//
		// 用 sessionOwnedIfExists 而不是挂载端点那个 sessionOwnerGuard —— 后者在会话
		// 行不存在时 404，而**会话行是第一条消息才由 TouchSession 插的**（POST /sessions
		// 只在内存建）。于是最自然的那条路径会整个失败：新开对话 → 从文件库导入上周
		// 那份报告 → 问它。用户什么都没做错，却拿到"会话不存在"。
		//
		// 上传那条路（handleUploadAttachment）早就是这么判的，因为"新开对话 → 拖个
		// 文件进来 → 发第一条消息"是同一个时序。导入没道理更严。
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		if !sessionOwnedIfExists(c, db, c.Param("id")) {
			c.JSON(404, gin.H{"error": "session not found"})
			return
		}
		var body struct {
			Kind string `json:"kind"`
			ID   int64  `json:"id"`
		}
		if err := c.ShouldBindJSON(&body); err != nil {
			c.JSON(400, gin.H{"error": "bad body: " + err.Error()})
			return
		}
		if body.Kind != "upload" && body.Kind != "artifact" {
			c.JSON(400, gin.H{"error": "kind 只能是 upload 或 artifact"})
			return
		}
		if body.ID <= 0 {
			c.JSON(400, gin.H{"error": "bad id"})
			return
		}

		ctx := c.Request.Context()
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")
		sessionID := c.Param("id")

		// 取源文件。归属过滤在 SQL 里 —— 这是第二道边界（第一道是上面的会话归属）：
		// 会话是你的，不代表你能把**别人的文件**导进去。
		//
		// 两条路汇合成同一个 Attachment：导入的产物是**目标会话下的一行上传登记**，
		// 无论源头是上传件还是成品。差别只在从哪儿取值。
		var src persistence.Attachment
		var destName string
		if body.Kind == "upload" {
			att, err := persistence.GetUploadForImport(ctx, db, tenantID, userID, body.ID)
			if err != nil {
				c.JSON(404, gin.H{"error": "file not found"})
				return
			}
			src = att
			// 上传件已经有 staged_path（blob 那步算过并落了库）。原样传过去，
			// 新会话里的文件名才跟原会话一致。
			destName = att.StagedPath
		} else {
			a, err := persistence.GetArtifactForUser(ctx, db, tenantID, userID, body.ID)
			if err != nil {
				c.JSON(404, gin.H{"error": "file not found"})
				return
			}
			src = persistence.Attachment{
				Filename: a.Filename,
				FileKey:  a.OBSKey,
				FileSize: a.Size,
				SHA256:   a.SHA256,
				FileType: extOf(a.Filename),
			}
			// media_type 按扩展名走跟上传同一张表：注入文本和 renderUpload 都按它
			// 分档，成品那行也得分对档（一张模型画出来的 png 该走 image 分支，
			// 说"没有文本层"，而不是"解析失败"）。
			if mt, ok := parserExts[src.FileType]; ok {
				src.MediaType = mt
			} else {
				src.MediaType = "file"
			}
			// 成品没有 staged_path（从没物化过），destName 留空让 lea-tools 现算
			// 并回给我们。不在这边拼 —— 清洗规则只有一份实现，在 safe_stage_name。
		}
		if src.FileKey == "" {
			// 登记在、对象不在。上传件是当时 OBS 不可达，成品是配额满时的软放行。
			// 两种都不是"临时故障"，说清楚，别让用户重试。
			c.JSON(410, gin.H{"error": "这个文件没有保存原件，无法导入到会话"})
			return
		}

		// ── 1. 物化字节 ─────────────────────────────────────────
		//
		// 先物化再登记。反过来的话，登记成功、物化失败会留下一行指向**不存在的**
		// staged_path —— 而那一行会被注入进消息（"文件在 /mnt/uploads/x"），
		// 模型去读，拿到"文件不存在"。宁可反过来：物化了没登记，那份字节跟着会话
		// 30 天后一起被回收，期间没有任何人知道它在，也就不会有人被它误导。
		staged, serr := stageLibraryFile(c, tenantID, sessionID, src.FileKey, destName, src.Filename, src.SHA256)
		if serr != nil {
			// 状态码原样透传：507（会话配额满）是用户能处理的，503（没共享存储）
			// 和 502（OBS 故障）不是，前端要能分开说。
			c.JSON(serr.status, gin.H{"error": serr.msg})
			return
		}

		// ── 2. 在目标会话下补一行登记 ───────────────────────────
		//
		// 这一行跟一次真实上传落下的行形状完全相同，于是它自动接上后面所有既有链路：
		// 前端把 id 放进 attachment_ids → LoadAttachments → 注入 → Read 能反查到。
		newID, err := persistence.InsertAttachment(ctx, db, persistence.Attachment{
			TenantID:  tenantID,
			UserID:    userID,
			SessionID: sessionID,
			Filename:  src.Filename,
			FileKey:   src.FileKey, // 同一个对象，不复制字节，也不多占 OBS 配额
			FileType:  src.FileType,
			FileSize:  src.FileSize,
			MediaType: src.MediaType,
			// 解析结果按内容寻址缓存，同 sha 复制过来是免费的。成品那侧是空串
			// （从没解析过）—— 见函数头最后一段。
			ExtractedText: src.ExtractedText,
			SHA256:        src.SHA256,
			StagedPath:    staged.StagedName,
		})
		if err != nil {
			c.JSON(500, gin.H{"error": "导入登记失败：" + err.Error()})
			return
		}

		c.JSON(200, gin.H{
			"status": "ok",
			// attachment_id 是这个响应里前端唯一必须用的字段：下一条消息带上它，
			// 模型才知道这个文件在。名字跟上传端点的响应对齐，前端两条路同一套处理。
			"attachment_id": newID,
			"filename":      src.Filename,
			"file_type":     src.FileType,
			"media_type":    src.MediaType,
			"file_size":     src.FileSize,
			"staged_name":   staged.StagedName,
			"sandbox_path":  persistence.UploadsMount + "/" + staged.StagedName,
			// 有文本 = 能直接问内容；没有 = 只能进沙箱处理。前端据此给提示，
			// 跟上传响应里的能力提示是同一件事。
			"has_text": src.ExtractedText != "",
		})
	}
}

// extOf 取小写扩展名，没有则空串。跟 handleUploadAttachment 里那段同一条规则。
func extOf(filename string) string {
	if i := strings.LastIndex(filename, "."); i >= 0 {
		return strings.ToLower(filename[i+1:])
	}
	return ""
}

type stageResult struct {
	StagedName  string `json:"staged_name"`
	Size        int64  `json:"size"`
	SandboxPath string `json:"sandbox_path"`
}

// stageError 带着状态码往上传。lea-tools 那边的 507/503/502 各有不同的用户含义，
// 全折成 500 的话前端只能说"出错了"。
type stageError struct {
	status int
	msg    string
}

func (e *stageError) Error() string { return e.msg }

func stageLibraryFile(
	c *gin.Context, tenantID, sessionID, fileKey, destName, filename, sha string,
) (*stageResult, *stageError) {
	payload, err := json.Marshal(map[string]string{
		"tenant_id":  tenantID,
		"session_id": sessionID,
		"file_key":   fileKey,
		"dest_name":  destName,
		"filename":   filename,
		"sha256":     sha,
	})
	if err != nil {
		return nil, &stageError{500, err.Error()}
	}
	req, err := http.NewRequestWithContext(c.Request.Context(), http.MethodPost,
		toolsBaseURL()+"/api/files/uploads/stage", strings.NewReader(string(payload)))
	if err != nil {
		return nil, &stageError{500, err.Error()}
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := stageClient.Do(req)
	if err != nil {
		return nil, &stageError{502, "文件服务不可达：" + err.Error()}
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		msg := strings.TrimSpace(string(b))
		// lea-tools 的错误体是 {"detail": "..."}，里面那句中文是写给用户看的
		// （"会话文件配额已满（已用 xxMB…）"）。剥出来直接用，别把 JSON 原文丢给前端。
		var wrapped struct {
			Detail string `json:"detail"`
		}
		if json.Unmarshal([]byte(msg), &wrapped) == nil && wrapped.Detail != "" {
			msg = wrapped.Detail
		}
		return nil, &stageError{resp.StatusCode, msg}
	}
	var out stageResult
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, &stageError{502, "物化响应解析失败：" + err.Error()}
	}
	if out.StagedName == "" {
		return nil, &stageError{502, "物化返回了空文件名"}
	}
	return &out, nil
}
