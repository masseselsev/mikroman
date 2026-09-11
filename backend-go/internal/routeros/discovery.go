package routeros

import (
	"context"
	"errors"
)

// GetDHCPLeases reads /ip/dhcp-server/lease
func (c *Client) GetDHCPLeases(ctx context.Context) ([]DHCPLease, error) {
	var leases []DHCPLease
	if err := c.Get(ctx, "/ip/dhcp-server/lease", &leases); err != nil {
		if errors.Is(err, ErrNotFound) {
			return nil, nil
		}
		return nil, err
	}
	return leases, nil
}

// GetARPTable reads /ip/arp
func (c *Client) GetARPTable(ctx context.Context) ([]ARPEntry, error) {
	var entries []ARPEntry
	if err := c.Get(ctx, "/ip/arp", &entries); err != nil {
		return nil, err
	}
	return entries, nil
}

// GetWiFiRegistrations reads /interface/wifi/registration-table or falls back to /interface/wireless/registration-table
func (c *Client) GetWiFiRegistrations(ctx context.Context) ([]WiFiRegistration, error) {
	var regs []WiFiRegistration

	// 1. Try modern RouterOS v7.13+ WiFi (AX/BE devices)
	err := c.Get(ctx, "/interface/wifi/registration-table", &regs)
	if err == nil {
		return regs, nil
	}

	// 2. If 404 or unsupported, fallback to legacy wireless (AC devices)
	if errors.Is(err, ErrNotFound) {
		if err2 := c.Get(ctx, "/interface/wireless/registration-table", &regs); err2 == nil {
			return regs, nil
		}
		return nil, nil
	}

	return nil, err
}
