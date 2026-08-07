package router

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"mime/multipart"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
)

const attachmentMaxBytes int64 = 30 * 1024 * 1024

// parserExts 与 lea-parser dispatcher SUPPORTED_FORMATS 保持一致（同步维护）。
// 不在表内的扩展名 → 返 415，避免无谓打 parser 又拿不到内容。
var parserExts = map[string]string{
	// 文档 → text
	"pdf": "text", "docx": "text", "doc": "text", "pptx": "text", "ppt": "text",
	"md": "text", "txt": "text", "csv": "text", "json": "text",
	// 图片 → image（parser 会用 VLM 出 caption 塞进 md_content）
	"png": "image", "jpg": "image", "jpeg": "image", "webp": "image",
	"gif": "image", "bmp": "image",
	// 音频 / 视频 → 分类保留，本 sprint 仍按 text 处理拼进 message
	"mp3": "audio", "wav": "audio", "m4a": "audio", "flac": "audio", "ogg": "audio",
	"mp4": "video", "avi": "video", "mkv": "video", "mov": "video",
	"wmv": "video", "flv": "video", "webm": "video",
}

// handleUploadAttachment 接收 multipart file，转发 lea-parser 拿 md_content，
// 然后落 chat_attachments（file_key 留空，H5 版本不上 OBS）。
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
		mediaType, ok := parserExts[ext]
		if !ok {
			c.JSON(415, gin.H{"error": "unsupported file type: " + ext})
			return
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

		mdContent, err := parseViaParser(c, buf, filename)
		if err != nil {
			log.Printf("parser_call_failed filename=%s err=%v", filename, err)
			c.JSON(502, gin.H{"error": "parser upstream failed: " + err.Error()})
			return
		}

		sessionID := c.PostForm("session_id")

		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		id, err := persistence.InsertAttachment(c.Request.Context(), db, persistence.Attachment{
			TenantID:      tenantID,
			UserID:        userID,
			SessionID:     sessionID,
			Filename:      filename,
			FileKey:       "",
			FileType:      ext,
			FileSize:      int64(len(buf)),
			MediaType:     mediaType,
			ExtractedText: mdContent,
		})
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}

		preview := mdContent
		if len(preview) > 200 {
			preview = preview[:200]
		}
		c.JSON(200, gin.H{
			"id":           id,
			"filename":     filename,
			"file_type":    ext,
			"file_size":    len(buf),
			"media_type":   mediaType,
			"text_preview": preview,
		})
	}
}

// parseViaParser POST multipart 到 lea-parser/parse?chunk=false 拿 md_content。
// 透传 Authorization header（lea-parser 用同一份 JWT SECRET）。
func parseViaParser(c *gin.Context, fileBytes []byte, filename string) (string, error) {
	baseURL := os.Getenv("LEA_PARSER_URL")
	if baseURL == "" {
		baseURL = "http://localhost:8600"
	}
	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)
	part, err := writer.CreateFormFile("file", filename)
	if err != nil {
		return "", err
	}
	if _, err := part.Write(fileBytes); err != nil {
		return "", err
	}
	if err := writer.Close(); err != nil {
		return "", err
	}

	req, err := http.NewRequestWithContext(c.Request.Context(), "POST",
		baseURL+"/parse?chunk=false", body)
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())
	if auth := c.GetHeader("Authorization"); auth != "" {
		req.Header.Set("Authorization", auth)
	}

	client := &http.Client{Timeout: 120 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return "", fmt.Errorf("parser status=%d body=%s", resp.StatusCode, string(b))
	}
	var out struct {
		MdContent string `json:"md_content"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return "", err
	}
	return out.MdContent, nil
}
