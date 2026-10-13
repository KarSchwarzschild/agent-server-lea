package runtime

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/require"
	"gitlab.ouc-online.com.cn/aibase/agentloop/tool"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/breaker"
)

func TestMemoryToolsReceiveCurrentAgentID(t *testing.T) {
	cfg := &AgentConfig{ID: 42, MemoryMode: "on"}

	var search *MemorySearchTool
	for _, candidate := range BuildPlatformTools(cfg, "tenant-a", "user-a", "session-a") {
		if typed, ok := candidate.(*MemorySearchTool); ok {
			search = typed
		}
	}
	require.NotNil(t, search)
	require.Equal(t, "42", search.agentID)

	var add *MemoryAddTool
	for _, candidate := range BuildMainSessionTools(cfg, "tenant-a", "user-a", "session-a", nil) {
		if typed, ok := candidate.(*MemoryAddTool); ok {
			add = typed
		}
	}
	require.NotNil(t, add)
	require.Equal(t, "42", add.agentID)
}

func TestMemorySearchOnlyRequestsGlobalAndCurrentAgentScopes(t *testing.T) {
	var payload map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/memory/search", r.URL.Path)
		require.NoError(t, json.NewDecoder(r.Body).Decode(&payload))
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`[]`))
	}))
	defer server.Close()

	oldURL, oldBreaker := ragServiceURL, memoryBreaker
	ragServiceURL = server.URL
	memoryBreaker = breaker.NewMemoryBreaker()
	t.Cleanup(func() {
		ragServiceURL = oldURL
		memoryBreaker = oldBreaker
	})

	search := &MemorySearchTool{tenantID: "tenant-a", userID: "user-a", agentID: "42"}
	_, err := search.Run(context.Background(), json.RawMessage(`{"query":"偏好"}`), &tool.Context{}, nil)
	require.NoError(t, err)
	require.Equal(t, "tenant-a", payload["tenant_id"])
	require.Equal(t, "user-a", payload["user_id"])
	require.Equal(t, []any{"global", "agent:42"}, payload["scopes"])
}

func TestMemoryAddMapsScopeToCurrentAgent(t *testing.T) {
	var payload map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/memory/add", r.URL.Path)
		require.NoError(t, json.NewDecoder(r.Body).Decode(&payload))
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":1}`))
	}))
	defer server.Close()

	oldURL, oldBreaker := ragServiceURL, memoryBreaker
	ragServiceURL = server.URL
	memoryBreaker = breaker.NewMemoryBreaker()
	t.Cleanup(func() {
		ragServiceURL = oldURL
		memoryBreaker = oldBreaker
	})

	add := &MemoryAddTool{tenantID: "tenant-a", userID: "user-a", agentID: "42"}
	_, err := add.Run(context.Background(), json.RawMessage(`{"content":"项目使用 Go","scope":"agent"}`), &tool.Context{}, nil)
	require.NoError(t, err)
	require.Equal(t, "agent:42", payload["scope"])
}

func TestMemoryAddDefaultsToPlatformScope(t *testing.T) {
	var payload map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.NoError(t, json.NewDecoder(r.Body).Decode(&payload))
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	oldURL, oldBreaker := ragServiceURL, memoryBreaker
	ragServiceURL = server.URL
	memoryBreaker = breaker.NewMemoryBreaker()
	t.Cleanup(func() {
		ragServiceURL = oldURL
		memoryBreaker = oldBreaker
	})

	add := &MemoryAddTool{tenantID: "tenant-a", userID: "user-a", agentID: "42"}
	_, err := add.Run(context.Background(), json.RawMessage(`{"content":"偏好简洁回答"}`), &tool.Context{}, nil)
	require.NoError(t, err)
	require.Equal(t, "global", payload["scope"])
}
