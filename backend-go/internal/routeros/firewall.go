package routeros

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strings"
)

func cleanIP(addr string) string {
	s := strings.TrimSpace(addr)
	if idx := strings.Index(s, ":"); idx != -1 && !strings.Contains(s, "]") {
		s = s[:idx]
	}
	return strings.TrimSpace(strings.Split(s, "/")[0])
}

// GetMangleRules reads /ip/firewall/mangle
func (c *Client) GetMangleRules(ctx context.Context) ([]MangleRule, error) {
	var rules []MangleRule
	if err := c.Get(ctx, "/ip/firewall/mangle", &rules); err != nil {
		return nil, err
	}
	return rules, nil
}

// CreateMangleRule adds a rule via PUT /ip/firewall/mangle
func (c *Client) CreateMangleRule(ctx context.Context, r *MangleRule) error {
	var res MangleRule
	if err := c.Put(ctx, "/ip/firewall/mangle", r, &res); err != nil {
		return err
	}
	if res.ID != "" {
		r.ID = res.ID
	}
	return nil
}

// UpdateMangleRule updates fields via PATCH /ip/firewall/mangle/{id}
func (c *Client) UpdateMangleRule(ctx context.Context, id string, fields map[string]interface{}) error {
	path := fmt.Sprintf("/ip/firewall/mangle/%s", id)
	return c.Patch(ctx, path, fields, nil)
}

// DeleteMangleRule removes a rule via DELETE /ip/firewall/mangle/{id}
func (c *Client) DeleteMangleRule(ctx context.Context, id string) error {
	path := fmt.Sprintf("/ip/firewall/mangle/%s", id)
	err := c.Delete(ctx, path)
	if errors.Is(err, ErrNotFound) {
		return nil
	}
	return err
}

// GetAddressList reads /ip/firewall/address-list filtering by list name
func (c *Client) GetAddressList(ctx context.Context, listName string) ([]AddressListEntry, error) {
	var entries []AddressListEntry
	if err := c.Get(ctx, "/ip/firewall/address-list", &entries); err != nil {
		return nil, err
	}

	if listName == "" {
		return entries, nil
	}

	var filtered []AddressListEntry
	for _, e := range entries {
		if strings.EqualFold(e.List, listName) {
			filtered = append(filtered, e)
		}
	}
	return filtered, nil
}

// AddToAddressList adds an address to a list via PUT /ip/firewall/address-list
func (c *Client) AddToAddressList(ctx context.Context, list, address, comment string) error {
	entry := AddressListEntry{
		List:     list,
		Address:  address,
		Comment:  comment,
		Disabled: "false",
	}
	return c.Put(ctx, "/ip/firewall/address-list", &entry, nil)
}

// RemoveFromAddressList removes an entry via DELETE /ip/firewall/address-list/{id}
func (c *Client) RemoveFromAddressList(ctx context.Context, id string) error {
	path := fmt.Sprintf("/ip/firewall/address-list/%s", id)
	err := c.Delete(ctx, path)
	if errors.Is(err, ErrNotFound) {
		return nil
	}
	return err
}

// GetFilterRules reads /ip/firewall/filter
func (c *Client) GetFilterRules(ctx context.Context) ([]FilterRule, error) {
	var rules []FilterRule
	if err := c.Get(ctx, "/ip/firewall/filter", &rules); err != nil {
		return nil, err
	}
	return rules, nil
}

// CleanLegacyRawDropRules removes any dangerous raw prerouting drop rules
func (c *Client) CleanLegacyRawDropRules(ctx context.Context) error {
	var rawRules []FilterRule
	if err := c.Get(ctx, "/ip/firewall/raw", &rawRules); err != nil {
		return nil
	}

	for _, r := range rawRules {
		if strings.Contains(r.Comment, "mikroman:drop_blocked_raw") && r.ID != "" {
			_ = c.Delete(ctx, fmt.Sprintf("/ip/firewall/raw/%s", r.ID))
		}
	}
	return nil
}

// EnsurePauseRules ensures safe forward drop with explicit port 53 (DNS) allowance
func (c *Client) EnsurePauseRules(ctx context.Context) error {
	// First clean legacy raw drop rules
	_ = c.CleanLegacyRawDropRules(ctx)

	filterRules, err := c.GetFilterRules(ctx)
	if err != nil {
		return err
	}

	hasDrop := false
	for _, r := range filterRules {
		if strings.Contains(r.Comment, "mikroman:drop_blocked_internet") {
			hasDrop = true
			break
		}
	}

	if !hasDrop {
		dropRule := FilterRule{
			Chain:          "forward",
			Action:         "drop",
			SrcAddressList: "mikroman_blocked",
			DstAddressList: "!mikroman_allowed_lans",
			Comment:        "mikroman:drop_blocked_internet",
			Disabled:       "false",
		}
		_ = c.Put(ctx, "/ip/firewall/filter", &dropRule, nil)
	}

	return nil
}

// EnsureFastTrackExemption ensures FastTrack rule excludes mikroman_queued addresses so Simple Queues take effect.
func (c *Client) EnsureFastTrackExemption(ctx context.Context) error {
	filterRules, err := c.GetFilterRules(ctx)
	if err != nil {
		return err
	}

	for _, r := range filterRules {
		if r.Action == "fasttrack-connection" && r.ID != "" {
			if r.SrcAddressList != "!mikroman_queued" || r.DstAddressList != "!mikroman_queued" {
				path := fmt.Sprintf("/ip/firewall/filter/%s", r.ID)
				err := c.Patch(ctx, path, map[string]interface{}{
					"src-address-list": "!mikroman_queued",
					"dst-address-list": "!mikroman_queued",
				}, nil)
				if err != nil {
					slog.Warn("Failed to patch FastTrack exemption on RouterOS", "id", r.ID, "err", err)
				} else {
					slog.Info("Configured FastTrack exemption rule with !mikroman_queued", "id", r.ID)
				}
			}
		}
	}
	return nil
}

// GetFirewallConnections reads /ip/firewall/connection
func (c *Client) GetFirewallConnections(ctx context.Context) ([]FirewallConnection, error) {
	var conns []FirewallConnection
	if err := c.Get(ctx, "/ip/firewall/connection", &conns); err != nil {
		return nil, err
	}
	return conns, nil
}

// DeleteFirewallConnection removes an active connection via POST /ip/firewall/connection/remove or DELETE fallback
func (c *Client) DeleteFirewallConnection(ctx context.Context, id string) error {
	cleanID := strings.TrimSpace(id)
	if cleanID == "" {
		return nil
	}
	// Try REST POST command first
	payload := map[string]string{"numbers": cleanID}
	if err := c.Post(ctx, "/ip/firewall/connection/remove", payload, nil); err == nil {
		return nil
	}
	// Fallback to DELETE
	path := fmt.Sprintf("/ip/firewall/connection/%s", cleanID)
	return c.Delete(ctx, path)
}

// FlushConnectionsForIP removes active connections involving an IP so queues take immediate effect without waiting for timeouts
func (c *Client) FlushConnectionsForIP(ctx context.Context, ip string) error {
	cleanTarget := strings.TrimSpace(ip)
	if cleanTarget == "" {
		return nil
	}
	immunes := c.GetImmuneIPs()
	if immunes[cleanTarget] {
		return nil
	}

	conns, err := c.GetFirewallConnections(ctx)
	if err != nil {
		return err
	}

	for _, conn := range conns {
		src := cleanIP(conn.SrcAddress)
		dst := cleanIP(conn.DstAddress)
		rSrc := cleanIP(conn.ReplySrcAddress)
		rDst := cleanIP(conn.ReplyDstAddress)

		// Never kill management connections to immune IPs
		if immunes[src] || immunes[dst] || immunes[rSrc] || immunes[rDst] {
			continue
		}

		if src == cleanTarget || dst == cleanTarget || rSrc == cleanTarget || rDst == cleanTarget {
			if conn.ID != "" {
				_ = c.DeleteFirewallConnection(ctx, conn.ID)
			}
		}
	}
	return nil
}
