package routeros

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"strconv"
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
	var raw json.RawMessage
	if err := c.Get(ctx, "/system/health", &raw); err != nil {
		if strings.Contains(err.Error(), "404") || strings.Contains(err.Error(), "no such command") {
			return nil, nil
		}
		return nil, err
	}

	trimmed := bytes.TrimSpace(raw)
	if len(trimmed) == 0 {
		return nil, nil
	}

	var items []HealthItem
	if trimmed[0] == '[' {
		var rawList []map[string]interface{}
		if err := json.Unmarshal(trimmed, &rawList); err == nil {
			for _, m := range rawList {
				name := fmt.Sprintf("%v", m["name"])
				val := fmt.Sprintf("%v", m["value"])
				if name != "" && name != "<nil>" {
					items = append(items, HealthItem{
						Name:  name,
						Value: val,
						Type:  fmt.Sprintf("%v", m["type"]),
					})
				} else {
					for k, v := range m {
						items = append(items, HealthItem{
							Name:  k,
							Value: fmt.Sprintf("%v", v),
						})
					}
				}
			}
			return items, nil
		}
	} else if trimmed[0] == '{' {
		var rawObj map[string]interface{}
		if err := json.Unmarshal(trimmed, &rawObj); err == nil {
			for k, v := range rawObj {
				items = append(items, HealthItem{
					Name:  k,
					Value: fmt.Sprintf("%v", v),
				})
			}
			return items, nil
		}
	}

	return items, nil
}

// ExtractHealthMetrics extracts temperature and voltage from HealthItem slice.
// Supports: "temperature", "cpu-temperature", "board-temperature1", "sfp-temperature",
// "voltage", "supply-voltage", etc.
func ExtractHealthMetrics(items []HealthItem) (temp *float64, volt *float64) {
	for _, item := range items {
		name := strings.ToLower(strings.TrimSpace(item.Name))
		valStr := strings.TrimSpace(item.Value)
		if valStr == "" || valStr == "<nil>" {
			continue
		}
		valStr = strings.TrimSuffix(valStr, "C")
		valStr = strings.TrimSuffix(valStr, "c")
		valStr = strings.TrimSuffix(valStr, "V")
		valStr = strings.TrimSuffix(valStr, "v")
		valStr = strings.TrimSpace(valStr)

		v, err := strconv.ParseFloat(valStr, 64)
		if err != nil {
			continue
		}

		if strings.Contains(name, "temp") {
			if temp == nil || strings.Contains(name, "cpu") || name == "temperature" {
				temp = &v
			}
		} else if strings.Contains(name, "volt") {
			if volt == nil || name == "voltage" {
				volt = &v
			}
		}
	}
	return temp, volt
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

// InterfaceTrafficRate holds instantaneous traffic rates from /interface/monitor-traffic.
type InterfaceTrafficRate struct {
	Name               string  `json:"name"`
	RxBitsPerSecond    float64 `json:"rx-bits-per-second"`
	TxBitsPerSecond    float64 `json:"tx-bits-per-second"`
	RxPacketsPerSecond float64 `json:"rx-packets-per-second"`
	TxPacketsPerSecond float64 `json:"tx-packets-per-second"`
}

// MonitorInterfaceTraffic fetches live interface bandwidth rates via POST /interface/monitor-traffic once.
func (c *Client) MonitorInterfaceTraffic(ctx context.Context, interfaces []string) ([]InterfaceTrafficRate, error) {
	if len(interfaces) == 0 {
		return nil, nil
	}
	var rates []InterfaceTrafficRate
	payload := map[string]string{
		"interface": strings.Join(interfaces, ","),
		"once":      "",
	}
	if err := c.Post(ctx, "/interface/monitor-traffic", payload, &rates); err != nil {
		return nil, err
	}
	return rates, nil
}

// GetCloudPublicAddress reads /ip/cloud to retrieve the public IP if DDNS is active.
func (c *Client) GetCloudPublicAddress(ctx context.Context) (string, error) {
	var data map[string]interface{}
	if err := c.Get(ctx, "/ip/cloud", &data); err != nil {
		return "", err
	}
	if addr, ok := data["public-address"].(string); ok {
		clean := strings.TrimSpace(addr)
		if clean != "" && clean != "0.0.0.0" {
			return clean, nil
		}
	}
	return "", nil
}

// GetIPAddresses reads /ip/address to retrieve configured IP addresses.
func (c *Client) GetIPAddresses(ctx context.Context) ([]IPAddress, error) {
	var addrs []IPAddress
	if err := c.Get(ctx, "/ip/address", &addrs); err != nil {
		return nil, err
	}
	return addrs, nil
}
