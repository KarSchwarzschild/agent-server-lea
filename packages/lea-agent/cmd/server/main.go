package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/control"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/middleware"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/router"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/runtime"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
)

// drainTimeout 是收到 SIGTERM 后等在跑的 turn 收尾的上限。
//
// 3 分钟是照最长的一类任务估的（带沙箱的 PPT 生成实测几分钟）。等不完的那些 turn
// 结果会丢，所以宁可长一点 —— 代价只是发版慢一点，而丢一个跑了三分钟的答案对用户
// 是实打实的损失。
//
// k8s 那边 terminationGracePeriodSeconds 必须配得**比这个大**（比如 200s），
// 否则 kubelet 会在我们排空完成之前直接 SIGKILL，这段逻辑就等于没写。
const defaultDrainTimeout = 3 * time.Minute

// preStopDelay 是翻掉健康检查之后、真正停止接收新请求之前的等待时间。
//
// 为什么需要这个空档：k8s 摘除 endpoint 不是原子的 —— 探针失败到 kube-proxy/ingress
// 真的不再往这里转发之间有几秒延迟。这段时间里流量还在进来，如果此刻已经关了监听，
// 那些请求收到的是连接被拒，而不是被优雅处理。先让探针红、再等一会儿、才关监听。
//
// 也可以改用 pod spec 里的 preStop: sleep，效果一样；放在进程里的好处是不依赖
// 部署清单配对。
const defaultPreStopDelay = 5 * time.Second

func main() {
	port := os.Getenv("LEA_AGENT_PORT")
	if port == "" {
		port = "8100"
	}

	// 初始化 Redis 和缓存层
	redisAddr := os.Getenv("REDIS_URL")
	if redisAddr == "" {
		redisAddr = "localhost:6379"
	}

	var redisClient *redis.Client
	// 如果是完整的 redis:// URL，使用 ParseURL
	if strings.HasPrefix(redisAddr, "redis://") {
		opts, err := redis.ParseURL(redisAddr)
		if err != nil {
			log.Printf("[cache] Failed to parse REDIS_URL: %v — cache disabled", err)
			redisClient = nil
		} else {
			redisClient = redis.NewClient(opts)
		}
	} else {
		// 否则当作 host:port 处理
		redisClient = redis.NewClient(&redis.Options{
			Addr:         redisAddr,
			Password:     os.Getenv("REDIS_PASSWORD"),
			DB:           0,
			DialTimeout:  5 * time.Second,
			ReadTimeout:  3 * time.Second,
			WriteTimeout: 3 * time.Second,
		})
	}

	// 测试 Redis 连接
	if redisClient != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		if err := redisClient.Ping(ctx).Err(); err != nil {
			log.Printf("[cache] Redis connection failed (addr=%s): %v — cache disabled", redisAddr, err)
			redisClient = nil
		} else {
			log.Printf("[cache] Redis connected at %s", redisAddr)
		}
		cancel()
	}

	// 初始化缓存（Redis 失败时传 nil，缓存层会降级）
	runtime.InitCache(redisClient)

	// 初始化消息缓存
	if db, err := persistence.DB(); err == nil && db != nil {
		cacheLayer := runtime.GetCache()
		router.InitMessageCache(cacheLayer, db, redisClient)
		log.Printf("[cache] Message cache initialized")
	} else {
		log.Printf("[cache] Message cache init skipped (db unavailable)")
	}

	mgr := session.NewManager()

	r := gin.Default()
	r.Use(middleware.Metrics()) // 记录所有 HTTP 请求指标
	r.Use(middleware.CORS())

	api := r.Group("/api")
	api.Use(middleware.JWTAuth())

	// /api/user/agents/* → lea-config:8500/agents/* （用户视角 Agent CRUD）
	api.Any("/user/agents", router.UserAgentsProxy())
	api.Any("/user/agents/*path", router.UserAgentsProxy())
	// /api/user/teams/* → lea-config:8500/teams/* （用户视角专家团 CRUD）
	api.Any("/user/teams", router.UserTeamsProxy())
	api.Any("/user/teams/*path", router.UserTeamsProxy())
	// /api/user/skills/* → lea-config:8500/skills/* （技能市场）
	api.Any("/user/skills", router.UserSkillsProxy())
	api.Any("/user/skills/*path", router.UserSkillsProxy())
	// /api/user/tools/* → lea-config:8500/tools/* （用户动态工具管理）
	api.Any("/user/tools", router.UserToolsProxy())
	api.Any("/user/tools/*path", router.UserToolsProxy())
	// /api/user/mcp-servers/* → lea-config:8500/mcp-servers/*
	//
	// 放开用户私有 MCP server 之后必需：lea-config 不对外暴露，少了这条，「用户能建
	// 自己的 server」在接口上够不着 —— 库和 CRUD 都对，但前端没有路可走。
	api.Any("/user/mcp-servers", router.UserMCPServersProxy())
	api.Any("/user/mcp-servers/*path", router.UserMCPServersProxy())
	// /api/user/mcp-actions/{id}/{test,sync} → lea-tools:8400/admin/mcp-servers/{id}/...
	//
	// 建完 server 要 sync 一次才有工具行，而那个端点在 lea-tools。只映射这两个动作、
	// 不映射整个 /admin/ 前缀 —— 见 UserMCPActionsProxy 的说明。
	api.Any("/user/mcp-actions/*path", router.UserMCPActionsProxy())

	router.RegisterRoutes(api, mgr)
	router.RegisterDebugRoutes(api)

	// draining 一旦置位，健康检查就开始返 503，让 k8s 把本 pod 从 Service
	// endpoints 里摘掉。用 atomic 而不是普通 bool：写在信号处理那条 goroutine，
	// 读在每个请求的 handler 里。
	var draining atomic.Bool

	r.GET("/health", healthHandler(mgr, &draining))

	// Prometheus 监控指标端点（不需要鉴权，供 Prometheus 抓取）
	r.GET("/metrics", gin.WrapH(promhttp.Handler()))

	srv := &http.Server{Addr: ":" + port, Handler: r}

	// NotifyContext 而不是自己开 signal.Notify + channel：它顺带管好了取消传播，
	// 而 stop() 会恢复默认信号行为 —— 排空期间用户再按一次 Ctrl-C / 再来一个
	// SIGTERM 就能强杀，不用干等 drainTimeout。
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	go func() {
		log.Printf("lea-agent starting on :%s", port)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("listen failed: %v", err)
		}
	}()

	<-ctx.Done()
	stop()
	drain(srv, mgr, &draining)
}

// healthHandler 是探针和监控的入口。
//
// 带上驻留会话数和在跑的 turn 数：CCE 的探针靠前者看内存压力（也能确认淘汰真的在
// 工作），排空期间靠后者看还剩几个 turn 没收尾。
//
// draining 置位后返 503 而不是 200 —— 这是让编排层把本 pod 从 Service endpoints
// 里摘掉的唯一手段。返 200 的话新请求会继续被路由进来，而我们正在关监听，那些
// 请求收到的是连接被拒。
func healthHandler(mgr *session.Manager, draining *atomic.Bool) gin.HandlerFunc {
	return func(c *gin.Context) {
		body := gin.H{
			"service":      "lea-agent",
			"sessions":     mgr.Count(),
			"active_turns": mgr.ActiveTurns(),
		}
		if draining.Load() {
			body["status"] = "draining"
			c.JSON(503, body)
			return
		}
		body["status"] = "ok"
		c.JSON(200, body)
	}
}

// drain 是优雅退出的全过程。顺序是有讲究的，每一步都在解决上一步留下的问题：
//
//  1. 翻掉健康检查并等一会儿 —— 让编排层停止往这里转发新流量（见 preStopDelay）。
//  2. srv.Shutdown —— 关监听、等**还挂着连接**的请求跑完。SSE 是长连接，所以这一步
//     天然会等到 turn 广播 done、pumpEvents 返回、连接关闭为止。
//  3. mgr.WaitIdle —— 等客户端已经断开、但仍在后台跑的 turn。它们没有连接可等，
//     第 2 步看不见它们，而它们的答案只存在于还没执行的 persistTurn 里。
//  4. mgr.Close —— 关掉清理协程和所有会话的订阅 channel。
//
// 分布式锁顺带被这套流程修好了：turn 正常收尾会执行 defer handle.Release()，所以
// 排空干净的 pod 不会留下没释放的锁。没有这段逻辑时进程被 SIGKILL，锁要等满 60s
// TTL 才过期，那个会话在此期间发消息一直吃 409。
func drain(srv *http.Server, mgr *session.Manager, draining *atomic.Bool) {
	timeout := durationFromEnv("LEA_AGENT_DRAIN_TIMEOUT", defaultDrainTimeout)
	preStop := durationFromEnv("LEA_AGENT_PRESTOP_DELAY", defaultPreStopDelay)

	log.Printf("shutdown signal received —— 开始排空 active_turns=%d prestop=%s timeout=%s",
		mgr.ActiveTurns(), preStop, timeout)

	draining.Store(true)
	if preStop > 0 {
		time.Sleep(preStop)
	}

	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	// Shutdown 出错（超时）不影响后面 —— 剩下的连接会被强断，但仍要给没挂客户端的
	// turn 一次机会，它们可能马上就落库完了。
	if err := srv.Shutdown(ctx); err != nil {
		log.Printf("http_shutdown_incomplete err=%v (仍在等后台 turn)", err)
	}
	if err := mgr.WaitIdle(ctx); err != nil {
		log.Printf("drain_incomplete err=%v", err)
	} else {
		log.Printf("drain_complete —— 所有 turn 已收尾并落库")
	}

	mgr.Close()
	// 控制面的连接最后关。mgr.Close 已经退订（并等订阅 goroutine 退出），所以这里
	// 只是把 TCP 连接还掉。放在最后是因为排空期间我们仍然想能收到跨 pod 的停止请求 ——
	// 用户看到发版卡着，点一下停止应该管用。
	if err := control.Shared().Close(); err != nil {
		log.Printf("control_bus_close_failed err=%v", err)
	}
	log.Printf("lea-agent stopped")
}

// durationFromEnv 跟 session 包里那个同名函数是一份重复实现 —— 那个是 unexported，
// 而为了两处共用就把它导出，等于把一个内部小工具塞进包的公开面。这里 8 行复制更划算。
func durationFromEnv(key string, def time.Duration) time.Duration {
	v := os.Getenv(key)
	if v == "" {
		return def
	}
	d, err := time.ParseDuration(v)
	if err != nil || d < 0 {
		log.Printf("%s=%q 解析失败，用默认值 %s", key, v, def)
		return def
	}
	return d
}
