package services

import (
	"crypto/sha256"
	"encoding/hex"
	"regexp"
	"strings"
)

var volatileHeaderRegex = regexp.MustCompile(`(?m)^#\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+by\s+RouterOS\b.*$`)

// NormalizeRSC strips volatile timestamp headers and normalizes line endings and whitespace.
func NormalizeRSC(rscText string) string {
	if rscText == "" {
		return ""
	}

	// Standardize line endings
	text := strings.ReplaceAll(rscText, "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")

	// Strip volatile RouterOS timestamp header
	text = volatileHeaderRegex.ReplaceAllString(text, "")

	// Strip trailing whitespace per line
	lines := strings.Split(text, "\n")
	for i, l := range lines {
		lines[i] = strings.TrimRight(l, " \t")
	}

	// Remove leading empty lines
	for len(lines) > 0 && lines[0] == "" {
		lines = lines[1:]
	}

	// Remove trailing empty lines
	for len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}

	return strings.Join(lines, "\n")
}

// ComputeFingerprint returns the SHA-256 hex digest of the normalized RouterOS configuration.
func ComputeFingerprint(rscText string) string {
	normalized := NormalizeRSC(rscText)
	hash := sha256.Sum256([]byte(normalized))
	return hex.EncodeToString(hash[:])
}

