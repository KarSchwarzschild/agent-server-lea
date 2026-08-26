package router

import (
	"errors"
	"log"
	"strconv"
	"time"

	"github.com/gin-contrib/sse"
	"github.com/gin-gonic/gin"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/eventlog"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
)

// resumeBlock 是一次 XREAD 的阻塞时长。到期就发一次心跳再接着等 ——
// 跟直播链路同一个理由（见 stream.go 的 keepaliveEvery）。
const resumeBlock = 20 * time.Second

// handleStreamResume 从事件日志续传一个会话的事件流。
//
// ── 它解决什么 ──────────────────────────────────────────────
//
// 主链路 POST /chat/stream 把发消息和收流合并成一条连接，代价是断了就没法重连
// （重发一个 POST 会真的再问一遍）。所以这条 GET 是**唯一的重连入口**：客户端把
// 最后收到的 SSE id 通过 ?from= 报回来，服务端补上之后的，再接上直播。
//
// ── 为什么改成读日志，而不是继续读内存 ──────────────────────
//
// 老实现挂在 AgentSession 的内存订阅上，于是有三个问题，全都只在多副本下暴露：
//
//  1. 必须同副本 —— POST 落在 A、GET 落在 B 的话，B 内存里没这个会话，返 404。
//     轮询负载均衡下大约一半概率命中。
//  2. 没有回放 —— broadcast 对没有订阅者的事件直接丢弃，所以"先发消息后连流"
//     会丢掉 start 和开头几个 delta。文档里那句"必须先连 SSE 再 POST"就是它。
//  3. 重启即失 —— 会话在内存，pod 一换就没了。
//
// 读 Redis 之后三个一起没了：日志跟 pod 无关，从头补也没问题，而且 pod 换了流还在。
//
// 未启用日志（没配 REDIS_URL）时退回内存订阅 —— 单实例部署下那条路是正确的，
// 而且是今天线上跑的那条，不能因为多副本的需求把它弄坏。
func handleStreamResume(mgr *session.Manager) gin.HandlerFunc {
	return func(c *gin.Context) {
		sessionID := c.Param("session_id")
		tenantID := c.GetString("tenant_id")
		userID := c.GetString("user_id")

		// 归属校验：本 pod 有会话对象就用它，没有就查库（多副本下这是常态）。
		// 少了这道就等于任何登录用户拿一个 session_id 就能实时读走别人的对话。
		sess := mgr.Get(sessionID)
		if sess != nil {
			if !sess.OwnedBy(tenantID, userID) {
				c.JSON(403, gin.H{"error": "forbidden"})
				return
			}
		} else if !authorizeAbsentSession(c, sessionID, tenantID, userID) {
			return
		}

		logs := eventlog.Shared()
		if !logs.Enabled() {
			// 单实例：内存订阅是唯一的真相来源，也是今天线上的行为。
			if sess == nil {
				c.JSON(404, gin.H{"error": "session not found"})
				return
			}
			streamFromMemory(c, sess)
			return
		}

		from, err := parseFrom(c.Query("from"))
		if err != nil {
			c.JSON(400, gin.H{"error": err.Error()})
			return
		}

		// 先探一次：日志不存在就别写 200 —— 写了响应头之后状态码就定死了，
		// 客户端只能从事件里猜发生了什么。趁还能返状态码把这种情况分出去。
		first, err := logs.Range(c.Request.Context(), sessionID, from)
		if err != nil {
			if errors.Is(err, eventlog.ErrNoLog) {
				// 日志过期、从没写过、或者写的时候降级删了。让客户端退回拉历史 ——
				// 用一个专门的 code，别让它跟"会话不存在"混在一起，两者该做的事不同。
				c.JSON(410, gin.H{
					"error": "event log unavailable for this session",
					"code":  "log_expired",
					"hint":  "fall back to GET /api/sessions/:id/messages",
				})
				return
			}
			log.Printf("resume_range_failed session=%s from=%d err=%v", sessionID, from, err)
			c.JSON(503, gin.H{"error": "cannot read event log"})
			return
		}

		c.Writer.Header().Set("Content-Type", "text/event-stream")
		c.Writer.Header().Set("Cache-Control", "no-cache")
		c.Writer.Header().Set("Connection", "keep-alive")
		c.Writer.Header().Set("X-Accel-Buffering", "no")
		c.Writer.WriteHeader(200)

		cursor := from
		if done := replay(c, first, &cursor); done {
			return
		}
		tail(c, logs, sessionID, &cursor)
	}
}

// parseFrom 解析续传游标。空串 = 从头补。
func parseFrom(v string) (uint64, error) {
	if v == "" {
		return 0, nil
	}
	n, err := strconv.ParseUint(v, 10, 64)
	if err != nil {
		return 0, errors.New("from 必须是非负整数（上一条 SSE 事件的 id）")
	}
	return n, nil
}

// replay 把日志里的条目写出去，返回是否已经见到本轮的 done。
func replay(c *gin.Context, entries []eventlog.Entry, cursor *uint64) bool {
	for _, e := range entries {
		// Data 是入库前渲染好的字符串，这里当字符串交回 gin —— 它会原样写出，
		// 于是重放跟直播逐字节一致（见 session.renderSSEData）。
		c.Render(-1, sse.Event{Id: strconv.FormatUint(e.Seq, 10), Event: e.Type, Data: e.Data})
		c.Writer.Flush()
		*cursor = e.Seq
		if e.Type == "done" {
			return true
		}
	}
	return false
}

// tail 接上直播：阻塞等新条目，直到本轮 done 或客户端断开。
func tail(c *gin.Context, logs *eventlog.Log, sessionID string, cursor *uint64) {
	for {
		if c.Request.Context().Err() != nil {
			return
		}
		entries, err := logs.Wait(c.Request.Context(), sessionID, *cursor, resumeBlock)
		if err != nil {
			if c.Request.Context().Err() == nil {
				log.Printf("resume_tail_failed session=%s cursor=%d err=%v", sessionID, *cursor, err)
			}
			return
		}
		if len(entries) == 0 {
			// 阻塞到期，什么都没来。发一行 SSE 注释免得中间设备按空闲超时掐连接
			// （跟直播链路同理，见 stream.go）。
			if _, err := c.Writer.WriteString(": keepalive\n\n"); err != nil {
				return
			}
			c.Writer.Flush()
			continue
		}
		if replay(c, entries, cursor) {
			return
		}
	}
}

// streamFromMemory 是未启用事件日志时的退路：挂内存订阅，跟接日志之前一样。
//
// 保留它不是为了兼容，而是因为单实例部署下它是**正确**的实现 —— 会话就在本进程，
// 没有跨 pod 问题，也不需要为了重连去多存一份事件。
//
// 局限照旧：没有回放（晚连会丢开头）、重启即失、订阅缓冲 128 条满了就丢事件。
func streamFromMemory(c *gin.Context, sess *session.AgentSession) {
	c.Writer.Header().Set("Content-Type", "text/event-stream")
	c.Writer.Header().Set("Cache-Control", "no-cache")
	c.Writer.Header().Set("Connection", "keep-alive")
	c.Writer.Header().Set("X-Accel-Buffering", "no")
	c.Writer.Flush()

	events := sess.Subscribe()
	defer sess.Unsubscribe(events)

	tick := time.NewTicker(keepaliveEvery)
	defer tick.Stop()

	for {
		select {
		case ev, ok := <-events:
			if !ok {
				return
			}
			writeEvent(c, ev)
			tick.Reset(keepaliveEvery)
		case <-tick.C:
			_, _ = c.Writer.WriteString(": keepalive\n\n")
			c.Writer.Flush()
		case <-c.Request.Context().Done():
			return
		}
	}
}
