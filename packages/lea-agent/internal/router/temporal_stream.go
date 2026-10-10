package router

import (
	"context"
	"database/sql"
	"errors"
	"log"
	"time"

	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/eventlog"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
)

const temporalStatusPoll = 2 * time.Second

func handleTemporalSendStream(c *gin.Context, coordinator RunCoordinator, sess *session.AgentSession, req SendRequest) {
	logs := eventlog.Shared()
	if !logs.Enabled() {
		c.JSON(503, gin.H{"error": "temporal execution requires Redis event logging"})
		return
	}

	cursor, err := logs.LastSeq(c.Request.Context(), sess.ID())
	if err != nil {
		log.Printf("temporal_stream_cursor_failed session=%s err=%v", sess.ID(), err)
		c.JSON(503, gin.H{"error": "cannot initialize event stream"})
		return
	}
	runID, err := submitTemporalRun(
		c.Request.Context(), coordinator, sess, req, c.GetString("tenant_id"), c.GetString("user_id"),
	)
	if err != nil {
		if errors.Is(err, session.ErrPlanModeUnsupported) {
			c.JSON(400, gin.H{"error": "plan mode is not supported for expert teams"})
			return
		}
		if errors.Is(err, persistence.ErrActiveRun) || errors.Is(err, session.ErrBusy) {
			c.JSON(409, gin.H{"error": "a turn is already running for this session"})
			return
		}
		// 会话已经被软删（另一个标签页点了"删除"）。见 handlers.go 同处的说明。
		if errors.Is(err, persistence.ErrSessionArchived) {
			c.JSON(410, gin.H{"error": "该对话已被删除", "code": "session_archived"})
			return
		}
		c.JSON(500, gin.H{"error": err.Error()})
		return
	}

	c.Writer.Header().Set("Content-Type", "text/event-stream")
	c.Writer.Header().Set("Cache-Control", "no-cache")
	c.Writer.Header().Set("Connection", "keep-alive")
	c.Writer.Header().Set("X-Accel-Buffering", "no")
	c.Writer.WriteHeader(200)
	c.SSEvent("session", gin.H{"session_id": sess.ID(), "run_id": runID})
	c.Writer.Flush()

	tailTemporalRun(c, logs, sess.ID(), runID, c.GetString("tenant_id"), c.GetString("user_id"), &cursor,
		func() { onTemporalClientGone(runID, sess.ID(), req.OnDisconnect, coordinator) })
}

func tailTemporalRun(c *gin.Context, logs *eventlog.Log, sessionID, runID, tenantID, userID string, cursor *uint64, onGone func()) {
	heartbeat := time.NewTicker(keepaliveEvery)
	defer heartbeat.Stop()
	started := false
	var terminalSince time.Time

	for {
		entries, err := logs.Wait(c.Request.Context(), sessionID, *cursor, temporalStatusPoll)
		if err != nil {
			if c.Request.Context().Err() != nil {
				onGone()
			}
			return
		}
		if len(entries) > 0 {
			for _, entry := range entries {
				if entry.Type == "start" {
					started = true
				}
			}
			if replay(c, entries, cursor) {
				return
			}
			heartbeat.Reset(keepaliveEvery)
		}

		terminal, runErr := temporalRunTerminal(c.Request.Context(), runID, tenantID, userID)
		if runErr != nil && !errors.Is(runErr, sql.ErrNoRows) {
			log.Printf("temporal_stream_status_failed run=%s err=%v", runID, runErr)
		}
		if terminal != nil {
			if !started {
				writeTemporalTerminal(c, *terminal)
				return
			}
			if terminalSince.IsZero() {
				terminalSince = time.Now()
			} else if time.Since(terminalSince) >= temporalStatusPoll {
				writeTemporalTerminal(c, *terminal)
				return
			}
		}

		select {
		case <-c.Request.Context().Done():
			onGone()
			return
		case <-heartbeat.C:
			if _, err := c.Writer.WriteString(": keepalive\n\n"); err != nil {
				onGone()
				return
			}
			c.Writer.Flush()
		default:
		}
	}
}

func temporalRunTerminal(ctx context.Context, runID, tenantID, userID string) (*persistence.AgentRun, error) {
	db, err := persistence.DB()
	if err != nil {
		return nil, err
	}
	if db == nil {
		return nil, errors.New("persistence unavailable")
	}
	run, err := persistence.GetAgentRun(ctx, db, runID, tenantID, userID)
	if err != nil {
		return nil, err
	}
	switch run.Status {
	case persistence.RunCompleted, persistence.RunCancelled, persistence.RunFailed, persistence.RunTimedOut:
		return &run, nil
	default:
		return nil, nil
	}
}

func writeTemporalTerminal(c *gin.Context, run persistence.AgentRun) {
	if run.Status == persistence.RunFailed || run.Status == persistence.RunTimedOut {
		message := run.ErrorMessage
		if message == "" {
			message = string(run.Status)
		}
		writeEvent(c, session.SSEEvent{Type: "error", Data: message})
	}
	writeEvent(c, session.SSEEvent{Type: "done"})
}

func onTemporalClientGone(runID, sessionID, policy string, coordinator RunCoordinator) {
	if !shouldCancelOnDisconnect(policy) {
		log.Printf("sse_client_gone session=%s run=%s policy=continue", sessionID, runID)
		return
	}
	db, err := persistence.DB()
	if err == nil && db != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		_, err = persistence.RequestAgentRunCancellation(ctx, db, runID)
		cancel()
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	cancelErr := coordinator.Cancel(ctx, runID)
	cancel()
	log.Printf("sse_client_gone session=%s run=%s policy=cancel persist_err=%v temporal_err=%v",
		sessionID, runID, err, cancelErr)
}
