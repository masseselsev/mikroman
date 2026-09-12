package services

import (
	"context"
	"net"
	"os"
	"strings"

	"github.com/masseselsev/mikroman/internal/routeros"
)

var procVersionPath = "/proc/version"

// IsRunningInRouterOSContainer checks whether this MikroMan instance is running
// as a container directly on the specified RouterOS device.
func IsRunningInRouterOSContainer(ctx context.Context, client *routeros.Client) bool {
	// 1. Explicit environment variable override / declaration
	val := os.Getenv("MIKROMAN_ON_ROUTER")
	if strings.EqualFold(val, "true") || val == "1" {
		return true
	}

	// 2. Host kernel signature in /proc/version
	if isRouterOSKernel() {
		return true
	}

	// 3. Network topology match: our outbound socket IP matches a RouterOS VETH interface
	if client != nil && isMatchingRouterVeth(ctx, client) {
		return true
	}

	return false
}

func isRouterOSKernel() bool {
	data, err := os.ReadFile(procVersionPath)
	if err != nil {
		return false
	}
	content := strings.ToLower(string(data))
	return strings.Contains(content, "mikrotik") || strings.Contains(content, "routeros")
}

func isMatchingRouterVeth(ctx context.Context, client *routeros.Client) bool {
	immuneIPs := client.GetImmuneIPs()
	if len(immuneIPs) == 0 {
		return false
	}

	veths, err := client.GetVethInterfaces(ctx)
	if err != nil || len(veths) == 0 {
		return false
	}

	for _, v := range veths {
		addr := strings.TrimSpace(v.Address)
		if addr == "" {
			continue
		}
		ipStr := addr
		if ip, _, err := net.ParseCIDR(addr); err == nil {
			ipStr = ip.String()
		}

		if immuneIPs[ipStr] {
			return true
		}
	}

	return false
}

