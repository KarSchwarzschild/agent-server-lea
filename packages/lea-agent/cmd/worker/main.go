package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/eventlog"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/persistence"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/runtime"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/session"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/temporalrun"
	sdkactivity "go.temporal.io/sdk/activity"
	"go.temporal.io/sdk/worker"
)

func main() {
	if err := initCache(); err != nil {
		log.Fatalf("redis init failed: %v", err)
	}
	if !eventlog.Shared().Enabled() {
		log.Fatal("temporal worker requires REDIS_URL for distributed locks and SSE event delivery")
	}
	coordinator, err := temporalrun.Dial()
	if err != nil {
		log.Fatalf("temporal init failed: %v", err)
	}
	defer coordinator.Close()

	// 指标 + 健康检查端口。
	//
	// 这个进程在这之前**完全没有可观测性**：不监听任何端口，compose 里
	// healthcheck 也是 disable。于是它卡死（Temporal 连接断了但进程没退）的表现
	// 是容器 Running、日志不滚、用户消息一直没回复，而面板全绿。
	//
	// temporalrun.Dial() 已经给客户端挂了 MetricsHandler，所以 SDK 的 temporal_*
	// 指标此刻正在往默认 registry 里写；这里只是把 registry 暴露出去 + 给
	// healthcheck 一个能打的端点。
	startObservabilityServer()

	mgr := session.NewManager()
	defer mgr.Close()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	// 不在这里起 RunDispatcher——agent（cmd/server）已经在跑一份，这里再起一份
	// 就是两个进程同时轮询同一批"待派发"的行，谁先抢到谁派发，中间完全没有
	// 互斥（ListPendingAgentRunDispatches 就是一条裸 SELECT）。两边都在跑的
	// 后果不是"派发快一点"，是同一条消息真的被派发成两个独立的 workflow——
	// 一个抢到 attempt 1 顺利跑完，另一个抢 attempt 2 判定过期直接失败，
	// 用户看到的是随机哪个先把 SSE 流接到手上。派发这件事离 HTTP 请求越近
	// 越该归 agent 管，worker 只管干活，不管抢单。

	w := worker.New(coordinator.Client(), coordinator.TaskQueue(), worker.Options{
		WorkerStopTimeout: durationFromEnv("LEA_AGENT_DRAIN_TIMEOUT", 3*time.Minute),
	})
	w.RegisterWorkflow(temporalrun.AgentRunWorkflow)
	activities := &temporalrun.Activities{Manager: mgr}
	w.RegisterActivityWithOptions(activities.Execute, sdkactivity.RegisterOptions{Name: temporalrun.ExecuteActivityName})
	w.RegisterActivityWithOptions(activities.Finalize, sdkactivity.RegisterOptions{Name: temporalrun.FinalizeActivityName})
	if err := w.Start(); err != nil {
		log.Fatalf("temporal worker start failed: %v", err)
	}
	log.Printf("lea-agent-worker started task_queue=%s", coordinator.TaskQueue())

	<-ctx.Done()
	stop()
	log.Printf("lea-agent-worker stopping")
	w.Stop()
}

// startObservabilityServer 起一个只有 /metrics 和 /health 的 HTTP server。
//
// 端口默认 9109（网关的指标口是 9108，错开一位好认）。这个口**不需要发布到宿主**
// —— Prometheus 在同一个 compose 网络里，docker 的 healthcheck 也是在容器内执行。
//
// /health 只回 200，不去 ping Temporal。为什么够用：worker 的失败形态是整个
// 进程卡住（goroutine 全阻塞在某处），那时这个 handler 也响应不了，探针一样会
// 失败。反过来如果只是 Temporal 临时抖一下，不该把容器判成 unhealthy 然后重启
// —— SDK 自己会重连，重启只会把正在跑的 activity 全打断。
//
// "worker 接不上 Temporal"这件事由指标回答（temporal_poll_no_task、
// temporal_request_failure 那几条），不该由健康检查回答。
func startObservabilityServer() {
	port := env("LEA_AGENT_WORKER_METRICS_PORT", "9109")
	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.Handler())
	mux.HandleFunc("/health", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"ok","service":"lea-agent-worker"}`))
	})
	server := &http.Server{
		Addr:              ":" + port,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}
	go func() {
		log.Printf("lea-agent-worker observability listening on :%s", port)
		// 这个口起不来不该拖垮 worker：活照干，只是没指标。所以是 Printf 不是 Fatal。
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Printf("lea-agent-worker observability server stopped: %v", err)
		}
	}()
}

func env(key, fallback string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return fallback
}

func initCache() error {
	redisAddr := strings.TrimSpace(os.Getenv("REDIS_URL"))
	if redisAddr == "" {
		return fmt.Errorf("REDIS_URL is required")
	}
	var redisClient *redis.Client
	if strings.HasPrefix(redisAddr, "redis://") {
		opts, err := redis.ParseURL(redisAddr)
		if err != nil {
			return fmt.Errorf("parse REDIS_URL: %w", err)
		}
		redisClient = redis.NewClient(opts)
	} else {
		redisClient = redis.NewClient(&redis.Options{Addr: redisAddr})
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := redisClient.Ping(ctx).Err(); err != nil {
		_ = redisClient.Close()
		return fmt.Errorf("ping redis: %w", err)
	}
	runtime.InitCache(redisClient)
	// 子轮快照。引擎在每一步之后把整条工作历史镜像到这里，重试时优先从它接手，
	// 而不是回落到 checkpoint 边界重跑一批已经生效的工具。
	persistence.SetInflightStore(redisClient)
	return nil
}

func durationFromEnv(key string, fallback time.Duration) time.Duration {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}
	duration, err := time.ParseDuration(value)
	if err != nil || duration < 0 {
		log.Printf("%s=%q invalid, using %s", key, value, fallback)
		return fallback
	}
	return duration
}
