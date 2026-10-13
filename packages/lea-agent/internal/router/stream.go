package router

import (
	"errors"
	"log"
	"strconv"
	"time"

	"github.com/gin-contrib/sse"
	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
)

// handleSendStream 发消息并在同一条连接上回 SSE。
//
// 为什么不是 POST /chat/send + GET /chat/stream 两条请求：事件投递是纯进程内的
// （AgentSession.broadcast 只往内存里的 subscriber channel 写），所以那两条请求
// **必须落到同一个 pod** 才能工作。这个要求从来没被写出来，于是多副本部署下的
// 表现是 GET /chat/stream 返 404 —— 一个隐式的会话亲和要求，伪装成 bug。
//
// 一条连接则不需要任何亲和：turn 和事件流共用一条 TCP 连接，物理上就在同一个
// pod。落到哪个副本都对，下一条消息落到别的副本也对 —— 历史由引擎的
// sessions.Backend 从 agent_messages 读回来（星型链路每轮还会额外
// ReloadHistory 一次，见 AgentSession.Send）。这也是 Anthropic / OpenAI 的
// stream API 和本平台另外两条聊天链路（Chat.vue、SuperAgent.vue）的形状。
//
// 注意"不需要亲和"只覆盖事件投递。同一会话被两个 pod 同时跑仍然要挡 ——
// 两个标签页、或者一次客户端重试就够了 —— 那是 internal/lock 的事。
//
// 顺带修掉一个单副本就存在的 bug：老链路 POST 返 202 时 turn 已经在跑，客户端
// 之后才 GET 订阅，而 broadcast 满了就丢、没有 buffer、没有 replay，所以这两步
// 之间的 start 和头几个 delta 是直接丢的。这里 Subscribe 排在 Send 之前。
func handleSendStream(mgr *session.Manager, coordinator RunCoordinator) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req SendRequest
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(400, gin.H{"error": err.Error()})
			return
		}

		sess, err := mgr.GetOrCreate(c.Request.Context(), session.Params{
			SessionID: req.SessionID,
			AgentID:   req.AgentID,
			Model:     req.Model,
			TenantID:  c.GetString("tenant_id"),
			UserID:    c.GetString("user_id"),
			ProjectID: req.ProjectID,
		})
		if err != nil {
			if errors.Is(err, session.ErrForbidden) {
				c.JSON(403, gin.H{"error": "forbidden"})
				return
			}
			// 验不了归属 ≠ 无权。503 让客户端重试，403 会让它以为自己的会话不是
			// 自己的 —— 一次数据库抖动就会变成"我的会话打不开了"。
			if errors.Is(err, session.ErrUnverifiable) {
				c.Header("Retry-After", "5")
				c.JSON(503, gin.H{"error": "cannot verify session ownership, retry shortly"})
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
		sess, err = applyRequestAttachments(c.Request.Context(), mgr, sess, req, c.GetString("tenant_id"), c.GetString("user_id"))
		if err != nil {
			c.JSON(500, gin.H{"error": err.Error()})
			return
		}
		if coordinator != nil {
			handleTemporalSendStream(c, coordinator, sess, req)
			return
		}

		// Subscribe 必须在 Send 之前 —— 见上面关于丢头部事件的说明。
		events := sess.Subscribe()
		defer sess.Unsubscribe(events)

		// Send 排在写响应头之前：一旦写了 200 + event-stream，状态码就定死了，
		// 错误只能以 SSE 事件的形式告知客户端。趁还能返状态码先把它跑掉。
		runID, err := sess.SendWithRun(req.Message, req.AttachmentIDs, req.planControl())
		if err != nil {
			// 这个 agent 是专家团，计划模式在团里跑不起来。400 而不是 500：
			// 请求本身不成立，重试不会变好。
			if errors.Is(err, session.ErrPlanModeUnsupported) {
				c.JSON(400, gin.H{"error": "plan mode is not supported for expert teams"})
				return
			}
			// 这个会话已经有一轮在别的 pod 上跑着（本 pod 自己的上一轮会被 Send
			// 打断，走不到这里）。409 而不是 500 —— 这是可预期的并发。
			if errors.Is(err, session.ErrBusy) {
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
		// 反代（nginx）默认会缓冲上游响应，SSE 会被攒着一次性吐出来，看起来就是
		// "不流式"。这个头让 nginx 关掉该 location 的缓冲。
		c.Writer.Header().Set("X-Accel-Buffering", "no")
		c.Writer.WriteHeader(200)

		// 新会话时客户端在流结束之前就需要 session_id（老链路是从 POST 的 JSON
		// body 里拿的，现在 body 整个是事件流）。走带内事件而不是自定义响应头：
		// 头要靠 Access-Control-Expose-Headers 和代理配置才读得到，而这两者失效
		// 时是静默的 —— 丢了 session_id 等于丢了会话。带内事件走客户端已有的
		// 那条解析路径，没有额外前提。
		c.SSEvent("session", gin.H{"session_id": sess.ID(), "run_id": runID})
		c.Writer.Flush()

		pumpEvents(c, events, func() { onClientGone(sess, req.OnDisconnect) })
	}
}

// keepaliveEvery 是静默多久发一次心跳。
//
// 20s 是照着 nginx 的 proxy_read_timeout 默认值（60s）倒推的：留三次机会，
// 丢一两个包不至于让连接被判死。中间还可能有别的设备（云 LB 的空闲超时常见 60s，
// 有的 4 分钟），所以宁可勤一点 —— 一行注释是 13 字节，代价可以忽略。
const keepaliveEvery = 20 * time.Second

// pumpEvents 把会话事件转成 SSE 写给客户端，直到本轮结束或客户端断开。
func pumpEvents(c *gin.Context, events chan session.SSEEvent, onGone func()) {
	pumpEventsEvery(c, events, keepaliveEvery, onGone)
}

// pumpEventsEvery 是 pumpEvents 的实现，心跳间隔可注入 —— 测试不可能等 20 秒。
//
// 只在见到 start 之后才开始转发。原因是 Send 会掐掉上一轮没收尾的 turn，而被掐
// 的那轮仍会走完 persistTurn 再广播它**自己**的 done —— 我们的订阅者是在 Send
// 之前挂上的，会收到那条 done 并据此关流，症状是"流秒关、没答案"。老 turn 不会
// 再发 start，所以拿 start 当门槛就能把它的尾巴（done、result）挡在外面。
//
// 正常情况下零损失：start 是 turn goroutine 的第一条语句，订阅后的第一个事件。
//
// onGone 以回调传入而不是让本函数持有 *AgentSession：断线策略是唯一用得着会话的
// 地方，而把它摘出去之后这个泵就能拿一个裸 channel 测。
func pumpEventsEvery(c *gin.Context, events chan session.SSEEvent, keepalive time.Duration, onGone func()) {
	started := false

	// 心跳：静默期往连接里写点东西，免得中间的反代/LB 按空闲超时把它掐掉。
	//
	// 没有这个的话症状很误导：一次三分钟的沙箱执行期间流上一个字节都没有，nginx
	// 到 60s 就断连，而 turn 其实还在后台跑完并落库了（onClientGone 默认什么都不
	// 做）—— 用户看到"流断了"，刷新之后答案却在，很容易误判成 turn 挂了。
	//
	// 发的是 SSE **注释行**（": ..."）而不是一个事件：注释是 SSE 规范的一部分，
	// EventSource 和所有标准解析器都会静默丢弃，所以前端一行都不用改。发成
	// `event: ping` 就得让每个客户端都认识它，忘了处理就会当成未知事件报错。
	//
	// 心跳不受 started 门槛约束 —— 等 start 的那段时间同样会超时（Send 到引擎吐
	// 第一个字之间要建会话、注入记忆、拉附件，本身就可能是好几秒）。
	tick := time.NewTicker(keepalive)
	defer tick.Stop()

	for {
		select {
		case ev, ok := <-events:
			if !ok {
				// 会话被 Close（淘汰/清理/删除）把 channel 关了。
				return
			}
			if !started {
				if ev.Type != "start" {
					continue
				}
				started = true
			}
			writeEvent(c, ev)
			// 刚写了真数据，把心跳计时器推后 —— 活跃的流不需要心跳。
			tick.Reset(keepalive)
			// done 在 result/error 之后无条件广播，是本轮的终止信号。
			if ev.Type == "done" {
				return
			}
		case <-tick.C:
			// 写失败（客户端已经走了）不用特别处理：下一轮 select 会从
			// Request.Context().Done() 拿到断开信号，走统一的那条路。
			_, _ = c.Writer.WriteString(": keepalive\n\n")
			c.Writer.Flush()
		case <-c.Request.Context().Done():
			onGone()
			return
		}
	}
}

// writeEvent 把一条会话事件写成 SSE，带上 id: 字段。
//
// 不能用 c.SSEvent —— 它只设 event 和 data，没有 id。而 id 是断线续传的全部依据：
// 客户端把最后收到的 id 通过 ?from= 报回来，服务端从那之后补。没有它的话事件日志
// 写了也没人能用。
//
// Seq == 0 的事件不带 id（未经 broadcast 直接写的那些，比如握手用的 session 事件）——
// 它们不在日志里，给个假 id 会让客户端从一个不存在的位置续传。
func writeEvent(c *gin.Context, ev session.SSEEvent) {
	e := sse.Event{Event: ev.Type, Data: ev.Data}
	if ev.Seq > 0 {
		e.Id = strconv.FormatUint(ev.Seq, 10)
	}
	c.Render(-1, e)
	c.Writer.Flush()
}

// 断线策略的取值。
//
// 做成**每次请求的参数**而不是全局配置，形状照 LangGraph 的 on_disconnect 抄的 ——
// 因为同一个部署里两种需求都真实存在，选任何一个当全局默认都会得罪另一半场景：
// 随手问一句，用户关了标签页就该省下 token；而一个跑三分钟的 PPT 任务，不该因为
// 用户切了个 WiFi 就白跑。让发起方声明意图，比让运维猜一个折中值准确。
const (
	onDisconnectContinue = "continue" // 默认：让 turn 跑完并落库
	onDisconnectCancel   = "cancel"   // 立刻掐掉
)

// shouldCancelOnDisconnect 把参数取值映射成决定。
//
// 抽成纯函数是为了能直接测这张映射表 —— onClientGone 只有"打日志"和"调 Stop"
// 两个副作用，从外面很难观察，而映射错了的后果（把长任务当随手问掐掉）是静默的。
//
// 未知取值按 continue 处理：把"客户端拼错了参数"变成"多烧一点 token"，而不是
// "静默掐掉一个用户正等着的长任务"。默认值要选那个错了也能挽回的方向。
func shouldCancelOnDisconnect(policy string) bool {
	return policy == onDisconnectCancel
}

// onClientGone 处理客户端在 turn 还没跑完时消失（关标签页、断网、LB 空闲超时）。
//
// 注意这一层分不清"关了标签页"和"地铁进隧道"—— 两者在 TCP 上一模一样。所以
// cancel 是有代价的：一次两秒的网络抖动会弄死一个用户正等着的长任务。这也是
// 默认值选 continue 的原因。
//
// continue 是免费的：Send 用的是 context.WithCancel(context.Background())，turn
// 不绑 HTTP 请求，所以它会自己跑完、落库（persistTurn 另开 ctx），用户刷新后从
// GET /sessions/:id/messages 就能拿到回答。
func onClientGone(sess *session.AgentSession, policy string) {
	if shouldCancelOnDisconnect(policy) {
		stopped := sess.Stop()
		log.Printf("sse_client_gone session=%s policy=cancel stopped=%v —— 按请求要求掐断",
			sess.ID(), stopped)
		return
	}
	if policy != "" && policy != onDisconnectContinue {
		log.Printf("unknown on_disconnect=%q session=%s —— 按 continue 处理", policy, sess.ID())
	}
	log.Printf("sse_client_gone session=%s policy=continue —— turn 继续跑到结束并落库，"+
		"客户端刷新后可从 /sessions/:id/messages 取回", sess.ID())
}
