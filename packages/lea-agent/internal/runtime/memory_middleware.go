package runtime

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"strings"
)

type MemoryMiddleware struct {
	ragURL   string
	tenantID string
	userID   string
	agentID  string
	topK     int
}

func NewMemoryMiddleware(tenantID, userID, agentID string) *MemoryMiddleware {
	return &MemoryMiddleware{
		ragURL:   ragServiceURL,
		tenantID: tenantID,
		userID:   userID,
		agentID:  agentID,
		topK:     5,
	}
}

// PreTurn 取画像（全局+专属）+ 搜相关记忆，拼接注入
func (m *MemoryMiddleware) PreTurn(ctx context.Context, userMessage string) string {
	globalProfile := m.fetchProfile(ctx, "")
	agentProfile := m.fetchProfile(ctx, m.agentID)
	memories := m.searchMemories(ctx, userMessage)

	var parts []string
	if globalProfile != "" {
		parts = append(parts, "[用户画像]\n"+globalProfile)
	}
	if agentProfile != "" {
		parts = append(parts, "[当前进度]\n"+agentProfile)
	}
	if memories != "" {
		parts = append(parts, "[相关记忆]\n"+memories)
	}
	return strings.Join(parts, "\n\n")
}

// OnSessionEnd session 结束时调用：提取记忆 + merge 画像（全局+专属）
func (m *MemoryMiddleware) OnSessionEnd(ctx context.Context, messages []MessagePair, sessionID string) {
	if len(messages) < 2 {
		return
	}
	m.extractMemories(ctx, messages, sessionID)
	m.mergeProfile(ctx, sessionID, "")        // 全局画像
	m.mergeProfile(ctx, sessionID, m.agentID) // agent 专属画像
}

func (m *MemoryMiddleware) fetchProfile(ctx context.Context, agentID string) string {
	url := m.ragURL + "/memory/profile?user_id=" + m.userID +
		"&agent_id=" + agentID + "&tenant_id=" + m.tenantID
	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return ""
	}
	resp, err := httpClient.Do(req)
	if err != nil || resp.StatusCode != 200 {
		return ""
	}
	defer resp.Body.Close()
	var out struct {
		Profile string `json:"profile"`
	}
	json.NewDecoder(resp.Body).Decode(&out)
	return out.Profile
}

func (m *MemoryMiddleware) searchMemories(ctx context.Context, query string) string {
	payload := map[string]any{
		"query": query, "user_id": m.userID,
		"tenant_id": m.tenantID, "top_k": m.topK,
		"scopes": []string{"global", "agent:" + m.agentID},
	}
	body, _ := json.Marshal(payload)
	req, _ := http.NewRequestWithContext(ctx, "POST",
		m.ragURL+"/memory/search", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")

	resp, err := httpClient.Do(req)
	if err != nil || resp.StatusCode != 200 {
		return ""
	}
	defer resp.Body.Close()

	var results []struct {
		Content string `json:"content"`
	}
	json.NewDecoder(resp.Body).Decode(&results)
	var lines []string
	for _, r := range results {
		if r.Content != "" {
			lines = append(lines, "- "+r.Content)
		}
	}
	return strings.Join(lines, "\n")
}

func (m *MemoryMiddleware) extractMemories(ctx context.Context, messages []MessagePair, sessionID string) {
	payload := map[string]any{
		"user_id": m.userID, "tenant_id": m.tenantID,
		"agent_id": m.agentID, "session_id": sessionID,
		"messages": messages,
	}
	body, _ := json.Marshal(payload)
	req, _ := http.NewRequestWithContext(ctx, "POST",
		m.ragURL+"/memory/extract", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	resp, err := httpClient.Do(req)
	if err == nil {
		resp.Body.Close()
	}
}

func (m *MemoryMiddleware) mergeProfile(ctx context.Context, sessionID, agentID string) {
	payload := map[string]any{
		"user_id": m.userID, "agent_id": agentID,
		"tenant_id": m.tenantID, "session_id": sessionID,
	}
	body, _ := json.Marshal(payload)
	req, _ := http.NewRequestWithContext(ctx, "POST",
		m.ragURL+"/memory/profile/merge", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	resp, err := httpClient.Do(req)
	if err == nil {
		resp.Body.Close()
	}
}

type MessagePair struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

func (m *MemoryMiddleware) SetTopK(k int) { m.topK = k }
