package router

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"log"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/redis/go-redis/v9"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/cache"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/temporalrun"
)

// 全局消息缓存实例
var globalMessageCache *persistence.MessageCache

// InitMessageCache 初始化全局消息缓存
func InitMessageCache(cacheLayer *cache.Cache, db *sql.DB, redisClient *redis.Client) {
	globalMessageCache = persistence.NewMessageCache(cacheLayer, db, redisClient)
}

type SendRequest struct {
	SessionID string `json:"session_id"`
	AgentID   int    `json:"agent_id"`
	// Model 是当前会话的临时模型选择，不会修改 Agent 的全局配置。
	Model         string  `json:"model,omitempty"`
	ProjectID     *int    `json:"project_id,omitempty"`
	Message       string  `json:"message"`
	AttachmentIDs []int64 `json:"attachment_ids"`
	// OnDisconnect 决定客户端在 turn 跑完之前消失（关标签页、断网、LB 空闲超时）
	// 时怎么处理："continue"（默认，让它跑完并落库）或 "cancel"（立刻停）。
	// 见 router.onClientGone。
	OnDisconnect string `json:"on_disconnect"`
	// PlanMode 为 true 时进入计划模式：prompt 引导模型只做调研和计划。为 false
	// （含零值）时进入执行阶段，若有已批准的计划则每轮注入到 message 前缀。
	// 不填等价于 false，旧客户端无需改动。
	PlanMode bool `json:"plan_mode"`
	// PlanAction 是用户对**已提交的那份计划**的表态："approve" / "reject"，
	// 或空串（没表态，绝大多数消息都是空）。
	//
	// 它跟 PlanMode 是两个维度，不能互相推断 —— 这个字段存在的全部理由就是：
	// 老实现拿 plan_mode=false 当批准信号，于是用户回一句"算了不用了"也会激活
	// 那份计划。批准必须是用户显式做的动作，前端从 ask_user 的选项按钮发出来。
	PlanAction string `json:"plan_action"`
	// Attachments 是这条会话本轮应该挂载的资源完整清单——**替换语义，不是增量**：
	// 凡是不在这份清单里的旧挂载都会被摘掉（跟 /sessions/:id/attachments 那三个
	// 端点的"逐条增删"是两种语义，服务两种场景，见 persistence.ReplaceSessionAttachments
	// 的说明）。
	//
	// 用指针而不是裸 slice：nil（字段整个没出现）表示这次请求不关心挂载，现有的
	// 原样不动——旧客户端（不知道这个字段的存在）必须走这条路，否则升级后端会
	// 悄悄清空所有人已经挂好的工具。非 nil（哪怕是空数组，代表"清空"）才会真的
	// 触发替换，这个区分只有指针才做得到。
	//
	// 跟 AttachmentIDs（聊天附件，用户上传的文件）完全是两个东西，别混——那个见
	// persistence/attachments.go 文件头的区分说明。
	Attachments *[]AttachmentSpec `json:"attachments,omitempty"`
}

// AttachmentSpec 是 Attachments 字段里的一项。
type AttachmentSpec struct {
	Kind  string `json:"kind"`
	RefID int    `json:"ref_id"`
}

// planControl 把请求里那两个字段收成 session 层的入参。
func (r SendRequest) planControl() session.PlanControl {
	return session.PlanControl{Mode: r.PlanMode, Action: r.PlanAction}
}

// applyRequestAttachments 处理请求里的 Attachments 字段：非 nil 就跟当前挂载
// 集合比一次指纹，不一样才整体替换并让引擎按新集合重建，好让**这一条**正在
// 处理的消息就用得上，不用等到下一条。
//
// 前端约定是每条消息都带上"当前完整清单"（不判断这一轮是否真的改过），所以
// 这里必须自己先判断"有没有变化"再决定要不要动数据库——否则日常聊天的每一条
// 消息都会白白付一次 DELETE+INSERT+Drop+重建的开销。
//
// 跟 handleAttachToSession 那条路径故意让重建推迟到"下一条消息"不一样：这里
// Drop 之后立刻重新 GetOrCreate——Drop 只是把内存里那份引擎对象丢掉，不会自动
// 重建，必须原地拿一次才能拿到按新挂载 build 出来的对象，交给调用方继续往下发
// 这条消息。
//
// req.Attachments 为 nil 时原样返回传入的 sess，什么都不做——这是不知道这个
// 字段的旧客户端会走的分支。
func applyRequestAttachments(
	ctx context.Context, mgr *session.Manager, sess *session.AgentSession,
	req SendRequest, tenantID, userID string,
) (*session.AgentSession, error) {
	if req.Attachments == nil {
		return sess, nil
	}
	db, err := persistence.DB()
	if err != nil {
		return sess, err
	}
	desired := make([]persistence.SessionAttachment, 0, len(*req.Attachments))
	for _, item := range *req.Attachments {
		desired = append(desired, persistence.SessionAttachment{Kind: item.Kind, RefID: item.RefID})
	}
	// 先比指纹再决定要不要真的写——前端每条消息都会带上"当前完整清单"（不管这一轮
	// 有没有新增，见 frontend-lab 的说明），绝大多数消息其实什么都没变。指纹一样
	// 就直接跳过整套 DELETE+INSERT+Drop+重建，不然每条消息都要付这个开销，
	// 日常聊天会被这个功能拖慢。
	//
	// 复用 Fingerprint 而不是另写一套比较：GetOrCreate 自己也靠它判断"要不要
	// 自愈重建"（见 session_attachments.go 顶部注释），两处比较的必须是同一个
	// 函数——用两套不同的比较逻辑，"一样"的定义就可能对不齐（比如大小写、
	// 排序方式），产生"该跳过却没跳过"或者反过来的不一致。
	current, err := persistence.LoadSessionAttachments(ctx, db, tenantID, sess.ID())
	if err != nil {
		return sess, fmt.Errorf("load current session attachments: %w", err)
	}
	if persistence.Fingerprint(current) == persistence.Fingerprint(desired) {
		return sess, nil
	}
	if err := persistence.ReplaceSessionAttachments(ctx, db, tenantID, sess.ID(), userID, desired); err != nil {
		return sess, fmt.Errorf("replace session attachments: %w", err)
	}
	mgr.Drop(sess.ID())
	refreshed, err := mgr.GetOrCreate(ctx, session.Params{
		SessionID: sess.ID(),
		AgentID:   req.AgentID,
		Model:     req.Model,
		TenantID:  tenantID,
		UserID:    userID,
		ProjectID: req.ProjectID,
	})
	if err != nil {
		return sess, fmt.Errorf("rebuild session after attachment replace: %w", err)
	}
	return refreshed, nil
}

func handleSend(mgr *session.Manager, coordinator RunCoordinator) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req SendRequest
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(400, gin.H{"error": err.Error()})
			return
		}

		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		sess, err := mgr.GetOrCreate(c.Request.Context(), session.Params{
			SessionID: req.SessionID,
			AgentID:   req.AgentID,
			Model:     req.Model,
			TenantID:  tenantID,
			UserID:    userID,
			ProjectID: req.ProjectID,
		})
		if err != nil {
			if errors.Is(err, session.ErrForbidden) {
				c.JSON(403, gin.H{"error": "forbidden"})
				return
			}
			// 见 stream.go 同处：验不了 ≠ 无权，必须是 503。
			if errors.Is(err, session.ErrUnverifiable) {
				c.Header("Retry-After", "5")
				c.JSON(503, gin.H{"error": "cannot verify session ownership, retry shortly"})
				return
			}
			// 会话数打满且没有可回收的空闲会话 —— 让客户端稍后重试，
			// 别去挤掉别人正在用的会话。
			if errors.Is(err, session.ErrCapacity) {
				c.Header("Retry-After", "10")
				c.JSON(503, gin.H{"error": "server busy, retry shortly"})
				return
			}
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		sess, err = applyRequestAttachments(c.Request.Context(), mgr, sess, req, tenantID, userID)
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}

		var runID string
		if coordinator != nil {
			runID, err = submitTemporalRun(c.Request.Context(), coordinator, sess, req, tenantID, userID)
		} else {
			runID, err = sess.SendWithRun(req.Message, req.AttachmentIDs, req.planControl())
		}
		if err != nil {
			// plan_mode 被拒（handoff 团、或 agent 的 plan_mode=off）。400 而不是 500：
			// 请求本身不成立，重试不会变好，客户端该做的是别发 plan_mode。
			if errors.Is(err, session.ErrPlanModeUnsupported) {
				c.JSON(400, gin.H{"error": err.Error()})
				return
			}
			// 会话已经被软删（另一个标签页点了"删除"）。410 Gone——这个会话
			// 曾经存在、被明确移除了，不是"从来没有"（404）也不是"这次抖动一下"
			// （503）。code 让前端能专门分支：提示"该对话已被删除"而不是把
			// 一句裸错误糊在用户脸上，参照 log_expired 那个先例。
			if errors.Is(err, persistence.ErrSessionArchived) {
				c.JSON(410, gin.H{"error": "该对话已被删除", "code": "session_archived"})
				return
			}
			// 这个会话已经有一轮在别的 pod 上跑着。409 而不是 500 —— 这是可预期的
			// 并发，客户端该做的是等当前这轮结束（它的 SSE 还在流），而不是立刻重试。
			if errors.Is(err, session.ErrBusy) || errors.Is(err, persistence.ErrActiveRun) {
				c.JSON(409, gin.H{"error": "a turn is already running for this session"})
				return
			}
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}

		c.JSON(202, gin.H{"session_id": sess.ID(), "run_id": runID, "status": "processing"})
	}
}

func submitTemporalRun(ctx context.Context, coordinator RunCoordinator, sess *session.AgentSession, req SendRequest, tenantID, userID string) (string, error) {
	runID, err := sess.SubmitRun(ctx, req.Message, req.AttachmentIDs, req.planControl())
	if err != nil {
		return "", err
	}
	agentID, projectID := sess.ExecutionParams()
	dispatchCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	err = coordinator.Dispatch(dispatchCtx, temporalrun.Input{
		RunID:         runID,
		SessionID:     sess.ID(),
		TenantID:      tenantID,
		UserID:        userID,
		AgentID:       agentID,
		ProjectID:     projectID,
		Message:       req.Message,
		AttachmentIDs: req.AttachmentIDs,
		PlanMode:      req.PlanMode,
		PlanAction:    req.PlanAction,
		Model:         req.Model,
	})
	cancel()
	if err != nil {
		log.Printf("run_initial_dispatch_failed run=%s err=%v (queued for reconciliation)", runID, err)
	}
	return runID, nil
}

// handleStop 掐断该会话正在跑的 turn。
//
// 两条路：会话在本 pod 内存里就直接停；不在就转给持有执行权的那个 pod。
//
// 为什么需要第二条：这是一条**独立的 HTTP 请求**，多副本下会落到任意副本，而
// cancel 函数只存在于跑这一轮的那个进程里。没有转发的话"停止"是抛硬币 —— 命中
// 就停，没命中返 404 而回答继续生成。而且这不是配置粘性路由能解决的：session_id
// 在本请求的 URL 里、在 POST /chat/stream 的 body 里，nginx 没法让两者哈希到
// 同一个后端（它读不了请求体）。
//
// stopped=false 表示这一轮已经自己结束了，属于正常竞态（用户点停的同一瞬间 turn
// 收尾），返 200 不报错。
func handleStop(mgr *session.Manager, coordinator RunCoordinator) gin.HandlerFunc {
	return func(c *gin.Context) {
		sessionID := c.Param("session_id")
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")
		if coordinator != nil {
			handleTemporalStop(c, mgr, coordinator, sessionID, tenantID, userID)
			return
		}

		if sess := mgr.Get(sessionID); sess != nil {
			if !sess.OwnedBy(tenantID, userID) {
				c.JSON(403, gin.H{"error": "forbidden"})
				return
			}
			stopped, err := sess.RequestStop(c.Request.Context())
			if err != nil {
				log.Printf("stop_persist_failed session=%s err=%v", sessionID, err)
			}
			c.JSON(200, gin.H{"session_id": sessionID, "stopped": stopped, "durable": err == nil})
			return
		}

		// 本 pod 没有这个会话。它可能在别的副本上跑着，也可能压根没在跑。
		if !authorizeAbsentSession(c, sessionID, tenantID, userID) {
			return
		}

		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		runID, durable, err := persistence.RequestActiveRunCancellation(c.Request.Context(), db, sessionID)
		if err != nil {
			log.Printf("stop_persist_failed session=%s err=%v", sessionID, err)
			c.JSON(503, gin.H{"error": "cannot persist cancellation request"})
			return
		}

		forwarded, forwardErr := mgr.ForwardStop(c.Request.Context(), sessionID)
		if forwardErr != nil && durable {
			// Pub/Sub 只是低延迟通道；runner 还会轮询数据库中的取消标记。
			log.Printf("stop_forward_failed_but_durable session=%s run=%s err=%v", sessionID, runID, forwardErr)
			c.JSON(202, gin.H{"session_id": sessionID, "run_id": runID, "stopped": true, "durable": true})
			return
		}
		err = forwardErr
		if err != nil {
			// 控制面不通（没配 Redis，或连不上）。返 503 而不是 404 —— 404 会让
			// 前端以为"没什么在跑"，而真相是"我们不知道，也送不出去信号"。
			log.Printf("stop_forward_failed session=%s err=%v", sessionID, err)
			c.JSON(503, gin.H{"error": "cannot reach the runner for this session"})
			return
		}
		if !forwarded && durable {
			c.JSON(202, gin.H{"session_id": sessionID, "run_id": runID, "stopped": true, "durable": true})
			return
		}
		if !forwarded {
			// 全集群都没有在跑的 turn（锁不存在）。这个 404 是有依据的，不是猜的。
			c.JSON(404, gin.H{"error": "session not found"})
			return
		}
		// stopped=true 只表示信号已经发出去了。权威的完成信号是 SSE 流上的
		// result + done —— 那条流就挂在跑这一轮的 pod 上，所以用户一定收得到。
		c.JSON(200, gin.H{"session_id": sessionID, "run_id": runID, "stopped": true, "forwarded": true, "durable": durable})
	}
}

func handleTemporalStop(c *gin.Context, mgr *session.Manager, coordinator RunCoordinator, sessionID, tenantID, userID string) {
	if sess := mgr.Get(sessionID); sess != nil {
		if !sess.OwnedBy(tenantID, userID) {
			c.JSON(403, gin.H{"error": "forbidden"})
			return
		}
	} else if !authorizeAbsentSession(c, sessionID, tenantID, userID) {
		return
	}

	db, err := persistence.DB()
	if err != nil || db == nil {
		c.JSON(503, gin.H{"error": "persistence unavailable"})
		return
	}
	runID, durable, err := persistence.RequestActiveRunCancellation(c.Request.Context(), db, sessionID)
	if err != nil {
		log.Printf("stop_persist_failed session=%s err=%v", sessionID, err)
		c.JSON(503, gin.H{"error": "cannot persist cancellation request"})
		return
	}
	if runID == "" {
		c.JSON(200, gin.H{"session_id": sessionID, "stopped": false, "durable": true})
		return
	}
	if err := coordinator.Cancel(c.Request.Context(), runID); err != nil {
		// MySQL is the durable fallback. A running worker polls the cancellation flag, and a
		// queued run was atomically moved to cancelled before this call.
		log.Printf("temporal_cancel_failed_but_durable session=%s run=%s err=%v", sessionID, runID, err)
	}
	c.JSON(202, gin.H{"session_id": sessionID, "run_id": runID, "stopped": true, "durable": durable})
}

func handleGetRun() gin.HandlerFunc {
	return func(c *gin.Context) {
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		run, err := persistence.GetAgentRun(
			c.Request.Context(), db, c.Param("run_id"), c.GetString("tenant_id"), c.GetString("user_id"),
		)
		if err != nil {
			if errors.Is(err, sql.ErrNoRows) {
				c.JSON(404, gin.H{"error": "run not found"})
				return
			}
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		c.JSON(200, gin.H{
			"run_id": run.ID, "session_id": run.SessionID, "status": run.Status,
			"cancel_requested_at": run.CancelRequestedAt, "started_at": run.StartedAt,
			"finished_at": run.FinishedAt, "error": run.ErrorMessage,
		})
	}
}

// authorizeAbsentSession 校验一个**不在本进程内存里**的会话是否归调用方所有。
// 不通过时它自己写好响应并返回 false，调用方直接 return 即可。
//
// 为什么必须单独有这么一道：AgentSession.OwnedBy 要内存里的会话对象，而多副本下
// 控制类请求（stop、status）经常落在没有这个会话的副本上。少了这道就等于：任何
// 登录用户拿一个 session_id 打到"不持有该会话"的副本，就能掐断别人正在生成的回答、
// 或者探到别人的会话状态。本 pod 那条路有校验、跨 pod 那条没有 —— 这是最容易漏的
// 一类权限缺口，因为功能测试全都会过。
func authorizeAbsentSession(c *gin.Context, sessionID, tenantID, userID string) bool {
	db, err := persistence.DB()
	if err != nil || db == nil {
		// 校验不了就不放行。不能"反正是内网先过去吧"—— 那正是缺口本身。
		c.JSON(503, gin.H{"error": "persistence unavailable"})
		return false
	}
	ownerUser, ownerTenant, err := persistence.GetSessionOwner(c.Request.Context(), db, sessionID)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			c.JSON(404, gin.H{"error": "session not found"})
			return false
		}
		c.JSON(500, gin.H{"error": err.Error()})
		return false
	}
	if ownerUser != userID || ownerTenant != tenantID {
		c.JSON(403, gin.H{"error": "forbidden"})
		return false
	}
	return true
}

// handleTurnStatus 回答"这个会话此刻还在生成吗"，覆盖全集群。
//
// 前端断流之后需要它来决定下一步：running=true 就接着等（或重连流），false 就去
// 拉 /sessions/:id/messages 取最终结果。没有它的话这两种情况在 messages 接口上
// 长得一模一样 —— assistant 消息要等整轮结束才落库，所以"还在写"和"写完了但我
// 没收到"都表现为"列表里没有新消息"。
//
// 这也是 OpenAI 的形状：GET /v1/responses/{id} 返回 status: in_progress / completed。
func handleTurnStatus(mgr *session.Manager, coordinator RunCoordinator) gin.HandlerFunc {
	return func(c *gin.Context) {
		sessionID := c.Param("session_id")
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		// 本 pod 有会话对象就用它校验；没有就查库（多副本下这是常态）。
		if sess := mgr.Get(sessionID); sess != nil {
			if !sess.OwnedBy(tenantID, userID) {
				c.JSON(403, gin.H{"error": "forbidden"})
				return
			}
		} else if !authorizeAbsentSession(c, sessionID, tenantID, userID) {
			return
		}

		if coordinator != nil {
			db, err := persistence.DB()
			if err != nil || db == nil {
				c.JSON(503, gin.H{"error": "persistence unavailable"})
				return
			}
			run, err := persistence.FindActiveAgentRun(c.Request.Context(), db, sessionID)
			if errors.Is(err, sql.ErrNoRows) {
				c.JSON(200, gin.H{"session_id": sessionID, "running": false})
				return
			}
			if err != nil {
				c.JSON(503, gin.H{"error": "cannot determine turn status"})
				return
			}
			c.JSON(200, gin.H{"session_id": sessionID, "run_id": run.ID, "status": run.Status, "running": true})
			return
		}

		running, err := mgr.IsTurnRunning(c.Request.Context(), sessionID)
		if err != nil {
			// 查不到就如实说查不到。猜 false 会让前端过早放弃、丢掉正在生成的答案；
			// 猜 true 会让它对着一个不存在的 turn 死等。
			log.Printf("turn_status_failed session=%s err=%v", sessionID, err)
			c.JSON(503, gin.H{"error": "cannot determine turn status"})
			return
		}
		c.JSON(200, gin.H{"session_id": sessionID, "running": running})
	}
}

// handleCreateSession 建会话。
//
// ## agent_id 是可选的
//
// 不传 = 用租户的 super-agent（lea-config 的 _resolve_super_agent 在 agentID=0 时兜底）。
// 这条路是"用户进来直接开聊"的前提：先有一个能对话的会话，再靠 @ 往里挂 skill/tool/
// mcp_server/kb —— 而不是逼用户先选一个 agent 才看得到输入框。
//
// 指针而不是 int：0 和"没传"必须分得开。将来若要让 0 表示别的意思（比如显式拒绝兜底），
// 有了这个区分才改得动；用零值的话那个语义位已经被占掉了。
//
// 空 body 也接受 —— POST 一个没有 body 的请求是这条路最自然的调用形状，而
// ShouldBindJSON 对空 body 返回 EOF。不放过它的话"直接开聊"仍然要求前端记得发 `{}`。
func handleCreateSession(mgr *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req struct {
			AgentID   *int   `json:"agent_id,omitempty"`
			ProjectID *int   `json:"project_id,omitempty"`
			Model     string `json:"model,omitempty"`
		}
		// EOF = 没有 body，等价于 {}。其余解析错误仍然是 400：那是**格式坏了**，
		// 静默当成缺省会把一个拼错的字段名变成"悄悄用了 super-agent"。
		if err := c.ShouldBindJSON(&req); err != nil && !errors.Is(err, io.EOF) {
			c.JSON(400, gin.H{"error": err.Error()})
			return
		}
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		agentID := 0
		if req.AgentID != nil {
			agentID = *req.AgentID
		}
		if req.ProjectID != nil {
			db, dbErr := persistence.DB()
			if dbErr != nil || db == nil {
				c.JSON(503, gin.H{"error": "persistence unavailable"})
				return
			}
			owned, err := persistence.ProjectOwnedBy(c.Request.Context(), db, *req.ProjectID, tenantID, userID)
			if err != nil {
				c.JSON(500, gin.H{"error": err.Error()})
				return
			}
			if !owned {
				c.JSON(404, gin.H{"error": "project not found"})
				return
			}
		}

		sess, err := mgr.GetOrCreate(c.Request.Context(), session.Params{
			AgentID:   agentID,
			Model:     req.Model,
			TenantID:  tenantID,
			UserID:    userID,
			ProjectID: req.ProjectID,
		})
		if err != nil {
			// 这条不传 SessionID，走不到复用分支，理论上碰不到 ErrForbidden；
			// 仍然映射一下，免得将来改了 GetOrCreate 这里静默变成 500。
			if errors.Is(err, session.ErrForbidden) {
				c.JSON(403, gin.H{"error": "forbidden"})
				return
			}
			if errors.Is(err, session.ErrCapacity) {
				c.Header("Retry-After", "10")
				c.JSON(503, gin.H{"error": "server busy, retry shortly"})
				return
			}
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		c.JSON(201, gin.H{"session_id": sess.ID()})
	}
}

// handleListSessions 读 DB 返回归属该 tenant + user 的未归档会话。
// 老的 mgr.ListByUser 只看进程内存，重启就丢，H5 改成读库为准。
func handleListSessions(_ *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(200, gin.H{"sessions": []any{}})
			return
		}
		rows, err := persistence.ListSessions(c.Request.Context(), db, tenantID, userID, 50, 0)
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		out := make([]gin.H, 0, len(rows))
		for _, r := range rows {
			out = append(out, gin.H{
				"id":             r.ID,
				"title":          r.Title,
				"agent_id":       r.AgentID,
				"model":          r.Model,
				"project_id":     r.ProjectID,
				"message_count":  r.MessageCount,
				"last_active_at": r.LastActiveAt,
				"created_at":     r.CreatedAt,
				"is_archived":    r.IsArchived,
			})
		}
		c.JSON(200, gin.H{"sessions": out})
	}
}

func handleListProjects() gin.HandlerFunc {
	return func(c *gin.Context) {
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		items, err := persistence.ListProjects(c.Request.Context(), db, c.GetString("tenant_id"), c.GetString("user_id"))
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		c.JSON(200, gin.H{"projects": items})
	}
}

func handleCreateProject() gin.HandlerFunc {
	return func(c *gin.Context) {
		var req struct {
			Name        string `json:"name" binding:"required"`
			Description string `json:"description"`
		}
		if err := c.ShouldBindJSON(&req); err != nil || strings.TrimSpace(req.Name) == "" {
			c.JSON(400, gin.H{"error": "project name required"})
			return
		}
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		project, err := persistence.CreateProject(c.Request.Context(), db, c.GetString("tenant_id"), c.GetString("user_id"), strings.TrimSpace(req.Name), strings.TrimSpace(req.Description))
		if err != nil {
			c.JSON(409, gin.H{"error": "project name already exists"})
			return
		}
		c.JSON(201, project)
	}
}

func handleUpdateProject() gin.HandlerFunc {
	return func(c *gin.Context) {
		id, err := strconv.Atoi(c.Param("id"))
		if err != nil {
			c.JSON(400, gin.H{"error": "invalid project id"})
			return
		}
		var req struct {
			Name        string `json:"name" binding:"required"`
			Description string `json:"description"`
		}
		if err := c.ShouldBindJSON(&req); err != nil || strings.TrimSpace(req.Name) == "" {
			c.JSON(400, gin.H{"error": "project name required"})
			return
		}
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		project, err := persistence.UpdateProject(c.Request.Context(), db, id, c.GetString("tenant_id"), c.GetString("user_id"), strings.TrimSpace(req.Name), strings.TrimSpace(req.Description))
		if errors.Is(err, sql.ErrNoRows) {
			c.JSON(404, gin.H{"error": "project not found"})
			return
		}
		if err != nil {
			c.JSON(409, gin.H{"error": "project name already exists"})
			return
		}
		c.JSON(200, project)
	}
}

func handleArchiveProject() gin.HandlerFunc {
	return func(c *gin.Context) {
		id, err := strconv.Atoi(c.Param("id"))
		if err != nil {
			c.JSON(400, gin.H{"error": "invalid project id"})
			return
		}
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		err = persistence.ArchiveProject(c.Request.Context(), db, id, c.GetString("tenant_id"), c.GetString("user_id"))
		if errors.Is(err, sql.ErrNoRows) {
			c.JSON(404, gin.H{"error": "project not found"})
			return
		}
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		c.JSON(200, gin.H{"id": id, "is_archived": true})
	}
}

func handleDeleteProject() gin.HandlerFunc {
	return func(c *gin.Context) {
		id, err := strconv.Atoi(c.Param("id"))
		if err != nil {
			c.JSON(400, gin.H{"error": "invalid project id"})
			return
		}
		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}
		err = persistence.DeleteProject(c.Request.Context(), db, id, c.GetString("tenant_id"), c.GetString("user_id"))
		if errors.Is(err, sql.ErrNoRows) {
			c.JSON(404, gin.H{"error": "project not found"})
			return
		}
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		c.JSON(200, gin.H{"ok": true})
	}
}

// handleGetMessages 拉某会话的历史消息，先校验归属再读。
func handleGetMessages(_ *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		sessionID := c.Param("id")
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		db, err := persistence.DB()
		if err != nil || db == nil {
			c.JSON(503, gin.H{"error": "persistence unavailable"})
			return
		}

		ownerUser, ownerTenant, err := persistence.GetSessionOwner(c.Request.Context(), db, sessionID)
		if err != nil {
			if errors.Is(err, sql.ErrNoRows) {
				c.JSON(404, gin.H{"error": "session not found"})
				return
			}
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		if ownerUser != userID || ownerTenant != tenantID {
			c.JSON(403, gin.H{"error": "forbidden"})
			return
		}

		// 使用消息缓存（如果可用）
		var msgs []persistence.MessageRow
		if globalMessageCache != nil {
			msgs, err = globalMessageCache.GetSessionMessages(c.Request.Context(), sessionID)
		} else {
			msgs, err = persistence.GetMessages(c.Request.Context(), db, sessionID)
		}
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		out := make([]gin.H, 0, len(msgs))
		for _, m := range msgs {
			row := gin.H{
				"id":         m.ID,
				"role":       m.Role,
				"content":    m.Content,
				"created_at": m.CreatedAt,
			}
			if len(m.Extra) > 0 {
				row["extra"] = m.Extra
			}
			out = append(out, row)
		}
		c.JSON(200, gin.H{"session_id": sessionID, "messages": out})
	}
}

// handleDeleteSession 走软删（ArchiveSession）。找不到或不属于当前用户 → 404。
func handleDeleteSession(mgr *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		id := c.Param("id")
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		db, err := persistence.DB()
		if err == nil && db != nil {
			if err := persistence.ArchiveSession(c.Request.Context(), db, id, userID, tenantID); err != nil {
				if errors.Is(err, sql.ErrNoRows) {
					c.JSON(404, gin.H{"error": "session not found"})
					return
				}
				log.Printf("archive_session_failed session=%s err=%v", id, err)
				c.JSON(500, gin.H{"error": err.Error()})
				return
			}
		}
		// 内存里的 session 也顺手清一下（若有）
		mgr.Delete(id)
		c.JSON(200, gin.H{"ok": true})
	}
}
