package cache

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand"
	"time"

	"github.com/redis/go-redis/v9"
)

// Config 缓存配置
type Config struct {
	// DefaultTTL 默认缓存时长
	DefaultTTL time.Duration
	// NullTTL 空值标记的缓存时长（防穿透）
	NullTTL time.Duration
	// LockTimeout 分布式锁超时时间（防击穿）
	LockTimeout time.Duration
	// JitterPercent TTL 随机抖动百分比（防雪崩），范围 0-100
	JitterPercent int
}

// DefaultConfig 默认配置
var DefaultConfig = Config{
	DefaultTTL:    5 * time.Minute,
	NullTTL:       1 * time.Minute,
	LockTimeout:   10 * time.Second,
	JitterPercent: 10, // 10% 抖动
}

// Cache 缓存层，提供三大防护：雪崩/击穿/穿透
type Cache struct {
	client *redis.Client
	config Config
}

// New 创建缓存实例
func New(client *redis.Client, config Config) *Cache {
	if config.DefaultTTL == 0 {
		config = DefaultConfig
	}
	return &Cache{
		client: client,
		config: config,
	}
}

// GetOrLoad 缓存未命中时调用 loader 加载数据
//
// 防护措施：
//  - 雪崩：TTL 加随机抖动
//  - 击穿：分布式锁（同一个 key 只有一个 loader 在跑）
//  - 穿透：NULL 值标记（loader 返回 nil 也缓存）
func (c *Cache) GetOrLoad(ctx context.Context, key string, loader func(context.Context) ([]byte, error)) ([]byte, error) {
	// 降级保护：Redis 不可用时直接回源
	if c.client == nil {
		return loader(ctx)
	}

	// 1. 尝试从缓存读取
	data, err := c.client.Get(ctx, key).Bytes()
	if err == nil {
		// 命中：检查是否是 NULL 标记
		if string(data) == "NULL" {
			return nil, nil
		}
		return data, nil
	}
	if !errors.Is(err, redis.Nil) {
		// Redis 异常（网络/超时等）：降级回源
		return loader(ctx)
	}

	// 2. 缓存未命中：尝试获取分布式锁
	lockKey := key + ":lock"
	acquired, err := c.acquireLock(ctx, lockKey)
	if err != nil || !acquired {
		// 获取锁失败：说明有其他进程在加载，等一会儿再读缓存
		time.Sleep(100 * time.Millisecond)
		data, err := c.client.Get(ctx, key).Bytes()
		if err == nil && string(data) != "NULL" {
			return data, nil
		}
		if err == nil && string(data) == "NULL" {
			return nil, nil
		}
		// 仍未命中：降级回源
		return loader(ctx)
	}
	defer c.releaseLock(ctx, lockKey)

	// 3. 持有锁：调用 loader
	data, err = loader(ctx)
	if err != nil {
		// loader 失败不缓存，直接返回错误
		return nil, err
	}

	// 4. 写入缓存（包括 NULL 值）
	ttl := c.ttlWithJitter(c.config.DefaultTTL)
	if data == nil {
		// 穿透防护：空值用较短 TTL
		c.client.Set(ctx, key, "NULL", c.config.NullTTL)
		return nil, nil
	}

	c.client.Set(ctx, key, data, ttl)
	return data, nil
}

// GetOrLoadJSON 是 GetOrLoad 的 JSON 版本
func (c *Cache) GetOrLoadJSON(ctx context.Context, key string, loader func(context.Context) (any, error)) (any, error) {
	data, err := c.GetOrLoad(ctx, key, func(ctx context.Context) ([]byte, error) {
		result, err := loader(ctx)
		if err != nil {
			return nil, err
		}
		if result == nil {
			return nil, nil
		}
		return json.Marshal(result)
	})

	if err != nil {
		return nil, err
	}
	if data == nil {
		return nil, nil
	}

	var result any
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, err
	}
	return result, nil
}

// Invalidate 使缓存失效
func (c *Cache) Invalidate(ctx context.Context, key string) error {
	if c.client == nil {
		return nil
	}
	return c.client.Del(ctx, key).Err()
}

// acquireLock 尝试获取分布式锁（SET NX + TTL）
func (c *Cache) acquireLock(ctx context.Context, lockKey string) (bool, error) {
	return c.client.SetNX(ctx, lockKey, "1", c.config.LockTimeout).Result()
}

// releaseLock 释放分布式锁
func (c *Cache) releaseLock(ctx context.Context, lockKey string) error {
	return c.client.Del(ctx, lockKey).Err()
}

// ttlWithJitter 给 TTL 加随机抖动（防雪崩）
func (c *Cache) ttlWithJitter(base time.Duration) time.Duration {
	if c.config.JitterPercent <= 0 {
		return base
	}
	jitter := int64(base) * int64(c.config.JitterPercent) / 100
	delta := rand.Int63n(jitter*2) - jitter
	return time.Duration(int64(base) + delta)
}

// InvalidatePattern 批量删除匹配 pattern 的 key（运维用）
func (c *Cache) InvalidatePattern(ctx context.Context, pattern string) error {
	if c.client == nil {
		return nil
	}

	var cursor uint64
	var keys []string

	for {
		var batch []string
		var err error
		batch, cursor, err = c.client.Scan(ctx, cursor, pattern, 100).Result()
		if err != nil {
			return fmt.Errorf("scan failed: %w", err)
		}
		keys = append(keys, batch...)
		if cursor == 0 {
			break
		}
	}

	if len(keys) > 0 {
		return c.client.Del(ctx, keys...).Err()
	}
	return nil
}
