package routeros

import (
	"context"
	"fmt"
	"regexp"
	"strings"
)

// PackageUpdateStatus represents RouterOS /system/package/update state
type PackageUpdateStatus struct {
	InstalledVersion string `json:"installed-version"`
	LatestVersion    string `json:"latest-version"`
	Channel          string `json:"channel"`
	Status           string `json:"status"`
}

// GetPackageUpdateStatus reads /system/package/update
func (c *Client) GetPackageUpdateStatus(ctx context.Context) (*PackageUpdateStatus, error) {
	var status PackageUpdateStatus
	if err := c.Get(ctx, "/system/package/update", &status); err != nil {
		return nil, err
	}

	// Clean installed version (strip parenthesized channel suffix like " (stable)")
	re := regexp.MustCompile(`\s*\(.*?\)`)
	status.InstalledVersion = strings.TrimSpace(re.ReplaceAllString(status.InstalledVersion, ""))
	status.LatestVersion = strings.TrimSpace(status.LatestVersion)
	status.Channel = strings.TrimSpace(status.Channel)
	if status.Channel == "" {
		status.Channel = "stable"
	}
	return &status, nil
}

// CheckForPackageUpdates triggers an on-demand update check via POST /system/package/update/check-for-updates
func (c *Client) CheckForPackageUpdates(ctx context.Context) (*PackageUpdateStatus, error) {
	_ = c.Post(ctx, "/system/package/update/check-for-updates", map[string]string{}, nil)
	return c.GetPackageUpdateStatus(ctx)
}

// SetPackageUpdateChannel switches the update channel and refreshes check
func (c *Client) SetPackageUpdateChannel(ctx context.Context, channel string) (*PackageUpdateStatus, error) {
	validChannels := map[string]bool{
		"stable":      true,
		"long-term":   true,
		"testing":     true,
		"development": true,
	}
	cleanChan := strings.ToLower(strings.TrimSpace(channel))
	if !validChannels[cleanChan] {
		return nil, fmt.Errorf("invalid channel %q, expected stable, long-term, testing, or development", channel)
	}

	payload := map[string]string{"channel": cleanChan}
	if err := c.Post(ctx, "/system/package/update/set", payload, nil); err != nil {
		return nil, err
	}
	return c.CheckForPackageUpdates(ctx)
}

// InstallPackageUpdate triggers package download and router reboot via POST /system/package/update/install
func (c *Client) InstallPackageUpdate(ctx context.Context) error {
	// RouterOS reboots immediately, dropping connection, which is normal and expected
	_ = c.Post(ctx, "/system/package/update/install", map[string]string{}, nil)
	return nil
}

// UpgradeRouterBoardFirmware triggers bootloader firmware flash via POST /system/routerboard/upgrade
func (c *Client) UpgradeRouterBoardFirmware(ctx context.Context) error {
	return c.Post(ctx, "/system/routerboard/upgrade", map[string]string{}, nil)
}

