package temporalrun

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
	enumspb "go.temporal.io/api/enums/v1"
	"go.temporal.io/api/serviceerror"
	"go.temporal.io/sdk/activity"
	"go.temporal.io/sdk/client"
	"go.temporal.io/sdk/temporal"
	"go.temporal.io/sdk/workflow"
)

const (
	DefaultTaskQueue     = "lea-agent-runs"
	ExecuteActivityName  = "lea-agent.execute-run"
	FinalizeActivityName = "lea-agent.finalize-run"
	workflowPrefix       = "lea-agent-run/"
	defaultDispatchEvery = 2 * time.Second
	defaultDispatchBatch = 100
	defaultDispatchWait  = 15 * time.Second
)

type Input struct {
	RunID         string
	SessionID     string
	TenantID      string
	UserID        string
	AgentID       int
	Model         string
	ProjectID     *int
	Message       string
	AttachmentIDs []int64
	PlanMode      bool
	PlanAction    string
}

type Coordinator struct {
	client    client.Client
	taskQueue string
}

func Enabled() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv("LEA_AGENT_EXECUTION_MODE")), "temporal")
}

func Dial() (*Coordinator, error) {
	hostPort := strings.TrimSpace(os.Getenv("TEMPORAL_ADDRESS"))
	if hostPort == "" {
		hostPort = "temporal:7233"
	}
	namespace := strings.TrimSpace(os.Getenv("TEMPORAL_NAMESPACE"))
	if namespace == "" {
		namespace = "default"
	}
	taskQueue := strings.TrimSpace(os.Getenv("LEA_AGENT_TEMPORAL_TASK_QUEUE"))
	if taskQueue == "" {
		taskQueue = DefaultTaskQueue
	}
	c, err := client.Dial(client.Options{HostPort: hostPort, Namespace: namespace})
	if err != nil {
		return nil, fmt.Errorf("dial temporal: %w", err)
	}
	return &Coordinator{client: c, taskQueue: taskQueue}, nil
}

func NewCoordinator(c client.Client, taskQueue string) *Coordinator {
	if taskQueue == "" {
		taskQueue = DefaultTaskQueue
	}
	return &Coordinator{client: c, taskQueue: taskQueue}
}

func (c *Coordinator) Dispatch(ctx context.Context, input Input) error {
	if c == nil || c.client == nil {
		return errorsUnavailable()
	}
	db, err := persistence.DB()
	if err != nil {
		return fmt.Errorf("open run store for dispatch: %w", err)
	}
	// 先认领、再起 workflow——不是反过来。dispatchPending 的周期轮询和
	// submitTemporalRun 的内联派发是同一个进程里两条独立的路径，仍然会
	// 并发看到同一行"待派发"。认领是一次原子的条件 UPDATE，只有一条路径能
	// 抢到；没抢到的直接返回 nil（不是错误——对它来说这次没事可做，跟
	// "别人已经派发过了"是同一个结果），不会再去调 StartWorkflowExecution。
	//
	// 反过来做（先起 workflow 再认领，这是这段代码原来的顺序）挡不住这个
	// 竞态：两条路径都能看到"还没派发"，都会去调 StartWorkflowExecution，
	// 虽然 WorkflowIDReusePolicy 挡住了真正建出两个 workflow，但挡不住的是
	// 两次 RPC 本身——而且一旦这个假设以后变了（比如换成允许重复的策略），
	// 顺序错误会直接变成两个真正独立的执行互相打架，就是这次线上事故的
	// 起因。
	claimed, err := persistence.MarkAgentRunDispatched(ctx, db, input.RunID)
	if err != nil {
		return fmt.Errorf("claim run dispatch: %w", err)
	}
	if !claimed {
		return nil
	}
	if err := c.startWorkflow(ctx, input); err != nil {
		// 认领之后真正起 workflow 失败了——退回认领标记，否则这一行永远
		// 卡在"认领了但没有真正起 workflow"这个死角，下一轮 dispatchPending
		// 的 WHERE dispatched_at IS NULL 会把它跳过，谁都不会再重试。
		_ = persistence.UnmarkAgentRunDispatched(ctx, db, input.RunID)
		_ = persistence.RecordAgentRunDispatchFailure(ctx, db, input.RunID, err)
		return err
	}
	return nil
}

func (c *Coordinator) startWorkflow(ctx context.Context, input Input) error {
	_, err := c.client.ExecuteWorkflow(ctx, client.StartWorkflowOptions{
		ID:                    workflowPrefix + input.RunID,
		TaskQueue:             c.taskQueue,
		WorkflowIDReusePolicy: enumspb.WORKFLOW_ID_REUSE_POLICY_REJECT_DUPLICATE,
	}, AgentRunWorkflow, input)
	if err != nil {
		var alreadyStarted *serviceerror.WorkflowExecutionAlreadyStarted
		if errors.As(err, &alreadyStarted) {
			return nil
		}
		return fmt.Errorf("start agent run workflow: %w", err)
	}
	return nil
}

func (c *Coordinator) RunDispatcher(ctx context.Context) {
	c.dispatchPending(ctx)
	ticker := time.NewTicker(durationFromEnv("LEA_AGENT_DISPATCH_INTERVAL", defaultDispatchEvery))
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			c.dispatchPending(ctx)
		}
	}
}

func (c *Coordinator) dispatchPending(ctx context.Context) {
	db, err := persistence.DB()
	if err != nil || db == nil {
		if err != nil {
			log.Printf("agent_run_dispatch_store_failed err=%v", err)
		}
		return
	}
	dispatches, err := persistence.ListPendingAgentRunDispatches(ctx, db, defaultDispatchBatch)
	if err != nil {
		log.Printf("agent_run_dispatch_list_failed err=%v", err)
		return
	}
	for _, dispatch := range dispatches {
		input := Input{
			RunID:         dispatch.ID,
			SessionID:     dispatch.SessionID,
			TenantID:      dispatch.TenantID,
			UserID:        dispatch.UserID,
			AgentID:       dispatch.AgentID,
			Model:         dispatch.Request.Model,
			ProjectID:     dispatch.ProjectID,
			Message:       dispatch.Request.Message,
			AttachmentIDs: dispatch.Request.AttachmentIDs,
			PlanMode:      dispatch.Request.PlanMode,
			PlanAction:    dispatch.Request.PlanAction,
		}
		dispatchCtx, cancel := context.WithTimeout(ctx, defaultDispatchWait)
		err := c.Dispatch(dispatchCtx, input)
		cancel()
		if err != nil {
			log.Printf("agent_run_dispatch_failed run=%s err=%v", dispatch.ID, err)
		}
	}
}

func (c *Coordinator) Cancel(ctx context.Context, runID string) error {
	if c == nil || c.client == nil {
		return errorsUnavailable()
	}
	if err := c.client.CancelWorkflow(ctx, workflowPrefix+runID, ""); err != nil {
		return fmt.Errorf("cancel agent run workflow: %w", err)
	}
	return nil
}

func (c *Coordinator) Close() {
	if c != nil && c.client != nil {
		c.client.Close()
	}
}

func (c *Coordinator) Client() client.Client { return c.client }

func (c *Coordinator) TaskQueue() string { return c.taskQueue }

func errorsUnavailable() error { return fmt.Errorf("temporal coordinator unavailable") }

func AgentRunWorkflow(ctx workflow.Context, input Input) error {
	executeCtx := workflow.WithActivityOptions(ctx, workflow.ActivityOptions{
		ScheduleToCloseTimeout: 24 * time.Hour,
		StartToCloseTimeout:    24 * time.Hour,
		HeartbeatTimeout:       30 * time.Second,
		WaitForCancellation:    true,
		RetryPolicy: &temporal.RetryPolicy{
			InitialInterval: time.Second,
			MaximumAttempts: 3,
		},
	})
	err := workflow.ExecuteActivity(executeCtx, ExecuteActivityName, input).Get(executeCtx, nil)
	if err == nil {
		return nil
	}

	finalizeCtx, _ := workflow.NewDisconnectedContext(ctx)
	finalizeCtx = workflow.WithActivityOptions(finalizeCtx, workflow.ActivityOptions{
		StartToCloseTimeout: time.Minute,
		RetryPolicy: &temporal.RetryPolicy{
			InitialInterval: time.Second,
			MaximumAttempts: 5,
		},
	})
	_ = workflow.ExecuteActivity(finalizeCtx, FinalizeActivityName, FinalizeInput{
		RunID:     input.RunID,
		Cancelled: temporal.IsCanceledError(err),
		Error:     err.Error(),
	}).Get(finalizeCtx, nil)
	return err
}

type Activities struct {
	Manager *session.Manager
}

type FinalizeInput struct {
	RunID     string
	Cancelled bool
	Error     string
}

func (a *Activities) Execute(ctx context.Context, input Input) error {
	if a == nil || a.Manager == nil {
		return errorsUnavailable()
	}
	sess, err := a.Manager.GetOrCreate(ctx, session.Params{
		SessionID: input.SessionID,
		AgentID:   input.AgentID,
		Model:     input.Model,
		TenantID:  input.TenantID,
		UserID:    input.UserID,
		ProjectID: input.ProjectID,
	})
	if err != nil {
		return err
	}
	attempt := int(activity.GetInfo(ctx).Attempt)

	done := make(chan struct{})
	defer close(done)
	go func() {
		ticker := time.NewTicker(10 * time.Second)
		defer ticker.Stop()
		activity.RecordHeartbeat(ctx, input.RunID)
		for {
			select {
			case <-done:
				return
			case <-ctx.Done():
				return
			case <-ticker.C:
				activity.RecordHeartbeat(ctx, input.RunID)
			}
		}
	}()

	err = sess.ExecutePreparedRun(ctx, input.RunID, input.Message, input.AttachmentIDs, session.PlanControl{
		Mode: input.PlanMode, Action: input.PlanAction,
	}, attempt)

	db, dbErr := persistence.DB()
	if dbErr != nil {
		return dbErr
	}
	stateCtx, stateCancel := context.WithTimeout(context.Background(), 5*time.Second)
	run, stateErr := persistence.GetAgentRunByID(stateCtx, db, input.RunID)
	stateCancel()
	if stateErr != nil {
		return stateErr
	}
	switch run.Status {
	case persistence.RunCompleted:
		return nil
	case persistence.RunCancelled:
		return temporal.NewCanceledError("agent run cancelled")
	case persistence.RunFailed, persistence.RunTimedOut:
		message := run.ErrorMessage
		if message == "" && err != nil {
			message = err.Error()
		}
		return temporal.NewNonRetryableApplicationError(message, string(run.Status), err)
	default:
		// ErrCannotClaimRun 不是瞬时故障 —— 它的意思是"已经有更新的 attempt
		// 抢到了，或者这个 run 已经离开 queued/running"。retry 只会拿到一模
		// 一样的判定结果，还会跟"更新的 attempt"继续竞争，制造更多这种失败
		// （见 runs.go 里 ErrCannotClaimRun 的说明）。标成不可重试，交给
		// FinalizeActivity 去收尾，而不是让 RetryPolicy 再排下一轮。
		//
		// 其余错误（心跳超时、worker 进程真的崩了、DB 网络抖动）维持原样
		// 可重试 —— Temporal 靠这个接住 worker 故障，这部分不能一并关掉。
		if err != nil && errors.Is(err, persistence.ErrCannotClaimRun) {
			return temporal.NewNonRetryableApplicationError(err.Error(), "CannotClaimRun", err)
		}
		if err != nil {
			return fmt.Errorf("agent run %s attempt %d interrupted: %v", input.RunID, attempt, err)
		}
		return fmt.Errorf("agent run %s attempt %d ended in non-terminal status %s", input.RunID, attempt, run.Status)
	}
}

func (a *Activities) Finalize(ctx context.Context, input FinalizeInput) error {
	db, err := persistence.DB()
	if err != nil {
		return err
	}
	return persistence.FinalizeAgentRunAfterWorkflow(ctx, db, input.RunID, input.Cancelled, input.Error)
}

func durationFromEnv(key string, fallback time.Duration) time.Duration {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}
	duration, err := time.ParseDuration(value)
	if err != nil || duration <= 0 {
		return fallback
	}
	return duration
}
