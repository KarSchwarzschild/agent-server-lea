package persistence

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"time"

	"github.com/redis/go-redis/v9"
	"gitlab.ouc-online.com.cn/aibase/lea-agent/internal/cache"
)

// MessageCache 提供消息的 Redis + MySQL 分层缓存
type MessageCache struct {
	cache  *cache.Cache
	db     *sql.DB
	client *redis.Client

	// 配置
	maxCachedMessages int           // 每个会话最多缓存多少条消息
	contextSize       int           // 上下文（prompt）用多少条消息
	contextTTL        time.Duration // 上下文缓存 TTL
	messagesTTL       time.Duration // 消息列表缓存 TTL
}

// NewMessageCache 创建消息缓存实例
func NewMessageCache(
	cacheLayer *cache.Cache,
	db *sql.DB,
	redisClient *redis.Client,
) *MessageCache {
	return &MessageCache{
		cache:             cacheLayer,
		db:                db,
		client:            redisClient,
		maxCachedMessages: 100,      // 每个会话最多缓存 100 条
		contextSize:       10,       // 上下文用最近 10 条
		contextTTL:        1 * time.Hour,
		messagesTTL:       24 * time.Hour,
	}
}

// SessionMessageCache 单个会话的消息缓存项
type SessionMessageCache struct {
	SessionID string       `json:"session_id"`
	Messages  []MessageRow `json:"messages"`
	UpdatedAt time.Time    `json:"updated_at"`
}

// GetSessionMessages 获取会话的消息历史（带缓存）
//
// 缓存策略：
//   - Redis: 最近 N 条消息，TTL 24h
//   - MySQL: 完整历史
func (mc *MessageCache) GetSessionMessages(
	ctx context.Context,
	sessionID string,
) ([]MessageRow, error) {
	if mc.cache == nil {
		// 缓存不可用，直接查数据库
		return GetMessages(ctx, mc.db, sessionID)
	}

	key := fmt.Sprintf("session:%s:messages:recent", sessionID)

	// 使用 GetOrLoad 自动处理缓存和加载
	data, err := mc.cache.GetOrLoad(ctx, key, func(ctx context.Context) ([]byte, error) {
		// loader：从 MySQL 加载
		msgs, err := GetMessages(ctx, mc.db, sessionID)
		if err != nil {
			return nil, err
		}

		// 只缓存最近 N 条，避免缓存过大
		if len(msgs) > mc.maxCachedMessages {
			msgs = msgs[len(msgs)-mc.maxCachedMessages:]
		}

		return json.Marshal(msgs)
	})

	if err != nil {
		return nil, err
	}

	if data == nil {
		return []MessageRow{}, nil
	}

	var messages []MessageRow
	if err := json.Unmarshal(data, &messages); err != nil {
		log.Printf("failed to unmarshal cached messages: %v", err)
		return nil, err
	}

	return messages, nil
}

// GetSessionContext 获取会话的上下文（最近 N 条消息，用于 prompt）
//
// 缓存策略：
//   - Redis: 最近 10 条消息，TTL 1h（活跃会话自动延期）
//   - MySQL: 完整历史
func (mc *MessageCache) GetSessionContext(
	ctx context.Context,
	sessionID string,
) ([]MessageRow, error) {
	if mc.cache == nil {
		// 缓存不可用，直接查数据库
		msgs, err := GetMessages(ctx, mc.db, sessionID)
		if err != nil {
			return nil, err
		}
		// 只返回最近 N 条
		if len(msgs) > mc.contextSize {
			msgs = msgs[len(msgs)-mc.contextSize:]
		}
		return msgs, nil
	}

	key := fmt.Sprintf("session:%s:context", sessionID)

	// 设置更短的 TTL 和加载函数
	data, err := mc.cache.GetOrLoad(ctx, key, func(ctx context.Context) ([]byte, error) {
		// 从 MySQL 获取最近 N 条消息
		msgs, err := GetMessages(ctx, mc.db, sessionID)
		if err != nil {
			return nil, err
		}

		// 只取最后 contextSize 条用于 prompt
		if len(msgs) > mc.contextSize {
			msgs = msgs[len(msgs)-mc.contextSize:]
		}

		return json.Marshal(msgs)
	})

	if err != nil {
		return nil, err
	}

	if data == nil {
		return []MessageRow{}, nil
	}

	var messages []MessageRow
	if err := json.Unmarshal(data, &messages); err != nil {
		log.Printf("failed to unmarshal cached context: %v", err)
		return nil, err
	}

	return messages, nil
}

// SaveMessageAndUpdateCache 保存消息并更新缓存
//
// 流程：
//   1. 写入 MySQL（事务）
//   2. 更新 Redis 缓存
//   3. 发布 PubSub 事件（多副本同步）
func (mc *MessageCache) SaveMessageAndUpdateCache(
	ctx context.Context,
	sessionID string,
	msg Message,
) error {
	// 1. 先写 MySQL
	if err := SaveMessages(ctx, mc.db, msg); err != nil {
		return err
	}

	// 2. 更新 Redis 缓存
	if mc.client != nil {
		// 使用增量更新而不是全量失效
		if err := mc.appendMessageToCache(ctx, sessionID, msg); err != nil {
			log.Printf("warning: failed to update message cache: %v", err)
			// 不要因为缓存更新失败就返回错误，消息已经保存到数据库了
		}

		// 更新上下文缓存
		if err := mc.updateContextCache(ctx, sessionID); err != nil {
			log.Printf("warning: failed to update context cache: %v", err)
		}
	}

	// 3. 发布事件通知其他副本
	// TODO: 实现 PubSub 发布逻辑（Phase 3）

	return nil
}

// appendMessageToCache 增量更新消息缓存，而不是全量失效
func (mc *MessageCache) appendMessageToCache(
	ctx context.Context,
	sessionID string,
	newMsg Message,
) error {
	if mc.client == nil {
		return nil
	}

	key := fmt.Sprintf("session:%s:messages:recent", sessionID)
	lockKey := key + ":lock"

	// 获取分布式锁，避免并发更新冲突
	acquired, err := mc.client.SetNX(ctx, lockKey, "1", 5*time.Second).Result()
	if err != nil || !acquired {
		// 获取锁失败，直接返回不更新缓存
		// 下一个读请求会触发缓存重新加载
		return nil
	}
	defer mc.client.Del(ctx, lockKey)

	// 读当前缓存
	data, err := mc.client.Get(ctx, key).Bytes()
	if err != nil && err != redis.Nil {
		return err
	}

	var msgs []MessageRow
	if err == nil && string(data) != "NULL" {
		if err := json.Unmarshal(data, &msgs); err != nil {
			return err
		}
	}

	// 追加新消息
	msgs = append(msgs, MessageRow{
		SessionID: newMsg.SessionID,
		Role:      newMsg.Role,
		Content:   newMsg.Content,
		CreatedAt: time.Now(),
		Extra:     nil,
	})

	// 如果超过限制，截断旧消息
	if len(msgs) > mc.maxCachedMessages {
		msgs = msgs[len(msgs)-mc.maxCachedMessages:]
	}

	// 写回 Redis
	data, err = json.Marshal(msgs)
	if err != nil {
		return err
	}

	return mc.client.Set(ctx, key, data, mc.messagesTTL).Err()
}

// updateContextCache 更新上下文缓存（最近 N 条）
func (mc *MessageCache) updateContextCache(
	ctx context.Context,
	sessionID string,
) error {
	if mc.client == nil {
		return nil
	}

	// 从消息缓存读取最新的消息
	msgs, err := mc.GetSessionMessages(ctx, sessionID)
	if err != nil {
		return err
	}

	// 只取最后 contextSize 条
	context := msgs
	if len(msgs) > mc.contextSize {
		context = msgs[len(msgs)-mc.contextSize:]
	}

	// 写入上下文缓存
	key := fmt.Sprintf("session:%s:context", sessionID)
	data, err := json.Marshal(context)
	if err != nil {
		return err
	}

	return mc.client.Set(ctx, key, data, mc.contextTTL).Err()
}

// InvalidateSessionCache 失效某个会话的所有缓存
// 用于会话删除或需要强制刷新的场景
func (mc *MessageCache) InvalidateSessionCache(
	ctx context.Context,
	sessionID string,
) error {
	if mc.cache == nil {
		return nil
	}

	keys := []string{
		fmt.Sprintf("session:%s:messages:recent", sessionID),
		fmt.Sprintf("session:%s:context", sessionID),
		fmt.Sprintf("session:%s:stats", sessionID),
	}

	for _, key := range keys {
		if err := mc.cache.Invalidate(ctx, key); err != nil {
			log.Printf("warning: failed to invalidate cache key %s: %v", key, err)
		}
	}

	return nil
}

// GetCacheStats 获取缓存统计信息（用于监控）
type CacheStats struct {
	SessionID        string
	CachedMessages   int
	ContextMessages  int
	LastCacheUpdate  time.Time
	CacheHitRate     float64
	AverageLoadTime  int64 // 单位: 毫秒
}

// GetCacheStatsForSession 获取某个会话的缓存统计
func (mc *MessageCache) GetCacheStatsForSession(
	ctx context.Context,
	sessionID string,
) (*CacheStats, error) {
	if mc.client == nil {
		return nil, fmt.Errorf("redis client not available")
	}

	stats := &CacheStats{
		SessionID: sessionID,
	}

	// 读消息缓存
	msgKey := fmt.Sprintf("session:%s:messages:recent", sessionID)
	msgData, err := mc.client.Get(ctx, msgKey).Bytes()
	if err == nil && string(msgData) != "NULL" {
		var msgs []MessageRow
		if err := json.Unmarshal(msgData, &msgs); err == nil {
			stats.CachedMessages = len(msgs)
		}
	}

	// 读上下文缓存
	ctxKey := fmt.Sprintf("session:%s:context", sessionID)
	ctxData, err := mc.client.Get(ctx, ctxKey).Bytes()
	if err == nil && string(ctxData) != "NULL" {
		var msgs []MessageRow
		if err := json.Unmarshal(ctxData, &msgs); err == nil {
			stats.ContextMessages = len(msgs)
		}
	}

	return stats, nil
}
