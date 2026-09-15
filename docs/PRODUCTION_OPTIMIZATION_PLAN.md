# 生产优化实施计划

## 概述（2026-09-14 更新）

这份文档整理从当前代码到生产就绪的实施路径，分为 **P0 立即执行** 和 **P1 Beta 阶段** 两个批次。

**目标**：
- 支持 1000+ 并发用户
- 防止缓存雪崩、击穿、穿透
- 防止全表扫描拖慢查询
- 防止 API 滥用打挂服务
- 异步处理长任务，不卡主链路

**时间估计**：
- P0（上线前）：2-3 天
- P1（Beta 阶段）：1-2 周

---

## P0：立即执行（上线前 2-3 天）

### 1. 数据库索引 ✅（已完成迁移文件）

**影响**：3 个高频全表扫描

**迁移文件**：
- `migrations/029_critical_query_indexes.sql` — P0（会话列表、挂载、附件）
- `migrations/030_secondary_query_indexes.sql` — P1（staged_path 反查、成品列表）

**执行方法**：
```bash
mysql -h 202.205.160.9 -P 3307 -u root -p'Ouchn_huiyan@2024' lea_platform < migrations/029_critical_query_indexes.sql

# 验证索引
mysql -h 202.205.160.9 -P 3307 -u root -p'Ouchn_huiyan@2024' lea_platform -e "
  SHOW INDEX FROM chat_sessions WHERE Key_name='idx_sessions_list';
  SHOW INDEX FROM session_attachments WHERE Key_name='idx_session';
  SHOW INDEX FROM chat_attachments WHERE Key_name='idx_attachment_session';
"
```

**时间**：5 分钟

---

### 2. Nginx 限流配置

**影响**：防止单用户 API 暴击打挂全站

**配置文件**：`infra/nginx/lea-platform.conf`（新建或修改）

```nginx
# 限流区域定义（放在 http 块）
limit_req_zone $http_x_user_id zone=per_user:10m rate=10r/s;
limit_req_zone $http_x_tenant_id zone=per_tenant:10m rate=50r/s;
limit_req_zone $binary_remote_addr zone=global:10m rate=500r/s;

server {
    listen 80;
    server_name lea-platform.example.com;
    
    location /api/ {
        # 应用限流规则
        limit_req zone=per_user burst=20 nodelay;
        limit_req zone=per_tenant burst=100 nodelay;
        limit_req zone=global burst=1000 nodelay;
        
        # 超限返回 429
        limit_req_status 429;
        
        # 反代到后端
        proxy_pass http://backend;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

**细粒度限流**（可选，Beta 阶段加）：
```nginx
# 文档上传限流（1 req/10s）
location ~ ^/api/kb/[^/]+/upload$ {
    limit_req_zone $http_x_user_id zone=upload:10m rate=6r/m;
    limit_req zone=upload burst=1 nodelay;
    proxy_pass http://backend;
}

# KB 检索限流（5 req/s）
location /api/kb/search {
    limit_req_zone $http_x_user_id zone=search:10m rate=5r/s;
    limit_req zone=search burst=10 nodelay;
    proxy_pass http://backend;
}
```

**时间**：30 分钟

---

### 3. 降级开关与防御性编码

**影响**：KB/记忆失败不阻断对话主链路

#### 3.1 环境变量定义

`infra/.env.agent.example`（lea-agent）：
```bash
# 降级开关
ENABLE_KB_SEARCH=true
ENABLE_MEMORY_SEARCH=true
ENABLE_EXTERNAL_TOOLS=true

# 超时配置
KB_SEARCH_TIMEOUT=5s
MEMORY_SEARCH_TIMEOUT=3s
TOOL_EXECUTION_TIMEOUT=30s
```

#### 3.2 Gateway 降级逻辑

`packages/lea-agent/internal/runtime/gateway.go`（修改现有代码）：

```go
// SearchKB 知识库检索（降级保护）
func (g *Gateway) SearchKB(ctx context.Context, query string, kbID string) ([]KBResult, error) {
	// 检查降级开关
	if !g.config.EnableKBSearch {
		log.Printf("KB search disabled by config")
		return []KBResult{}, nil
	}
	
	// 超时控制
	ctx, cancel := context.WithTimeout(ctx, g.config.KBSearchTimeout)
	defer cancel()
	
	// 调用 lea-rag
	resp, err := g.client.Post(ctx, "/kb/search", map[string]any{
		"kb_id": kbID,
		"query": query,
		"limit": 10,
	})
	
	if err != nil {
		// 静默降级，不阻断对话
		log.Printf("KB search degraded: %v", err)
		return []KBResult{}, nil
	}
	
	return resp.Results, nil
}

// SearchMemory 记忆检索（降级保护）
func (g *Gateway) SearchMemory(ctx context.Context, agentID int, query string) ([]Memory, error) {
	if !g.config.EnableMemorySearch {
		return []Memory{}, nil
	}
	
	ctx, cancel := context.WithTimeout(ctx, g.config.MemorySearchTimeout)
	defer cancel()
	
	resp, err := g.client.Post(ctx, "/memory/search", map[string]any{
		"agent_id": agentID,
		"query":    query,
		"limit":    5,
	})
	
	if err != nil {
		log.Printf("Memory search degraded: %v", err)
		return []Memory{}, nil  // 静默降级
	}
	
	return resp.Memories, nil
}
```

#### 3.3 lea-rag 降级逻辑

`packages/lea-rag/src/api/kb.py`（修改）：

```python
import os
from fastapi import HTTPException

ENABLE_KB_SEARCH = os.getenv("ENABLE_KB_SEARCH", "true").lower() == "true"

@router.post("/kb/search")
async def search_kb(kb_id: str, query: str, limit: int = 10):
    if not ENABLE_KB_SEARCH:
        return {"results": [], "degraded": True}
    
    try:
        results = await vector_db.search(kb_id, query, limit)
        return {"results": results, "degraded": False}
    except Exception as e:
        logger.error(f"KB search failed: {e}", exc_info=True)
        # 静默降级，不抛 500
        return {"results": [], "degraded": True}
```

**时间**：1 天

---

### 4. 缓存层集成

**影响**：降低 80% DB 查询

#### 4.1 cache.go 库 ✅（已完成）

位置：`packages/lea-agent/internal/cache/cache.go`

功能：
- 雪崩防护（随机 TTL）
- 击穿防护（分布式锁）
- 穿透防护（NULL 标记）
- 降级保护（Redis 失败回源）

#### 4.2 集成进 lea-agent

`packages/lea-agent/internal/runtime/config_loader.go`（修改）：

```go
package runtime

import (
	"context"
	"fmt"
	"sync"
	
	"lea-agent/internal/cache"
	"github.com/redis/go-redis/v9"
)

var (
	globalCache     *cache.Cache
	globalCacheOnce sync.Once
)

// initCache 初始化全局缓存（单例）
func initCache(redisClient *redis.Client) *cache.Cache {
	globalCacheOnce.Do(func() {
		globalCache = cache.New(redisClient, cache.DefaultConfig)
	})
	return globalCache
}

// LoadAgentConfig 加载 agent 配置（带缓存）
func LoadAgentConfig(ctx context.Context, agentID int, tenantID string) (*AgentConfig, error) {
	// 初始化缓存（首次调用）
	if globalCache == nil {
		// 这里需要从 main.go 传入 redisClient
		// 暂时降级到无缓存
		return loadAgentConfigFromDB(ctx, agentID, tenantID)
	}
	
	cacheKey := fmt.Sprintf("agent:%d:%s", agentID, tenantID)
	
	// 通过缓存加载
	result, err := globalCache.GetOrLoadJSON(ctx, cacheKey, func(ctx context.Context) (any, error) {
		return loadAgentConfigFromDB(ctx, agentID, tenantID)
	})
	
	if err != nil {
		return nil, err
	}
	
	if result == nil {
		return nil, fmt.Errorf("agent %d not found for tenant %s", agentID, tenantID)
	}
	
	// 类型断言（需要改进反序列化逻辑）
	cfg, ok := result.(*AgentConfig)
	if !ok {
		// JSON 反序列化后是 map[string]any，需要重新转换
		return convertToAgentConfig(result)
	}
	
	return cfg, nil
}

// loadAgentConfigFromDB 从数据库加载（原逻辑）
func loadAgentConfigFromDB(ctx context.Context, agentID int, tenantID string) (*AgentConfig, error) {
	// 原来的实现：调 lea-config
	// ... 保持不变
}
```

#### 4.3 main.go 传入 Redis 客户端

`packages/lea-agent/cmd/server/main.go`（修改）：

```go
func main() {
	// ... 现有初始化

	// 初始化 Redis（已有）
	redisClient := redis.NewClient(&redis.Options{
		Addr: os.Getenv("REDIS_URL"),
	})
	
	// 传给 runtime
	runtime.InitCache(redisClient)
	
	// ... 启动服务
}
```

**时间**：1 天

---

## P1：Beta 阶段（上线后 1-2 周）

### 5. 消息队列（Redis + Celery）

**影响**：文档解析异步化，不卡 HTTP 连接

#### 5.1 场景

1. **文档解析**（KB 上传大文件）
   - 问题：50MB PDF 解析 10 分钟 → HTTP 连接超时
   - 方案：POST 立即返回 202 + task_id → worker 处理 → 前端轮询状态

2. **后台清理任务**（定期 cron）
   - 问题：过期会话清理在主 goroutine 里可能阻塞
   - 方案：定时入队清理任务 → worker 异步处理

#### 5.2 实现

**Python 侧（lea-rag）** — 用 Celery

`packages/lea-rag/requirements.txt`（新增）：
```txt
celery==5.3.4
redis==5.0.1
```

`packages/lea-rag/src/celery_app.py`（新建）：
```python
from celery import Celery
import os

app = Celery(
    "lea-rag",
    broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 小时超时
)
```

`packages/lea-rag/src/tasks/document.py`（新建）：
```python
from celery_app import app
from api.kb import parse_document_internal
import logging

logger = logging.getLogger(__name__)

@app.task(bind=True, max_retries=3)
def parse_document_task(self, kb_id: str, doc_id: str, file_key: str):
    """解析文档任务"""
    try:
        logger.info(f"Parsing doc {doc_id} in KB {kb_id}")
        result = parse_document_internal(kb_id, doc_id, file_key)
        return {"status": "success", "result": result}
    except Exception as e:
        logger.error(f"Parse failed: {e}", exc_info=True)
        # 重试（指数退避）
        self.retry(exc=e, countdown=2 ** self.request.retries)
```

`packages/lea-rag/src/api/kb.py`（修改上传端点）：
```python
from tasks.document import parse_document_task

@router.post("/kb/{kb_id}/upload")
async def upload_document(kb_id: str, file: UploadFile):
    # 1. 保存文件到存储
    file_key = await save_to_storage(file)
    
    # 2. 创建文档记录（status=pending）
    doc_id = await db.create_document(kb_id, file.filename, status="pending")
    
    # 3. 入队异步任务
    task = parse_document_task.delay(kb_id, doc_id, file_key)
    
    return {
        "doc_id": doc_id,
        "task_id": task.id,
        "status": "pending"
    }

@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """查询任务状态"""
    from celery.result import AsyncResult
    result = AsyncResult(task_id, app=app)
    
    return {
        "task_id": task_id,
        "status": result.state,  # PENDING/STARTED/SUCCESS/FAILURE
        "result": result.result if result.ready() else None,
    }
```

**启动 worker**：
```bash
cd packages/lea-rag
celery -A src.celery_app worker --loglevel=info --concurrency=4
```

**Go 侧（lea-agent）** — 复用 Redis List

`packages/lea-agent/internal/queue/queue.go`（新建）：
```go
package queue

import (
	"context"
	"encoding/json"
	"time"
	
	"github.com/redis/go-redis/v9"
)

type Queue struct {
	client *redis.Client
	name   string
}

func New(client *redis.Client, name string) *Queue {
	return &Queue{client: client, name: name}
}

// Enqueue 入队
func (q *Queue) Enqueue(ctx context.Context, task any) error {
	data, err := json.Marshal(task)
	if err != nil {
		return err
	}
	return q.client.RPush(ctx, q.name, data).Err()
}

// Dequeue 出队（阻塞）
func (q *Queue) Dequeue(ctx context.Context, timeout time.Duration) ([]byte, error) {
	result, err := q.client.BLPop(ctx, timeout, q.name).Result()
	if err == redis.Nil {
		return nil, nil  // 超时，无任务
	}
	if err != nil {
		return nil, err
	}
	return []byte(result[1]), nil  // result = [queueName, data]
}
```

**时间**：3 天

---

### 6. 异步任务框架

**影响**：统一任务定义、重试、死信队列、幂等性

#### 6.1 任务定义

`packages/lea-agent/internal/tasks/registry.go`（新建）：
```go
package tasks

import (
	"context"
	"encoding/json"
	"fmt"
)

type Task struct {
	ID        string                 `json:"id"`
	Type      string                 `json:"type"`
	Params    map[string]any         `json:"params"`
	Retry     int                    `json:"retry"`
	MaxRetry  int                    `json:"max_retry"`
	CreatedAt string                 `json:"created_at"`
	Deadline  string                 `json:"deadline,omitempty"`
}

type Handler func(context.Context, map[string]any) error

var registry = make(map[string]Handler)

// Register 注册任务处理器
func Register(taskType string, handler Handler) {
	registry[taskType] = handler
}

// Execute 执行任务
func Execute(ctx context.Context, taskData []byte) error {
	var task Task
	if err := json.Unmarshal(taskData, &task); err != nil {
		return fmt.Errorf("unmarshal task: %w", err)
	}
	
	handler, ok := registry[task.Type]
	if !ok {
		return fmt.Errorf("unknown task type: %s", task.Type)
	}
	
	return handler(ctx, task.Params)
}
```

#### 6.2 Worker 实现

`packages/lea-agent/cmd/worker/main.go`（新建）：
```go
package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"
	
	"lea-agent/internal/queue"
	"lea-agent/internal/tasks"
	"github.com/redis/go-redis/v9"
)

func main() {
	// 初始化 Redis
	redisClient := redis.NewClient(&redis.Options{
		Addr: os.Getenv("REDIS_URL"),
	})
	
	// 注册任务
	tasks.Register("cleanup_sandbox", tasks.CleanupSandbox)
	tasks.Register("expire_sessions", tasks.ExpireSessions)
	
	// 创建队列
	q := queue.New(redisClient, "lea:tasks")
	
	// 优雅关闭
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)
	
	go func() {
		<-sigCh
		log.Println("Shutting down worker...")
		cancel()
	}()
	
	// 主循环
	log.Println("Worker started")
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		
		// 出队任务（阻塞 5 秒）
		taskData, err := q.Dequeue(ctx, 5*time.Second)
		if err != nil {
			log.Printf("Dequeue error: %v", err)
			time.Sleep(1 * time.Second)
			continue
		}
		
		if taskData == nil {
			continue  // 超时，无任务
		}
		
		// 执行任务
		if err := tasks.Execute(ctx, taskData); err != nil {
			log.Printf("Task failed: %v", err)
			// TODO: 重试逻辑或死信队列
		} else {
			log.Printf("Task completed")
		}
	}
}
```

#### 6.3 具体任务实现

`packages/lea-agent/internal/tasks/cleanup.go`（新建）：
```go
package tasks

import (
	"context"
	"log"
)

func CleanupSandbox(ctx context.Context, params map[string]any) error {
	sandboxID := params["sandbox_id"].(string)
	log.Printf("Cleaning up sandbox: %s", sandboxID)
	
	// 调用 Cube API 删除沙箱
	// ...
	
	return nil
}

func ExpireSessions(ctx context.Context, params map[string]any) error {
	maxAge := params["max_age_hours"].(float64)
	log.Printf("Expiring sessions older than %.0f hours", maxAge)
	
	// 查询过期会话并删除
	// ...
	
	return nil
}
```

**时间**：2 天

---

### 7. 熔断器

**影响**：防止下游服务抖动拖垮上游

#### 7.1 Go 侧实现

`packages/lea-agent/go.mod`（新增依赖）：
```go
require github.com/sony/gobreaker v0.5.0
```

`packages/lea-agent/internal/runtime/gateway.go`（修改）：
```go
import "github.com/sony/gobreaker"

type Gateway struct {
	client        *http.Client
	config        Config
	kbBreaker     *gobreaker.CircuitBreaker
	memoryBreaker *gobreaker.CircuitBreaker
}

func NewGateway(cfg Config) *Gateway {
	// 熔断器配置：10 秒内失败 5 次 → 熔断 30 秒
	breakerSettings := gobreaker.Settings{
		Name:        "kb-search",
		MaxRequests: 3,                  // 半开状态允许 3 个请求
		Interval:    10 * time.Second,   // 统计窗口
		Timeout:     30 * time.Second,   // 熔断持续时间
		ReadyToTrip: func(counts gobreaker.Counts) bool {
			return counts.ConsecutiveFailures >= 5
		},
	}
	
	return &Gateway{
		client:        &http.Client{Timeout: 10 * time.Second},
		config:        cfg,
		kbBreaker:     gobreaker.NewCircuitBreaker(breakerSettings),
		memoryBreaker: gobreaker.NewCircuitBreaker(breakerSettings),
	}
}

// SearchKB 带熔断器
func (g *Gateway) SearchKB(ctx context.Context, query string, kbID string) ([]KBResult, error) {
	result, err := g.kbBreaker.Execute(func() (any, error) {
		return g.doSearchKB(ctx, query, kbID)
	})
	
	if err == gobreaker.ErrOpenState {
		// 熔断打开，降级返回空结果
		log.Printf("KB search circuit open, degrading")
		return []KBResult{}, nil
	}
	
	if err != nil {
		return nil, err
	}
	
	return result.([]KBResult), nil
}

func (g *Gateway) doSearchKB(ctx context.Context, query string, kbID string) ([]KBResult, error) {
	// 实际调用 lea-rag
	// ...
}
```

#### 7.2 Python 侧实现

`packages/lea-rag/requirements.txt`（新增）：
```txt
pybreaker==1.0.1
```

`packages/lea-rag/src/breaker.py`（新建）：
```python
from pybreaker import CircuitBreaker
import logging

logger = logging.getLogger(__name__)

# 熔断器：10 秒内失败 5 次 → 熔断 30 秒
vector_db_breaker = CircuitBreaker(
    fail_max=5,
    timeout_duration=30,
    name="vector_db",
)

def on_circuit_open(breaker):
    logger.warning(f"Circuit {breaker.name} opened")

def on_circuit_close(breaker):
    logger.info(f"Circuit {breaker.name} closed")

vector_db_breaker.add_listeners(on_circuit_open, on_circuit_close)
```

`packages/lea-rag/src/api/kb.py`（修改）：
```python
from breaker import vector_db_breaker
from pybreaker import CircuitBreakerError

@router.post("/kb/search")
async def search_kb(kb_id: str, query: str, limit: int = 10):
    try:
        # 通过熔断器调用
        @vector_db_breaker
        def do_search():
            return vector_db.search(kb_id, query, limit)
        
        results = do_search()
        return {"results": results, "degraded": False}
    
    except CircuitBreakerError:
        # 熔断打开，降级返回
        logger.warning("Vector DB circuit open, degrading")
        return {"results": [], "degraded": True}
    
    except Exception as e:
        logger.error(f"KB search failed: {e}", exc_info=True)
        return {"results": [], "degraded": True}
```

**时间**：2 天

---

### 8. 监控与告警

**影响**：及早发现问题

#### 8.1 指标上报

`packages/lea-agent/internal/metrics/metrics.go`（新建）：
```go
package metrics

import (
	"log"
	"time"
)

// 简单版：日志上报（生产用 Prometheus）
func RecordCacheHit(key string, hit bool) {
	if hit {
		log.Printf("[METRIC] cache_hit key=%s", key)
	} else {
		log.Printf("[METRIC] cache_miss key=%s", key)
	}
}

func RecordDBQuery(table string, duration time.Duration) {
	log.Printf("[METRIC] db_query table=%s duration=%dms", table, duration.Milliseconds())
}

func RecordQueueDepth(queue string, depth int) {
	log.Printf("[METRIC] queue_depth queue=%s depth=%d", queue, depth)
}

func RecordCircuitState(name string, state string) {
	log.Printf("[METRIC] circuit_state name=%s state=%s", name, state)
}
```

#### 8.2 告警规则（示例）

如果用 Prometheus + Alertmanager：

```yaml
groups:
  - name: lea-platform
    rules:
      # 连接池使用率 > 80%
      - alert: DBConnectionPoolHigh
        expr: db_connections_active / db_connections_max > 0.8
        for: 2m
        annotations:
          summary: "Database connection pool usage high"
      
      # 队列积压 > 100
      - alert: QueueBacklog
        expr: queue_depth{queue="lea:tasks"} > 100
        for: 5m
        annotations:
          summary: "Task queue backlog"
      
      # 错误率 > 5%
      - alert: HighErrorRate
        expr: rate(http_requests_total{status=~"5.."}[5m]) > 0.05
        for: 2m
        annotations:
          summary: "High error rate"
      
      # 熔断器打开
      - alert: CircuitBreakerOpen
        expr: circuit_breaker_state{state="open"} == 1
        for: 1m
        annotations:
          summary: "Circuit breaker opened"
```

**时间**：2-3 天

---

## 执行顺序总结

| 步骤 | 任务 | 时间 | 优先级 | 依赖 |
|------|------|------|--------|------|
| 1 | 数据库索引 | 5 分钟 | P0 | 无 |
| 2 | Nginx 限流 | 30 分钟 | P0 | 无 |
| 3 | 降级开关 | 1 天 | P0 | 无 |
| 4 | 缓存集成 | 1 天 | P0 | cache.go ✅ |
| 5 | 消息队列 | 3 天 | P1 | Redis |
| 6 | 异步任务框架 | 2 天 | P1 | 消息队列 |
| 7 | 熔断器 | 2 天 | P1 | 无 |
| 8 | 监控告警 | 2-3 天 | P1 | 无 |

**总计**：
- P0（上线前）：2-3 天
- P1（Beta）：9-10 天

---

## 验收标准

### P0 验收

```bash
# 1. 索引验证
mysql -h 202.205.160.9 -P 3307 -u root -p lea_platform -e "
  EXPLAIN SELECT * FROM chat_sessions 
  WHERE tenant_id='t1' AND user_id='u1' AND is_archived=0 
  ORDER BY last_active_at DESC LIMIT 20;
" | grep idx_sessions_list

# 2. 限流验证
for i in {1..100}; do
  curl -H "X-User-ID: test" http://localhost/api/sessions &
done
# 预期：前 30 个成功，后面返回 429

# 3. 降级验证
# 停掉 lea-rag
docker-compose stop lea-rag
# 发消息（不带 KB）
curl -X POST http://localhost/api/sessions/1/messages -d '{"content":"hello"}'
# 预期：200 OK（不是 500）

# 4. 缓存验证
# 查日志
grep "cache_hit" logs/lea-agent.log | wc -l
grep "cache_miss" logs/lea-agent.log | wc -l
# 预期：命中率 > 70%
```

### P1 验收

```bash
# 5. 消息队列验证
# 上传 50MB 文档
curl -X POST http://localhost/api/kb/1/upload -F "file=@large.pdf"
# 预期：立即返回 202 + task_id

# 查任务状态
curl http://localhost/api/tasks/{task_id}
# 预期：{"status": "STARTED"} → {"status": "SUCCESS"}

# 6. 异步任务验证
# 查 worker 日志
tail -f logs/worker.log
# 预期：看到 "Task completed"

# 7. 熔断器验证
# 模拟 lea-rag 故障（返回 500）
# 连续发 10 个请求
for i in {1..10}; do
  curl http://localhost/api/kb/search?query=test
done
# 预期：前 5 个返回 500，后面返回 {"degraded": true}

# 8. 监控验证
# 查指标日志
grep "\\[METRIC\\]" logs/lea-agent.log | tail -20
# 预期：有 cache_hit/db_query/queue_depth 等指标
```

---

## 回滚计划

| 步骤 | 回滚方法 | 风险 |
|------|---------|------|
| 索引 | `ALTER TABLE DROP INDEX` | 低（只加索引，未改表结构）|
| 限流 | 删除 nginx 配置，reload | 零（配置即改）|
| 降级 | 环境变量设回 `true` | 零 |
| 缓存 | 设 `ENABLE_CACHE=false` | 低（回退到无缓存）|
| 队列 | 停掉 worker，清空队列 | 中（堆积任务丢失）|
| 熔断器 | 删除 breaker 代码，重新发布 | 中（需重新部署）|

---

## 附录：配置文件模板

### docker-compose.yml 补充

```yaml
services:
  # Worker（异步任务）
  lea-worker:
    build: ./packages/lea-agent
    command: /app/worker
    environment:
      - REDIS_URL=redis://redis:6379/0
      - DB_HOST=202.205.160.9
      - DB_PORT=3307
    depends_on:
      - redis
      - mysql
    restart: always
  
  # Celery Worker（Python 任务）
  lea-celery:
    build: ./packages/lea-rag
    command: celery -A src.celery_app worker --loglevel=info
    environment:
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - redis
    restart: always
```

### 环境变量清单

```bash
# lea-agent
ENABLE_KB_SEARCH=true
ENABLE_MEMORY_SEARCH=true
ENABLE_EXTERNAL_TOOLS=true
KB_SEARCH_TIMEOUT=5s
MEMORY_SEARCH_TIMEOUT=3s
TOOL_EXECUTION_TIMEOUT=30s
ENABLE_CACHE=true
CACHE_BASE_TTL=5m
CACHE_JITTER=60s

# lea-rag
ENABLE_KB_SEARCH=true
VECTOR_DB_TIMEOUT=10s
```
  实际 TTL = 300 + random(-60, +60) = 240~360 秒
  
结果：key 过期时间分散，不会突发峰值
```

#### 2. 缓存击穿防护（Penetration）
```
问题：热 key 过期时，1000 个请求同时查库
原因：没有加锁，都打到数据库
解决：分布式锁 (Redis SET NX)

流程：
  1. 线程 A: 缓存 miss
  2. 线程 A: 尝试 SET lock:agent:1 NX TTL=5s
  3. 线程 A: 拿到锁 → 去数据库查
  4. 线程 B/C/.../Z: 缓存 miss，尝试拿锁
  5. 线程 B/C/.../Z: 拿不到锁 → 等待 (100ms * 3次)
  6. 线程 A: 查完了，写缓存，释放锁
  7. 线程 B/C/.../Z: 重试读缓存 → 命中
  
结果：1000 个请求，只有 1 个查库，其他 999 个等待或命中
```

#### 3. 缓存穿透防护（Pass-through）
```
问题：查一个不存在的 key，缓存 miss → 每次都查库
原因：NULL 值没有被缓存
解决：缓存 NULL 标记（短 TTL）

示例：
  agent:9999 不存在
  
  第一次：
    缓存 miss → 查库 → 空 → 缓存 "__NULL__" TTL=60s
  
  第二次（60s 内）：
    缓存命中 "__NULL__" → 直接返回 NULL
    不用查库
  
  60s 后：
    TTL 过期，可以重新检查库
```

#### 4. 缓存与库不一致（Consistency）
```
问题：用户改了配置，缓存还是旧的
解决：主动失效 + 发布事件

流程（以 update_agent 为例）：
  1. 用户修改 agent 配置
  2. 写入数据库
  3. 立即删除缓存 key
  4. 发布事件到 Redis pub/sub: cache:invalidate
  5. 所有 pod 收到事件，本地清对应缓存
  
结果：修改立即生效，无缓存延迟
```

### 实施内容

**Go 侧**（lea-agent）：
```
packages/lea-agent/internal/cache/
  ├── cache.go          // 核心缓存类（已实现）
  └── cache_test.go     // 单元测试

使用示例：

  cfg := runtime.LoadAgentConfig(ctx, agentID, tenantID)
  
  改成：
  
  cache := cache.New(redisClient, cache.DefaultConfig)
  cfg, err := cache.GetOrLoadJSON(ctx, fmt.Sprintf("agent:%d", agentID), func(ctx context.Context) (any, error) {
      return runtime.LoadAgentConfig(ctx, agentID, tenantID)
  })

  // 修改 agent 时
  _ = cache.Delete(ctx, fmt.Sprintf("agent:%d", agentID))
  _ = cache.PublishInvalidation(ctx, "agent", fmt.Sprintf("%d", agentID))
```

**Python 侧**（lea-config, lea-rag）：
```
packages/lea-config/src/lea_config/cache.py
packages/lea-rag/src/lea_rag/cache.py

使用示例（asyncio）：

  cache = Cache(redis_client, BaseTTL=300, Jitter=60)
  
  kbs = await cache.get_or_load_json(
      "kbs:agent:1",
      loader=lambda: db.query(KnowledgeBase).filter_by(agent_id=1).all()
  )

  # 修改 KB 时
  await cache.delete(f"kbs:agent:{agent_id}")
```

### 缓存键设计规范

```
agent 配置:           agent:{id}
skill 列表:           skills:{agent_id}
kb 列表:              kbs:{agent_id}
工具列表:             tools:{agent_id}
mcp_servers:          mcp_servers:{user_id}
用户信息:             user:{id}
租户配置:             tenant:{id}

清除所有相关缓存时：
  cache.InvalidatePattern(ctx, "agent:*")
  cache.InvalidatePattern(ctx, "kbs:*")
```

### 预期收益

| 指标 | 改前 | 改后 |
|------|------|------|
| 每个 key 查询的 DB 连接数 | 20 (pool满) | 1 |
| 缓存命中率 | 0% | 80%+ |
| 数据库 QPS | 500+ (峰值) | 50-100 |
| 平均响应时间 | 500ms | 50ms |

---

## 第 2 步：代码安全审计清单

### 为什么现在必须做

**风险等级**：HIGH

上线前必须排除：
- SQL 注入漏洞
- 权限检查遗漏
- 敏感信息泄露
- 无效的错误处理

### 审计清单

#### 2.1 SQL 注入风险

**检查项**：
```
□ 所有数据库查询都用参数化查询（不是字符串拼接）
□ 用户输入（query、filter、sort）都被验证和转义
□ 动态 SQL（如排序字段）有白名单检查
```

**自动化扫描**：
```bash
# Go
go install github.com/securego/gosec/v2/cmd/gosec@latest
gosec ./packages/lea-agent/...
gosec ./packages/lea-config/...

# Python
python -m pip install bandit
bandit -r packages/lea-rag/src
bandit -r packages/lea-config/src
```

**典型问题及修复**：
```go
// ❌ 危险：字符串拼接
query := fmt.Sprintf("SELECT * FROM agents WHERE id = %d", userInput)
db.Query(query)

// ✅ 安全：参数化
db.QueryContext(ctx, "SELECT * FROM agents WHERE id = ?", userInput)
```

```python
# ❌ 危险
query = f"SELECT * FROM agents WHERE id = {user_input}"
db.execute(query)

# ✅ 安全
query = "SELECT * FROM agents WHERE id = ?"
db.execute(query, (user_input,))
```

#### 2.2 权限检查遗漏

**检查项**：
```
□ 所有数据修改操作都有权限检查（UPDATE/DELETE）
□ 跨租户请求都验证 tenant_id 归属
□ 跨用户请求都验证 user_id 归属
□ 列表查询都按租户/用户过滤
```

**扫描脚本**：
```bash
# 查找所有 UPDATE/DELETE，检查有没有权限条件
grep -r "UPDATE\|DELETE" packages/lea-agent/internal/persistence/*.go | \
  grep -v "WHERE.*tenant_id\|WHERE.*user_id" | \
  head -20
```

**典型问题**：
```go
// ❌ 危险：没有租户过滤
func DeleteProject(ctx context.Context, db *sql.DB, id int) error {
    return db.ExecContext(ctx, "DELETE FROM projects WHERE id = ?", id).Err()
}

// ✅ 安全：有租户和用户过滤
func DeleteProject(ctx context.Context, db *sql.DB, id int, tenantID, userID string) error {
    result, err := db.ExecContext(ctx, 
        "DELETE FROM projects WHERE id = ? AND tenant_id = ? AND user_id = ?",
        id, tenantID, userID)
    if err != nil {
        return err
    }
    if affected, _ := result.RowsAffected(); affected == 0 {
        return sql.ErrNoRows  // 404 而不是成功
    }
    return nil
}
```

#### 2.3 敏感信息泄露

**检查项**：
```
□ 日志里不能出现密码、token、API key
□ 错误响应不能泄露内部路径、SQL、栈
□ 返回的 JSON 里不能有不该公开的字段
```

**自动化扫描**：
```bash
# 查找可能的敏感数据日志
grep -r "password\|token\|secret\|key" \
  packages/lea-*/src --include="*.py" \
  | grep -i "log\|print" \
  | grep -v "log_name\|key_id\|token_id" \
  | head -20

# Go
grep -r "log.Printf\|fmt.Printf" packages/lea-agent/internal \
  | grep -i "password\|secret\|token" \
  | head -20
```

**典型问题**：
```python
# ❌ 危险：日志里有 token
logger.info(f"User login with token: {token}")

# ✅ 安全：只记 ID
logger.info(f"User login: {user_id}")

# ❌ 危险：错误响应暴露 SQL
{
  "error": "UNIQUE constraint failed: agents.name",
  "sql": "INSERT INTO agents ..."
}

# ✅ 安全：通用错误
{
  "error": "Agent with this name already exists"
}
```

#### 2.4 错误处理

**检查项**：
```
□ 所有 defer 都正确处理 error（不是空的 _）
□ 所有 panic 都有注释说明为什么必须 panic
□ 数据库操作失败都有回滚（事务）
```

**扫描脚本**：
```bash
# 查找所有 defer，看有没有处理错误
grep -r "defer" packages/lea-agent/internal --include="*.go" | \
  grep -v "Rollback\|Close" | \
  head -10

# 查找 panic，看有没有注释
grep -r "panic(" packages/lea-agent/internal --include="*.go" | \
  grep -v "^[[:space:]]*//.*panic" | \
  head -10
```

#### 2.5 分布式锁超时

**检查项**：
```
□ Redis locker 都设了 TTL（防孤儿锁）
□ 所有分布式锁操作都有超时
□ 锁获取失败都有降级方案
```

**现状检查**：
```bash
grep -r "locker\|lock" packages/lea-agent/internal/lock/*.go | \
  grep -v "TTL\|Timeout\|Expire" | \
  head -10
```

### 审计工作流

```
第一天：
  1. 运行 gosec 和 bandit
  2. 记录所有高风险项
  3. 分配责任人

第二天：
  1. 修复 SQL 注入（高优先）
  2. 补全权限检查
  3. 清理日志里的敏感信息

第三天：
  1. 错误处理审计
  2. 代码审查（peer review）
  3. 编写测试覆盖安全场景
```

### 输出物

```
docs/SECURITY_AUDIT_REPORT.md
  - 发现的问题列表（严重程度）
  - 修复状态
  - 测试覆盖度
  
docs/SECURITY_GUIDELINES.md
  - 代码审查清单
  - 常见错误模式
  - 最佳实践
```

---

## 第 3 步：消息队列设计

### 为什么现在必须做

**当前问题**：
```
场景 1: 用户上传知识库文档
  POST /kb/upload → 解析 PDF → 存向量 → 返回
  
  问题：
  - 大文件（50MB PDF）解析需要 10 分钟
  - HTTP 请求超时（通常 30s）
  - 用户以为失败，重复上传
  - 数据库连接被长时间占用

场景 2: 清理过期会话和沙箱
  定期 sweep() → 可能卡死主 goroutine
  
  问题：
  - 清理期间无法处理新请求
  - 大量连接卡在清理上
```

### 方案：Redis FIFO 队列

**设计原理**：
```
用户请求 → 入队 → 立即返回 (202 Accepted)
        ↓
后台 worker → 队列取任务 → 处理 → 存结果
        ↓
用户查询结果 → GET /tasks/{task_id}
```

**任务格式**：
```json
{
  "id": "task-uuid",
  "type": "parse_document|cleanup_session",
  "params": {
    "kb_id": "kb-1",
    "doc_id": "doc-123",
    "file_key": "s3://..."
  },
  "created_at": "2026-09-14T10:00:00Z",
  "deadline": "2026-09-14T10:30:00Z",
  "retry": 3,
  "status": "pending|processing|done|failed",
  "result": null,
  "error": null
}
```

### 架构

```
                    ┌─────────────────────┐
                    │   Frontend Request  │
                    └──────────┬──────────┘
                               │
                ┌──────────────▼──────────────┐
                │  lea-agent / lea-rag API   │
                │  1. 生成 task_id           │
                │  2. 入队 lea:tasks:queue  │
                │  3. 保存到数据库          │
                │  4. 返回 202 Accepted     │
                └──────────────┬──────────────┘
                               │
                ┌──────────────▼──────────────┐
                │   Redis Queue (FIFO)       │
                │  lea:tasks:queue           │
                │  ├─ task-1                 │
                │  ├─ task-2                 │
                │  └─ task-3                 │
                └─────────────┬──────────────┘
                              │
                ┌─────────────▼──────────────┐
                │  Background Workers      │
                │  (goroutine / Python)    │
                │  1. BLPOP from queue    │
                │  2. 处理任务            │
                │  3. 更新状态到 DB       │
                │  4. 发布完成事件        │
                └─────────────────────────┘
```

### 实施细节

#### 3.1 入队 API

```go
// handler 接收上传请求
POST /api/kb/{id}/upload
{
  "file": <binary>
}

// 处理流程
func handleUpload(c *gin.Context) {
    file := c.FormFile("file")
    
    // 1. 保存文件到 S3/NFS
    fileKey := saveFile(file)
    
    // 2. 生成任务
    task := Task{
        ID:        uuid.New().String(),
        Type:      "parse_document",
        Status:    "pending",
        Params: map[string]any{
            "kb_id": kbID,
            "doc_id": docID,
            "file_key": fileKey,
        },
        CreatedAt: time.Now(),
        Deadline:  time.Now().Add(30 * time.Minute),
    }
    
    // 3. 保存到数据库
    db.InsertTask(task)
    
    // 4. 入队
    queueTask(task)
    
    // 5. 立即返回
    c.JSON(202, map[string]any{
        "task_id": task.ID,
        "status": "queued",
        "check_at": "/api/tasks/" + task.ID,
    })
}
```

#### 3.2 Worker 处理

```go
// worker.go
func StartWorker(ctx context.Context, db *sql.DB, redis *redis.Client) {
    for {
        select {
        case <-ctx.Done():
            return
        default:
        }
        
        // 阻塞读，没有任务时等待
        result, err := redis.BLPop(ctx, 1*time.Second, "lea:tasks:queue").Result()
        if err == redis.Nil {
            continue  // 超时，继续轮询
        }
        if err != nil {
            log.Printf("queue read error: %v", err)
            continue
        }
        
        taskJSON := result[1]  // BLPOP 返回 [key, value]
        var task Task
        json.Unmarshal([]byte(taskJSON), &task)
        
        // 更新状态为 processing
        db.UpdateTaskStatus(task.ID, "processing")
        
        // 处理任务
        result := processTask(ctx, task)
        
        // 更新完成状态
        db.UpdateTaskResult(task.ID, "done", result, nil)
    }
}

func processTask(ctx context.Context, task Task) any {
    switch task.Type {
    case "parse_document":
        return parseDocument(ctx, task.Params["file_key"].(string))
    case "publish_skill":
        return publishSkill(ctx, task.Params["skill_id"].(string))
    default:
        return nil
    }
}
```

#### 3.3 查询结果 API

```go
// handler 查询任务状态
GET /api/tasks/{id}

func handleGetTask(c *gin.Context) {
    taskID := c.Param("id")
    
    task, err := db.GetTask(taskID)
    if err != nil {
        c.JSON(404, gin.H{"error": "task not found"})
        return
    }
    
    if task.Status == "pending" || task.Status == "processing" {
        c.JSON(202, gin.H{
            "id": task.ID,
            "status": task.Status,
            "progress": task.Progress,  // 可选：处理进度
            "retry_in": 5,  // 建议 5 秒后重试
        })
        return
    }
    
    if task.Status == "done" {
        c.JSON(200, gin.H{
            "id": task.ID,
            "status": "done",
            "result": task.Result,
        })
        return
    }
    
    if task.Status == "failed" {
        c.JSON(200, gin.H{
            "id": task.ID,
            "status": "failed",
            "error": task.Error,
            "retry": task.Retry > 0,  // 还能重试吗
        })
        return
    }
}
```

### 数据库表

```sql
CREATE TABLE tasks (
  id VARCHAR(36) PRIMARY KEY,
  type VARCHAR(50),
  status ENUM('pending', 'processing', 'done', 'failed'),
  params JSON,
  result JSON,
  error TEXT,
  
  retry INT DEFAULT 3,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  
  tenant_id VARCHAR(50),
  user_id VARCHAR(50),
  
  INDEX (tenant_id, created_at),
  INDEX (status, created_at)
);
```

### 失败处理

```
场景 1: 任务处理失败
  ├─ retry > 0 → 重新入队（等等再试）
  └─ retry = 0 → 标记 failed，用户可以手动重试

场景 2: Worker 崩溃
  ├─ 任务还在队列里 → 其他 worker 会继续处理
  └─ 没问题（Redis FIFO 的优势）

场景 3: 任务超时
  ├─ 检查 deadline
  ├─ 如果超过 deadline → 标记 failed，清理资源
  └─ 定期清理超期任务（独立的清理 worker）
```

### 预期效果

```
改前：
  POST /kb/upload
  ├─ 解析 PDF（10 分钟）
  └─ 返回 200（用户等 10 分钟）

改后：
  POST /kb/upload
  ├─ 保存文件（1 秒）
  ├─ 入队（10ms）
  └─ 返回 202（用户立即收到 task_id）
  
  后台 worker：
  ├─ 取任务（1ms）
  ├─ 解析 PDF（10 分钟，不阻塞主链路）
  └─ 存结果
  
  用户查询：
  GET /api/tasks/{task_id}
  └─ 返回进度或结果
```

---

## 第 4 步：异步任务框架

### 为什么现在必须做

消息队列只是"队列"，没有框架：
- 没有统一的错误处理
- 没有失败重试的策略
- 没有死信队列
- 没有幂等性保证
- 没有可观测性

### 方案：通用任务框架

**核心概念**：
```
Task = {
  type: string      // 任务类型标识符
  id: uuid          // 幂等性 key
  params: json      // 输入参数
  retry: int        // 重试次数
  deadline: time    // 截止时间
  status: enum      // 状态
  result: json      // 输出
  error: string     // 错误信息
}

Handler = func(ctx, params) (result, error)
  // 每个任务类型对应一个 handler
```

### 架构

```
Task Handler Registry
  ├─ parse_document → handler_parse_doc
  └─ cleanup_session → handler_cleanup_session

Task Executor
  ├─ 从队列取任务
  ├─ 查 registry 找对应 handler
  ├─ 执行 handler
  ├─ 处理结果/错误
  ├─ 存储到数据库
  └─ 发布完成事件

Error Handling
  ├─ 临时错误 (网络超时) → 重试（指数退避）
  ├─ 永久错误 (参数错误) → 标记 failed
  └─ 未知错误 → 死信队列
```

### 实施：Go 侧

```go
// packages/lea-agent/internal/task/task.go

package task

import (
    "context"
    "encoding/json"
    "time"
)

// TaskType 任务类型常量
type TaskType string

const (
    TaskParseDocument   TaskType = "parse_document"
    TaskCleanupSession  TaskType = "cleanup_session"
)

// Task 任务结构体
type Task struct {
    ID        string                 `json:"id"`
    Type      TaskType               `json:"type"`
    Status    string                 `json:"status"` // pending, processing, done, failed
    Params    map[string]interface{} `json:"params"`
    Result    map[string]interface{} `json:"result"`
    Error     string                 `json:"error"`
    Retry     int                    `json:"retry"`
    CreatedAt time.Time              `json:"created_at"`
    Deadline  time.Time              `json:"deadline"`
    TenantID  string                 `json:"tenant_id"`
    UserID    string                 `json:"user_id"`
}

// Handler 任务处理函数签名
type Handler func(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error)

// Registry 任务处理器注册表
type Registry struct {
    handlers map[TaskType]Handler
}

// NewRegistry 创建注册表
func NewRegistry() *Registry {
    return &Registry{
        handlers: make(map[TaskType]Handler),
    }
}

// Register 注册处理器
func (r *Registry) Register(taskType TaskType, handler Handler) {
    r.handlers[taskType] = handler
}

// Get 获取处理器
func (r *Registry) Get(taskType TaskType) Handler {
    return r.handlers[taskType]
}

// Executor 任务执行器
type Executor struct {
    registry *Registry
    db       DB
    queue    Queue
}

// NewExecutor 创建执行器
func NewExecutor(registry *Registry, db DB, queue Queue) *Executor {
    return &Executor{
        registry: registry,
        db:       db,
        queue:    queue,
    }
}

// Execute 执行单个任务
func (e *Executor) Execute(ctx context.Context, task Task) error {
    // 检查截止时间
    if time.Now().After(task.Deadline) {
        task.Status = "failed"
        task.Error = "deadline exceeded"
        return e.db.UpdateTask(task)
    }

    // 查找处理器
    handler := e.registry.Get(task.Type)
    if handler == nil {
        task.Status = "failed"
        task.Error = "unknown task type: " + string(task.Type)
        return e.db.UpdateTask(task)
    }

    // 更新状态为处理中
    task.Status = "processing"
    e.db.UpdateTask(task)

    // 执行处理器
    result, err := handler(ctx, task.Params)

    if err != nil {
        return e.handleError(task, err)
    }

    // 成功
    task.Status = "done"
    task.Result = result
    task.Error = ""

    return e.db.UpdateTask(task)
}

// handleError 错误处理：区分临时和永久错误
func (e *Executor) handleError(task Task, err error) error {
    // 判断是否是临时错误（可重试）
    if isTemporaryError(err) && task.Retry > 0 {
        // 指数退避：1s, 2s, 4s, 8s...
        delay := time.Duration(1<<uint(3-task.Retry)) * time.Second
        
        task.Status = "pending"
        task.Retry--
        task.Error = err.Error()
        
        // 重新入队（带延迟）
        e.queue.EnqueueWithDelay(task, delay)
        return nil
    }

    // 永久错误或已无重试次数
    task.Status = "failed"
    task.Error = err.Error()
    
    // 发送到死信队列
    e.queue.EnqueueDeadLetter(task)
    
    return e.db.UpdateTask(task)
}

// Run 启动执行器，持续处理队列中的任务
func (e *Executor) Run(ctx context.Context) error {
    for {
        select {
        case <-ctx.Done():
            return nil
        default:
        }

        task, err := e.queue.Dequeue(ctx)
        if err != nil {
            time.Sleep(1 * time.Second)
            continue
        }

        if err := e.Execute(ctx, task); err != nil {
            // 记录错误但继续处理
            log.Printf("task execution error: %v", err)
        }
    }
}

// Queue 队列接口
type Queue interface {
    Enqueue(task Task) error
    EnqueueWithDelay(task Task, delay time.Duration) error
    EnqueueDeadLetter(task Task) error
    Dequeue(ctx context.Context) (Task, error)
}

// DB 数据库接口
type DB interface {
    InsertTask(task Task) error
    UpdateTask(task Task) error
    GetTask(id string) (Task, error)
}

// isTemporaryError 判断是否是临时错误
func isTemporaryError(err error) bool {
    // 网络错误、超时 → 临时
    // 参数错误、权限错误 → 永久
    
    // 可以用自定义 error 类型，或检查错误字符串
    errStr := err.Error()
    if strings.Contains(errStr, "connection refused") ||
       strings.Contains(errStr, "timeout") ||
       strings.Contains(errStr, "temporary failure") {
        return true
    }
    return false
}
```

### 使用示例

```go
// main.go 初始化

// 1. 创建 registry
registry := task.NewRegistry()

// 2. 注册处理器
registry.Register(task.TaskParseDocument, func(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
    kbID := params["kb_id"].(string)
    fileKey := params["file_key"].(string)
    
    // 调用 lea-rag 解析
    err := leaRAG.ParseDocument(ctx, kbID, fileKey)
    if err != nil {
        return nil, err
    }
    
    return map[string]interface{}{
        "status": "parsed",
        "pages": 10,
    }, nil
})

registry.Register(task.TaskCleanupSession, func(ctx context.Context, params map[string]interface{}) (map[string]interface{}, error) {
    sessionID := params["session_id"].(string)
    
    // 清理沙箱
    err := sandbox.Cleanup(ctx, sessionID)
    return nil, err
})

// 3. 创建执行器
executor := task.NewExecutor(registry, db, redis)

// 4. 启动后台 worker
go executor.Run(ctx)

// 5. 入队任务
t := task.Task{
    ID:     uuid.New().String(),
    Type:   task.TaskParseDocument,
    Params: map[string]interface{}{
        "kb_id":    "kb-1",
        "file_key": "s3://bucket/doc.pdf",
    },
    Retry:    3,
    Deadline: time.Now().Add(30 * time.Minute),
}

queue.Enqueue(t)
```

### 监控和观测

```
指标：
  - 每个任务类型的成功率
  - 平均执行时间
  - 重试次数分布
  - 死信队列大小

日志：
  - 任务入队/开始/完成/失败
  - 处理耗时
  - 错误堆栈

可视化：
  - 任务状态分布（饼图）
  - 执行时间趋势（折线图）
  - 错误率（柱状图）
```

---

## 实施时间表

### Week 1

**Day 1-2: 缓存工具库**
```
□ 完成 Go 缓存库实现
□ 单元测试（雪崩、击穿、穿透防护）
□ 集成测试（Redis 连接、TTL 验证）
```

**Day 2-3: 代码审计**
```
□ 运行安全扫描工具 (gosec, bandit)
□ 修复 SQL 注入风险
□ 补全权限检查
□ 清理敏感信息
```

**Day 3-4: 消息队列设计**
```
□ 数据库表设计
□ 入队/出队 API 设计
□ Worker 框架
□ 集成测试
```

**Day 4-5: 异步任务框架**
```
□ Task 类型定义
□ Handler Registry
□ Executor 实现
□ 错误处理和重试
```

### Week 2

**Day 6-7: 集成和测试**
```
□ 文档解析任务集成
□ Skill 发布任务集成
□ 会话清理任务集成
□ 端到端测试
```

**Day 8-9: 监控和观测**
```
□ 指标收集
□ 日志结构化
□ 仪表板
```

**Day 10: 性能测试**
```
□ 并发压力测试（1000 users）
□ 缓存命中率验证
□ 队列吞吐量测试
```

---

## 风险和缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Redis 单点故障 | 任务队列瘫痪 | 用 Redis Sentinel 或 Cluster |
| 数据库连接泄漏 | OOM | 监控连接数，设告警 |
| 任务堆积 | 内存溢出 | 设队列大小限制，死信隔离 |
| 缓存与库不一致 | 数据错误 | 主动失效 + 定期重验 |

---

## 预期效果

### Before（现在）
```
- 缓存命中率: 0%
- 数据库 QPS: 300-500
- 响应延迟（p99）: 2-5 秒
- 长任务：阻塞主链路
- 安全审计：未进行
```

### After（完成后）
```
- 缓存命中率: 80%+
- 数据库 QPS: 50-100
- 响应延迟（p99）: 100-500ms
- 长任务：异步处理，用户即时得到 task_id
- 安全审计：已完成，零高危项
```

### 支撑规模

```
当前:      100 并发用户
优化后:    1000+ 并发用户
```

---

## 检查清单

```
缓存:
  □ 所有频繁查询的配置都加了缓存
  □ TTL 加了抖动
  □ NULL 值有标记
  □ 修改时清缓存
  □ 有缓存监控

代码安全:
  □ 无 SQL 注入
  □ 权限检查完整
  □ 敏感信息不日志
  □ 错误处理完善
  
消息队列:
  □ Redis FIFO 功能完善
  □ 入队 API 设计好
  □ Worker 能持续处理
  □ 失败重试有策略

异步任务:
  □ Task 格式统一
  □ Handler Registry 完善
  □ 错误分类和重试
  □ 死信队列隔离
  □ 可观测性完整
```

