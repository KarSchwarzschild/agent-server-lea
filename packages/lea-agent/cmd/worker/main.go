package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

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
