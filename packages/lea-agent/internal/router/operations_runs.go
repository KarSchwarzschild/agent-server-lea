package router

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/temporalrun"
)

type operationReasonBody struct {
	Reason string `json:"reason" binding:"required"`
}

func bindOperationReason(c *gin.Context) (operationReasonBody, bool) {
	var body operationReasonBody
	if err := c.ShouldBindJSON(&body); err != nil || strings.TrimSpace(body.Reason) == "" {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"error": "reason is required"})
		return operationReasonBody{}, false
	}
	return body, true
}

func adminRole(c *gin.Context) (tenantID string, platform bool, ok bool) {
	role := strings.ToLower(strings.TrimSpace(c.GetString("role")))
	if role != "admin" && role != "platform_admin" {
		c.JSON(http.StatusForbidden, gin.H{"error": "admin access required"})
		return "", false, false
	}
	return c.GetString("tenant_id"), role == "platform_admin", true
}

func runView(run persistence.AgentRun) gin.H {
	return gin.H{
		"run_id": run.ID, "session_id": run.SessionID, "tenant_id": run.TenantID,
		"user_id": run.UserID, "agent_id": run.AgentID, "project_id": run.ProjectID,
		"status": run.Status, "worker_id": run.WorkerID, "attempt": run.Attempt,
		"dispatch_attempt": run.DispatchAttempt, "dispatch_error": run.DispatchError,
		"cancel_requested_at": run.CancelRequestedAt, "started_at": run.StartedAt,
		"finished_at": run.FinishedAt, "error_message": run.ErrorMessage,
		"created_at": run.CreatedAt, "updated_at": run.UpdatedAt,
	}
}

func recordRunAudit(c *gin.Context, action, result, reason string, run persistence.AgentRun, before, after gin.H) {
	base := strings.TrimRight(strings.TrimSpace(os.Getenv("LEA_CONFIG_URL")), "/")
	if base == "" {
		return
	}
	payload, err := json.Marshal(gin.H{
		"tenant_id": run.TenantID, "action": action, "result": result,
		"resource_type": "agent_run", "resource_id": run.ID,
		"before_summary": before, "after_summary": after, "reason": reason,
	})
	if err != nil {
		log.Printf("run_audit_encode_failed action=%s run=%s err=%v", action, run.ID, err)
		return
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, base+"/internal/governance/audit-events", bytes.NewReader(payload))
	if err != nil {
		log.Printf("run_audit_request_failed action=%s run=%s err=%v", action, run.ID, err)
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if token := strings.TrimSpace(os.Getenv("LEA_INTERNAL_TOKEN")); token != "" {
		req.Header.Set("X-Internal-Token", token)
	}
	req.Header.Set("X-User-ID", c.GetString("user_id"))
	req.Header.Set("X-Tenant-ID", c.GetString("tenant_id"))
	req.Header.Set("X-User-Role", c.GetString("role"))
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Printf("run_audit_write_failed action=%s run=%s err=%v", action, run.ID, err)
		return
	}
	defer response.Body.Close()
	if response.StatusCode >= http.StatusBadRequest {
		log.Printf("run_audit_write_failed action=%s run=%s status=%d", action, run.ID, response.StatusCode)
	}
}

func handleListOperationRuns() gin.HandlerFunc {
	return func(c *gin.Context) {
		tenantID, platform, ok := adminRole(c)
		if !ok {
			return
		}
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(http.StatusServiceUnavailable, gin.H{"error": "run store unavailable"})
			return
		}
		page, _ := strconv.Atoi(c.DefaultQuery("page", "1"))
		size, _ := strconv.Atoi(c.DefaultQuery("size", "50"))
		agentID, _ := strconv.Atoi(c.Query("agent_id"))
		filter := persistence.AdminRunFilter{
			TenantID: tenantID, UserID: c.Query("user_id"),
			Status: persistence.RunStatus(c.Query("status")), AgentID: agentID,
			Page: page, Size: size,
		}
		if platform {
			filter.TenantID = strings.TrimSpace(c.Query("tenant_id"))
		}
		runs, total, err := persistence.ListAgentRunsForAdmin(c.Request.Context(), db, filter)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
			return
		}
		items := make([]gin.H, 0, len(runs))
		for _, run := range runs {
			items = append(items, runView(run))
		}
		c.JSON(http.StatusOK, gin.H{"items": items, "total": total, "page": page, "size": size})
	}
}

func operationRun(c *gin.Context) (persistence.AgentRun, *sql.DB, bool) {
	tenantID, platform, ok := adminRole(c)
	if !ok {
		return persistence.AgentRun{}, nil, false
	}
	db, err := persistence.DB()
	if err != nil || db == nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "run store unavailable"})
		return persistence.AgentRun{}, nil, false
	}
	if platform {
		tenantID = ""
	}
	run, err := persistence.GetAgentRunForAdmin(c.Request.Context(), db, c.Param("run_id"), tenantID)
	if errors.Is(err, sql.ErrNoRows) {
		c.JSON(http.StatusNotFound, gin.H{"error": "run not found"})
		return persistence.AgentRun{}, nil, false
	}
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return persistence.AgentRun{}, nil, false
	}
	return run, db, true
}

func handleGetOperationRun() gin.HandlerFunc {
	return func(c *gin.Context) {
		run, _, ok := operationRun(c)
		if ok {
			c.JSON(http.StatusOK, runView(run))
		}
	}
}

func handleCancelOperationRun(coordinator RunCoordinator) gin.HandlerFunc {
	return func(c *gin.Context) {
		run, db, ok := operationRun(c)
		if !ok {
			return
		}
		body, ok := bindOperationReason(c)
		if !ok {
			return
		}
		before := runView(run)
		if run.Status != persistence.RunQueued && run.Status != persistence.RunRunning && run.Status != persistence.RunCancelling {
			recordRunAudit(c, "agent_run.cancel", "blocked", body.Reason, run, before, nil)
			c.JSON(http.StatusConflict, gin.H{"error": "run is not cancellable"})
			return
		}
		changed, err := persistence.RequestAgentRunCancellation(c.Request.Context(), db, run.ID)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
			return
		}
		if run.Status == persistence.RunRunning && coordinator != nil {
			ctx, cancel := context.WithTimeout(c.Request.Context(), 10*time.Second)
			err = coordinator.Cancel(ctx, run.ID)
			cancel()
			if err != nil {
				updated, _ := persistence.GetAgentRunForAdmin(c.Request.Context(), db, run.ID, "")
				recordRunAudit(c, "agent_run.cancel", "partial", body.Reason, run, before, runView(updated))
				c.JSON(http.StatusBadGateway, gin.H{"error": err.Error(), "cancellation_recorded": changed})
				return
			}
		}
		updated, _ := persistence.GetAgentRunForAdmin(c.Request.Context(), db, run.ID, "")
		recordRunAudit(c, "agent_run.cancel", "success", body.Reason, run, before, runView(updated))
		c.JSON(http.StatusAccepted, runView(updated))
	}
}

func handleRetryOperationRun(coordinator RunCoordinator) gin.HandlerFunc {
	return func(c *gin.Context) {
		run, db, ok := operationRun(c)
		if !ok {
			return
		}
		body, ok := bindOperationReason(c)
		if !ok {
			return
		}
		if coordinator == nil {
			recordRunAudit(c, "agent_run.retry", "blocked", body.Reason, run, runView(run), nil)
			c.JSON(http.StatusServiceUnavailable, gin.H{"error": "manual retry requires temporal execution mode"})
			return
		}
		tenantScope := run.TenantID
		if strings.EqualFold(c.GetString("role"), "platform_admin") {
			tenantScope = ""
		}
		dispatch, err := persistence.RetryAgentRunForAdmin(c.Request.Context(), db, run.ID, tenantScope)
		if err != nil {
			recordRunAudit(c, "agent_run.retry", "blocked", body.Reason, run, runView(run), nil)
			c.JSON(http.StatusConflict, gin.H{"error": err.Error()})
			return
		}
		if coordinator != nil {
			ctx, cancel := context.WithTimeout(c.Request.Context(), 15*time.Second)
			err = coordinator.Dispatch(ctx, temporalrun.Input{
				RunID: dispatch.ID, SessionID: dispatch.SessionID, TenantID: dispatch.TenantID,
				UserID: dispatch.UserID, AgentID: dispatch.AgentID, ProjectID: dispatch.ProjectID,
				Message: dispatch.Request.Message, AttachmentIDs: dispatch.Request.AttachmentIDs,
				PlanMode: dispatch.Request.PlanMode, PlanAction: dispatch.Request.PlanAction,
			})
			cancel()
		}
		if err != nil {
			_ = persistence.FailAgentRunSubmission(c.Request.Context(), db, dispatch.ID, err)
			failed, _ := persistence.GetAgentRunForAdmin(c.Request.Context(), db, dispatch.ID, "")
			recordRunAudit(c, "agent_run.retry", "failed", body.Reason, failed, runView(run), runView(failed))
			c.JSON(http.StatusBadGateway, gin.H{"error": err.Error(), "run_id": dispatch.ID})
			return
		}
		created, _ := persistence.GetAgentRunForAdmin(c.Request.Context(), db, dispatch.ID, "")
		recordRunAudit(c, "agent_run.retry", "success", body.Reason, created, runView(run), runView(created))
		responseBody := runView(created)
		responseBody["retry_of_run_id"] = run.ID
		c.JSON(http.StatusCreated, responseBody)
	}
}
