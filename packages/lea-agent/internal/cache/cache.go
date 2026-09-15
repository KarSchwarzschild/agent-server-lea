package cache

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"time"

	"github.com/redis/go-redis/v9"
)

// Config 缓存配置
type Config struct {
	BaseTTL      time.Duration // 基础 TTL
	Jitter       time.Duration // TTL 抖动范围 ±
	NullTTL      time.Duration // NULL 值缓存 TTL（更短）
	LockTimeout  time.Duration // 分布式锁超时
	LockRetries  int           // 等锁重试次数
	LockRetryDel time.Duration // 等锁重试延迟
}

// DefaultConfig 默认配置
var DefaultConfig = Config{
	BaseTTL:      5 * time.Minute,
	Jitter:       60 * time.Second,
	NullTTL:      1 * time.Minute,
	LockTimeout:  5 * time.Second,
	LockRetries:  3,
	LockRetryDel: 100 * time.Millisecond,
}

const nullValue = "__NULL__"

// Cache 缓存层（防护所有雪崩/击穿/穿透）
type Cache struct {
	client *redis.Client
	cfg    Config
}

// New 创建缓存实例
func New(client *redis.Client, cfg Config) *Cache {
	if cfg.BaseTTL == 0 {
		cfg = DefaultConfig
	}
	return &Cache{client: client, cfg: cfg}
}

// Get 读缓存，返回 (值, 缓存命中, 错误)
func (c *Cache) Get(ctx context.Context, key string) (string, bool, error) {
	val, err := c.client.Get(ctx, key).Result()
	if err == redis.Nil {
		return "", false, nil // 缓存不存在
	}
	if err != nil {
		log.Printf("cache get error: %v", err)
		return "", false, err
	}

	// 检查是否是 NULL 标记（穿透防护）
	if val == nullValue {
		return "", true, nil // 命中，但值是 NULL
	}

	return val, true, nil
}

// Set 写缓存，自动加抖动避免雪崩
func (c *Cache) Set(ctx context.Context, key, value string) error {
	ttl := c.randomTTL()
	return c.client.Set(ctx, key, value, ttl).Err()
}

// SetWithTTL 写缓存，指定 TTL
func (c *Cache) SetWithTTL(ctx context.Context, key, value string, ttl time.Duration) error {
	return c.client.Set(ctx, key, value, ttl).Err()
}

// SetNull 写 NULL 标记（穿透防护）
func (c *Cache) SetNull(ctx context.Context, key string) error {
	return c.client.Set(ctx, key, nullValue, c.cfg.NullTTL).Err()
}

// Delete 删除缓存
func (c *Cache) Delete(ctx context.Context, keys ...string) error {
	if len(keys) == 0 {
		return nil
	}
	return c.client.Del(ctx, keys...).Err()
}

// GetOrLoadJSON 读 JSON 缓存，miss 时调 loader 加载并缓存（击穿防护）
//
// 用法：
//   config, err := cache.GetOrLoadJSON(ctx, "agent:1", func(ctx context.Context) (any, error) {
//       return db.GetAgentConfig(1)
//   })
func (c *Cache) GetOrLoadJSON(ctx context.Context, key string, loader func(context.Context) (any, error)) (any, error) {
	// 第一步：尝试从缓存读
	val, hit, err := c.Get(ctx, key)
	if err != nil {
		// 缓存连接出错，直接降级到 loader
		log.Printf("cache unavailable, falling back to loader: %v", err)
		return c.loadAndCacheJSON(ctx, key, loader)
	}

	if hit {
		if val == "" { // NULL 值命中
			return nil, nil
		}
		// 缓存命中，反序列化
		var result any
		if err := json.Unmarshal([]byte(val), &result); err != nil {
			log.Printf("cache unmarshal error: %v", err)
			// 坏数据，清掉重新加载
			_ = c.Delete(ctx, key)
			return c.loadAndCacheJSON(ctx, key, loader)
		}
		return result, nil
	}

	// 第二步：缓存 miss，用分布式锁防击穿
	return c.getWithLock(ctx, key, loader)
}

// getWithLock 用分布式锁防止击穿
func (c *Cache) getWithLock(ctx context.Context, key string, loader func(context.Context) (any, error)) (any, error) {
	lockKey := fmt.Sprintf("lock:%s", key)

	// 尝试拿锁
	ok, err := c.client.SetNX(ctx, lockKey, "1", c.cfg.LockTimeout).Result()
	if err != nil {
		// 锁服务出错，直接加载（降级）
		log.Printf("lock unavailable: %v", err)
		return c.loadAndCacheJSON(ctx, key, loader)
	}

	if ok {
		// 拿到锁，加载数据并缓存
		defer c.client.Del(ctx, lockKey)
		return c.loadAndCacheJSON(ctx, key, loader)
	}

	// 没拿到锁，等待其他 goroutine 加载完成
	for i := 0; i < c.cfg.LockRetries; i++ {
		time.Sleep(c.cfg.LockRetryDel)

		val, hit, err := c.Get(ctx, key)
		if err != nil {
			continue // 重试
		}

		if hit {
			if val == "" {
				return nil, nil
			}
			var result any
			if err := json.Unmarshal([]byte(val), &result); err != nil {
				continue
			}
			return result, nil
		}
	}

	// 等了多次还是没有，直接加载（可能加锁的 goroutine 失败了）
	log.Printf("lock wait timeout for %s, fallback to direct load", key)
	return c.loadAndCacheJSON(ctx, key, loader)
}

// loadAndCacheJSON 调 loader 加载数据并缓存
func (c *Cache) loadAndCacheJSON(ctx context.Context, key string, loader func(context.Context) (any, error)) (any, error) {
	result, err := loader(ctx)

	if err != nil {
		// 加载失败，不缓存
		log.Printf("loader error for %s: %v", key, err)
		return nil, err
	}

	if result == nil {
		// NULL 值，缓存一个标记（穿透防护）
		_ = c.SetNull(ctx, key)
		return nil, nil
	}

	// 成功，缓存 JSON
	data, err := json.Marshal(result)
	if err != nil {
		log.Printf("marshal error for %s: %v", key, err)
		return result, nil // 返回结果但不缓存
	}

	_ = c.Set(ctx, key, string(data))
	return result, nil
}

// randomTTL 生成随机 TTL（防雪崩）
func (c *Cache) randomTTL() time.Duration {
	jitterMs := rand.Intn(int(c.cfg.Jitter.Milliseconds()))
	return c.cfg.BaseTTL + time.Duration(jitterMs-int(c.cfg.Jitter.Milliseconds())/2)*time.Millisecond
}

// InvalidatePattern 清除匹配的缓存键（发布失效事件）
//
// 用法：
//   cache.InvalidatePattern(ctx, "agent:*")  // 清除所有 agent 缓存
func (c *Cache) InvalidatePattern(ctx context.Context, pattern string) error {
	// 用 SCAN 扫出所有匹配的 key，然后删除
	var cursor uint64
	var keys []string
	for {
		newKeys, newCursor, err := c.client.Scan(ctx, cursor, pattern, 100).Result()
		if err != nil {
			return err
		}
		keys = append(keys, newKeys...)
		cursor = newCursor
		if cursor == 0 {
			break
		}
	}

	if len(keys) > 0 {
		return c.Delete(ctx, keys...)
	}
	return nil
}

// PublishInvalidation 发布缓存失效事件给其他 pod（可选）
func (c *Cache) PublishInvalidation(ctx context.Context, eventType string, id string) error {
	msg := fmt.Sprintf("%s:%s", eventType, id)
	return c.client.Publish(ctx, "cache:invalidate", msg).Err()
}
