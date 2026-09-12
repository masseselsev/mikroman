package services

import (
	"testing"
)

func TestBrandFromDomain(t *testing.T) {
	tests := []struct {
		domain   string
		expected string
	}{
		{"ucell.uz", "Ucell"},
		{"t-mobile.com", "T-Mobile"},
		{"bt.co.uk", "Bt"},
		{"acme.net", "Acme"},
		{"sub.domain.co.jp", "Domain"},
		{"", ""},
		{"invalid", ""},
	}

	for _, tt := range tests {
		got := BrandFromDomain(tt.domain)
		if got != tt.expected {
			t.Errorf("BrandFromDomain(%q) = %q; want %q", tt.domain, got, tt.expected)
		}
	}
}

func TestCleanTradingName(t *testing.T) {
	tests := []struct {
		raw      string
		expected string
	}{
		{"Ucell Net 5", "Ucell"},
		{"Acme Network 10", "Acme"},
		{"Carrier AS1234", "Carrier"},
		{"Acme Holdings LLC", ""}, // rejected as legal entity
		{"Plain ISP", "Plain ISP"},
		{"", ""},
	}

	for _, tt := range tests {
		got := CleanTradingName(tt.raw)
		if got != tt.expected {
			t.Errorf("CleanTradingName(%q) = %q; want %q", tt.raw, got, tt.expected)
		}
	}
}

func TestPublicNetworkService_PrivateIP(t *testing.T) {
	svc := NewPublicNetworkService()
	// RFC 1918 and RFC 5737 addresses must not trigger lookups
	if isp := svc.GetISP(1, "192.168.1.1"); isp != "" {
		t.Errorf("expected empty ISP for private IP, got %q", isp)
	}
	if isp := svc.GetISP(1, "10.0.0.1"); isp != "" {
		t.Errorf("expected empty ISP for private IP, got %q", isp)
	}
	if isp := svc.GetISP(1, "192.0.2.1"); isp != "" {
		t.Errorf("expected empty ISP for RFC5737 IP, got %q", isp)
	}
}

