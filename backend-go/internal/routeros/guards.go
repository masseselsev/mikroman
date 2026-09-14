package routeros

import (
	"fmt"
	"net"
	"regexp"
	"strconv"
	"strings"
)

var (
	ratePattern       = regexp.MustCompile(`^(\d+)([kKMGT]?)/(\d+)([kKMGT]?)$`)
	rateSinglePattern = regexp.MustCompile(`^(\d+)([kKMGT]?)$`)

	immuneWildcards = map[string]bool{
		"0.0.0.0":         true,
		"0.0.0.0/0":       true,
		"::/0":            true,
		"255.255.255.255": true,
	}

	immuneLoopbacks = map[string]bool{
		"127.0.0.1": true,
		"::1":       true,
		"localhost": true,
	}
)

type WriteGuardViolation struct {
	Guard  string
	Target string
	Reason string
}

func (e *WriteGuardViolation) Error() string {
	return fmt.Sprintf("[WriteGuard] [%s] Refused write for %s: %s", e.Guard, e.Target, e.Reason)
}

// ParseBps parses bandwidth rate like "5M", "100k", or "0" into bits per second.
func ParseBps(val string) (int64, error) {
	raw := strings.TrimSpace(val)
	if raw == "" || raw == "0" {
		return 0, nil
	}
	if n, err := strconv.ParseInt(raw, 10, 64); err == nil {
		return n, nil
	}
	m := rateSinglePattern.FindStringSubmatch(raw)
	if m == nil {
		return 0, fmt.Errorf("invalid rate string: %s", val)
	}
	num, _ := strconv.ParseInt(m[1], 10, 64)
	mult := unitMultiplier(m[2])
	return num * mult, nil
}

// ParseRatePair parses "5M/10M" or "0/0" into (upload_bps, download_bps).
func ParseRatePair(pair string) (int64, int64, error) {
	clean := strings.TrimSpace(pair)
	if clean == "" || clean == "0" || clean == "0/0" || clean == "unlimited" || clean == "none" {
		return 0, 0, nil
	}
	m := ratePattern.FindStringSubmatch(clean)
	if m == nil {
		return 0, 0, fmt.Errorf("invalid rate pair format: %s", pair)
	}
	upNum, _ := strconv.ParseInt(m[1], 10, 64)
	upMult := unitMultiplier(m[2])
	downNum, _ := strconv.ParseInt(m[3], 10, 64)
	downMult := unitMultiplier(m[4])
	return upNum * upMult, downNum * downMult, nil
}

func unitMultiplier(unit string) int64 {
	switch unit {
	case "k", "K":
		return 1_000
	case "m", "M":
		return 1_000_000
	case "g", "G":
		return 1_000_000_000
	case "t", "T":
		return 1_000_000_000_000
	default:
		return 1
	}
}

// GuardImmuneTarget ensures management endpoints, wildcards, loopbacks, and supernets containing them are never blocked or throttled.
func GuardImmuneTarget(target string, immunes map[string]bool, action string) error {
	clean := strings.TrimSpace(target)
	if clean == "" {
		return nil
	}

	// 1. Loopbacks
	if immuneLoopbacks[clean] {
		return &WriteGuardViolation{Guard: "immune_targets", Target: target, Reason: "loopback targets are immune"}
	}
	if strings.HasPrefix(clean, "127.") {
		return &WriteGuardViolation{Guard: "immune_targets", Target: target, Reason: "loopback targets are immune"}
	}

	// 2. Wildcards
	if immuneWildcards[clean] {
		return &WriteGuardViolation{Guard: "immune_targets", Target: target, Reason: "wildcard targets are immune"}
	}

	// 3. Direct immune matches
	ipOnly := cleanIP(clean)
	if immunes[clean] || immunes[ipOnly] {
		return &WriteGuardViolation{Guard: "immune_targets", Target: target, Reason: "protected router management target"}
	}

	// 4. Supernet containment: if target is a CIDR network that contains an immune IP
	if strings.Contains(clean, "/") {
		_, ipNet, err := net.ParseCIDR(clean)
		if err == nil && ipNet != nil {
			for imm := range immunes {
				parsedImm := net.ParseIP(imm)
				if parsedImm != nil && ipNet.Contains(parsedImm) {
					return &WriteGuardViolation{
						Guard:  "immune_targets",
						Target: target,
						Reason: fmt.Sprintf("supernet contains immune address %s", imm),
					}
				}
			}
		}
	}

	return nil
}

// GuardForeignResource ensures we never mutate or delete rules/queues not created by MikroMan.
func GuardForeignResource(comment string, action, resourceType string) error {
	if !strings.HasPrefix(comment, "mikroman:") {
		return &WriteGuardViolation{
			Guard:  "foreign_resources",
			Target: comment,
			Reason: fmt.Sprintf("foreign %s without 'mikroman:' prefix is protected", resourceType),
		}
	}
	return nil
}

// GuardQueueInvariants validates queue rate formatting, hierarchy, and limits.
func GuardQueueInvariants(target, maxLimit, limitAt, parent, name string) error {
	if parent != "" && parent != "none" && parent == name {
		return &WriteGuardViolation{
			Guard:  "queue_invariants",
			Target: target,
			Reason: fmt.Sprintf("circular parent reference: %s cannot be parent of itself", name),
		}
	}

	upMax, downMax, err := ParseRatePair(maxLimit)
	if err != nil {
		return &WriteGuardViolation{
			Guard:  "queue_invariants",
			Target: target,
			Reason: fmt.Sprintf("invalid max_limit format: %s", maxLimit),
		}
	}

	if limitAt != "" && limitAt != "0" && limitAt != "0/0" && limitAt != "none" {
		upAt, downAt, err := ParseRatePair(limitAt)
		if err != nil {
			return &WriteGuardViolation{
				Guard:  "queue_invariants",
				Target: target,
				Reason: fmt.Sprintf("invalid limit_at format: %s", limitAt),
			}
		}

		if upMax > 0 && upAt > upMax {
			return &WriteGuardViolation{
				Guard:  "queue_invariants",
				Target: target,
				Reason: fmt.Sprintf("upload limit_at (%s) cannot exceed max_limit (%s)", limitAt, maxLimit),
			}
		}
		if downMax > 0 && downAt > downMax {
			return &WriteGuardViolation{
				Guard:  "queue_invariants",
				Target: target,
				Reason: fmt.Sprintf("download limit_at (%s) cannot exceed max_limit (%s)", limitAt, maxLimit),
			}
		}
	}

	return nil
}
