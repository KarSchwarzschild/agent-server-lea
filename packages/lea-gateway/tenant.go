package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"
)

type tenantMembershipClient struct {
	url         string
	client      *http.Client
	roleCache   tenantRoleCache
	cacheTTL    time.Duration
	cachePrefix string
}

type tenantRoleCache interface {
	Get(context.Context, string) (string, error)
	Set(context.Context, string, string, time.Duration) error
}

type tenantMembershipResponse struct {
	Data []tenantMembership `json:"data"`
}

type tenantMembership struct {
	TenantID           string `json:"tenantId"`
	TenantType         string `json:"tenantType"`
	Role               string `json:"role"`
	UserTenantRoleName string `json:"userTenantRoleName"`
	Disabled           bool   `json:"disabled"`
}

func (c *tenantMembershipClient) list(ctx context.Context, authorization string) ([]tenantMembership, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.url, nil)
	if err != nil {
		return nil, fmt.Errorf("create tenant membership request: %w", err)
	}
	req.Header.Set("Authorization", authorization)
	req.Header.Set("Accept", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("tenant membership request: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("tenant membership returned status %d", resp.StatusCode)
	}
	var payload tenantMembershipResponse
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return nil, fmt.Errorf("decode tenant membership: %w", err)
	}
	return payload.Data, nil
}

func (c *tenantMembershipClient) resolve(ctx context.Context, authorization, userID, selectedTenant string) (string, error) {
	selectedTenant = strings.TrimSpace(selectedTenant)
	if selectedTenant == "" {
		return "", fmt.Errorf("missing selected tenant")
	}
	cacheKey := tenantRoleCacheKey(c.cachePrefix, userID, selectedTenant)
	if c.roleCache != nil {
		if role, err := c.roleCache.Get(ctx, cacheKey); err == nil && role != "" {
			return role, nil
		}
	}
	items, err := c.list(ctx, authorization)
	if err != nil {
		return "", err
	}
	for _, item := range items {
		if item.TenantID == selectedTenant && !item.Disabled {
			role := mapTenantRole(item.TenantType, item.UserTenantRoleName, item.Role)
			if c.roleCache != nil && c.cacheTTL > 0 {
				_ = c.roleCache.Set(ctx, cacheKey, role, c.cacheTTL)
			}
			return role, nil
		}
	}
	return "", fmt.Errorf("user is not a member of selected tenant")
}

func tenantRoleCacheKey(prefix, userID, tenantID string) string {
	digest := sha256.Sum256([]byte(userID + "\x00" + tenantID))
	prefix = strings.TrimSpace(prefix)
	if prefix != "" && !strings.HasSuffix(prefix, ":") {
		prefix += ":"
	}
	return fmt.Sprintf("%sgateway:tenant-role:v1:%x", prefix, digest)
}

func mapTenantRole(tenantType, roleName, role string) string {
	if strings.EqualFold(tenantType, "super") &&
		(strings.EqualFold(roleName, "管理者") || strings.EqualFold(role, "Master")) {
		return "platform_admin"
	}
	if strings.EqualFold(roleName, "管理者") || strings.EqualFold(role, "Master") {
		return "admin"
	}
	return "user"
}
