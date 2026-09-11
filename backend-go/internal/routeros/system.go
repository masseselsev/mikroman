package routeros

import (
	"context"
	"strings"
)

// GetSystemResource reads /system/resource
func (c *Client) GetSystemResource(ctx context.Context) (*Resource, error) {
	var res Resource
	if err := c.Get(ctx, "/system/resource", &res); err != nil {
		return nil, err
	}
	return &res, nil
}

// GetSystemHealth reads /system/health (returns empty slice if not supported on hardware)
func (c *Client) GetSystemHealth(ctx context.Context) ([]HealthItem, error) {
	var items []HealthItem
	if err := c.Get(ctx, "/system/health", &items); err != nil {
		if strings.Contains(err.Error(), "404") || strings.Contains(err.Error(), "no such command") {
			return nil, nil
		}
		return nil, err
	}
	return items, nil
}

// GetRouterBoard reads /system/routerboard
func (c *Client) GetRouterBoard(ctx context.Context) (*RouterBoard, error) {
	var rb RouterBoard
	if err := c.Get(ctx, "/system/routerboard", &rb); err != nil {
		return nil, err
	}
	return &rb, nil
}

// Reboot triggers a router reboot via POST /system/reboot
func (c *Client) Reboot(ctx context.Context) error {
	return c.Post(ctx, "/system/reboot", nil, nil)
}
