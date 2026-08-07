package router

import (
	"database/sql"
	"errors"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
)

// downloadRateLimiter 是简易 per-user 内存滑动窗口，挡下载爆冲。
// 生产建议换 Redis + 分布式限流（多副本 lea-agent 时内存版失效）。
// 60 秒窗口 30 次是保护性上限：正常用户点点下载够用，脚本刷子会撞墙。
type downloadRateLimiter struct {
	mu     sync.Mutex
	events map[string][]time.Time
	window time.Duration
	limit  int
}

var globalDownloadRL = &downloadRateLimiter{
	events: make(map[string][]time.Time),
	window: 60 * time.Second,
	limit:  30,
}

// allow 返回 true 表示放行，false 表示拒绝（超限）。now 参数便于单元测试。
func (r *downloadRateLimiter) allow(key string, now time.Time) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	cutoff := now.Add(-r.window)
	events := r.events[key]
	kept := events[:0]
	for _, t := range events {
		if t.After(cutoff) {
			kept = append(kept, t)
		}
	}
	if len(kept) >= r.limit {
		r.events[key] = kept
		return false
	}
	r.events[key] = append(kept, now)
	return true
}

// assertSessionOwner 校验当前 JWT 主体是否拥有该 session。
// 返回 tenantID 供上游拼路径用；ok=false 时已写好响应，调用方直接 return。
// onMissing 决定 session 不在库里时怎么响应 —— 这不是一种错误状态，
// 而是取决于调用方：list 视作空工作区（session 可能刚建还没落库），
// download 视作 404（下载不存在的文件该报错）。
type onMissing func(c *gin.Context)

func missing404(c *gin.Context) { c.JSON(404, gin.H{"error": "session not found"}) }

func assertSessionOwner(c *gin.Context, sessionID string, whenMissing onMissing) (tenantID string, ok bool) {
	tenantID = c.GetString("tenant_id")
	userID := c.GetString("user_id")

	db, err := persistence.DB()
	if err != nil || db == nil {
		c.JSON(503, gin.H{"error": "persistence unavailable"})
		return "", false
	}

	ownerUser, ownerTenant, err := persistence.GetSessionOwner(c.Request.Context(), db, sessionID)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			whenMissing(c)
			return "", false
		}
		c.JSON(500, gin.H{"error": err.Error()})
		return "", false
	}
	if ownerUser != userID || ownerTenant != tenantID {
		c.JSON(403, gin.H{"error": "forbidden"})
		return "", false
	}
	return tenantID, true
}

// handleListArtifacts 列出某 session 的 workspace 文件树。
//
// 前端只传 session_id；tenant_id 从 JWT 取（前端传的不可信）。
// 参数：?session_id=<sid>[&show_hidden=true]
func handleListArtifacts() gin.HandlerFunc {
	toolsURL := os.Getenv("LEA_TOOLS_URL")
	if toolsURL == "" {
		toolsURL = "http://localhost:8400"
	}
	client := &http.Client{Timeout: 30 * time.Second}

	return func(c *gin.Context) {
		sessionID := c.Query("session_id")
		if sessionID == "" {
			c.JSON(400, gin.H{"error": "session_id required"})
			return
		}

		// session 还没落库 → 空工作区，不是错误。POST /api/sessions 只生成内存
		// ID，第一条消息才写 chat_sessions；抽屉在这之间查询是正常时序。
		tenantID, ok := assertSessionOwner(c, sessionID, func(c *gin.Context) {
			c.JSON(200, gin.H{"files": []any{}, "truncated": false, "store": "pending"})
		})
		if !ok {
			return
		}

		q := url.Values{}
		q.Set("session_id", sessionID)
		q.Set("tenant_id", tenantID)
		if c.Query("show_hidden") == "true" {
			q.Set("show_hidden", "true")
		}

		req, err := http.NewRequestWithContext(c.Request.Context(), http.MethodGet,
			toolsURL+"/api/files/list?"+q.Encode(), nil)
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		resp, err := client.Do(req)
		if err != nil {
			log.Printf("list_upstream_failed session=%s err=%v", sessionID, err)
			c.JSON(502, gin.H{"error": "upstream unreachable"})
			return
		}
		defer resp.Body.Close()

		if ct := resp.Header.Get("Content-Type"); ct != "" {
			c.Writer.Header().Set("Content-Type", ct)
		}
		c.Writer.WriteHeader(resp.StatusCode)
		if _, err := io.Copy(c.Writer, resp.Body); err != nil {
			log.Printf("list_stream_failed session=%s err=%v", sessionID, err)
		}
	}
}

// handleDownloadArtifact 是沙箱产物下载入口。
//
// 流程：JWT 中间件已把 user_id / tenant_id 塞进 context → 校验 session 归属 →
// 通过后转发到 lea-tools /api/files/download 拉 S3 对象流回前端。
//
// 参数：?session_id=<sid>&path=<relative>
// 校验失败：403（越权）/ 404（session 或文件不存在）
func handleDownloadArtifact() gin.HandlerFunc {
	toolsURL := os.Getenv("LEA_TOOLS_URL")
	if toolsURL == "" {
		toolsURL = "http://localhost:8400"
	}
	client := &http.Client{Timeout: 60 * time.Second}

	return func(c *gin.Context) {
		sessionID := c.Query("session_id")
		path := c.Query("path")
		if sessionID == "" || path == "" {
			c.JSON(400, gin.H{"error": "session_id and path required"})
			return
		}

		if !globalDownloadRL.allow(c.GetString("tenant_id")+":"+c.GetString("user_id"), time.Now()) {
			c.JSON(429, gin.H{"error": "download rate limit exceeded, try again later"})
			return
		}

		tenantID, ok := assertSessionOwner(c, sessionID, missing404)
		if !ok {
			return
		}

		q := url.Values{}
		q.Set("session_id", sessionID)
		q.Set("path", path)
		// tenant_id 来自 JWT，前端不传也不可信。lea-tools 靠它拼 NFS 路径；
		// 不传会退化成 S3 分支，v2 架构下读不到文件。
		q.Set("tenant_id", tenantID)
		upstreamURL := toolsURL + "/api/files/download?" + q.Encode()

		req, err := http.NewRequestWithContext(c.Request.Context(), http.MethodGet, upstreamURL, nil)
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		resp, err := client.Do(req)
		if err != nil {
			log.Printf("download_upstream_failed session=%s path=%s err=%v", sessionID, path, err)
			c.JSON(502, gin.H{"error": "upstream unreachable"})
			return
		}
		defer resp.Body.Close()

		// 透传关键 header：让浏览器拿到正确的 Content-Type / Content-Disposition / Content-Length
		for _, h := range []string{"Content-Type", "Content-Disposition", "Content-Length"} {
			if v := resp.Header.Get(h); v != "" {
				c.Writer.Header().Set(h, v)
			}
		}
		c.Writer.WriteHeader(resp.StatusCode)
		if _, err := io.Copy(c.Writer, resp.Body); err != nil {
			log.Printf("download_stream_failed session=%s path=%s err=%v", sessionID, path, err)
		}
	}
}
