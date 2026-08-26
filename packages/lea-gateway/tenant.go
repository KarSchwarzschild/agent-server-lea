package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
)

type tenantMembershipClient struct {
	url    string
	client *http.Client
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

func (c *tenantMembershipClient) resolve(authorization, selectedTenant string) (string, error) {
	selectedTenant = strings.TrimSpace(selectedTenant)
	if selectedTenant == "" {
		return "", fmt.Errorf("missing selected tenant")
	}
	req, err := http.NewRequest(http.MethodGet, c.url, nil)
	if err != nil {
		return "", fmt.Errorf("create tenant membership request: %w", err)
	}
	req.Header.Set("Authorization", authorization)
	req.Header.Set("Accept", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("tenant membership request: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("tenant membership returned status %d", resp.StatusCode)
	}
	var payload tenantMembershipResponse
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return "", fmt.Errorf("decode tenant membership: %w", err)
	}
	for _, item := range payload.Data {
		if item.TenantID == selectedTenant && !item.Disabled {
			return mapTenantRole(item.TenantType, item.UserTenantRoleName, item.Role), nil
		}
	}
	return "", fmt.Errorf("user is not a member of selected tenant")
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
