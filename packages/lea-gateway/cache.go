package main

import (
	"context"
	"log"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

type redisTenantRoleCache struct {
	client *redis.Client
}

func (c *redisTenantRoleCache) Get(ctx context.Context, key string) (string, error) {
	return c.client.Get(ctx, key).Result()
}

func (c *redisTenantRoleCache) Set(ctx context.Context, key, value string, ttl time.Duration) error {
	return c.client.Set(ctx, key, value, ttl).Err()
}

func newTenantRoleCache() (tenantRoleCache, time.Duration) {
	url := strings.TrimSpace(os.Getenv("REDIS_URL"))
	if url == "" {
		return nil, 0
	}
	options, err := redis.ParseURL(url)
	if err != nil {
		log.Printf("tenant role cache disabled: invalid REDIS_URL: %v", err)
		return nil, 0
	}
	// 缓存只是一条快路径；Redis 故障时必须尽快回退到实时租户校验。
	options.DialTimeout = 200 * time.Millisecond
	options.ReadTimeout = 200 * time.Millisecond
	options.WriteTimeout = 200 * time.Millisecond
	ttl := 60 * time.Second
	if raw := strings.TrimSpace(os.Getenv("TENANT_MEMBERSHIP_CACHE_TTL_SECONDS")); raw != "" {
		seconds, err := strconv.Atoi(raw)
		if err != nil || seconds < 1 {
			log.Printf("tenant role cache disabled: invalid TENANT_MEMBERSHIP_CACHE_TTL_SECONDS=%q", raw)
			return nil, 0
		}
		ttl = time.Duration(seconds) * time.Second
	}
	return &redisTenantRoleCache{client: redis.NewClient(options)}, ttl
}
