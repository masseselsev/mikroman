package services

import (
	"testing"
)

func TestLookupGeoIP(t *testing.T) {
	// Private / Local ranges must always be detected as Local Network
	testsLocal := []string{
		"127.0.0.1",
		"192.168.1.1",
		"10.0.0.1",
		"172.16.0.1",
		"192.0.2.1", // RFC 5737
		"198.51.100.1", // RFC 5737
		"203.0.113.1", // RFC 5737
		"",
	}
	for _, ip := range testsLocal {
		res := LookupGeoIP(ip)
		if !res.IsLocal {
			t.Errorf("expected %s to be local, got %+v", ip, res)
		}
		if res.CountryCode != "LOCAL" {
			t.Errorf("expected countryCode LOCAL for %s, got %s", ip, res.CountryCode)
		}
		if res.FlagEmoji != "🏠" {
			t.Errorf("expected flag 🏠 for %s, got %s", ip, res.FlagEmoji)
		}
	}

	// Known Public DNS / Cloud IP checks
	resGoogle := LookupGeoIP("8.8.8.8")
	if resGoogle.IsLocal {
		t.Errorf("expected 8.8.8.8 to be public")
	}
	if resGoogle.CountryCode != "US" {
		t.Errorf("expected US for 8.8.8.8, got %s", resGoogle.CountryCode)
	}
	if resGoogle.FlagEmoji != "🇺🇸" {
		t.Errorf("expected 🇺🇸 for 8.8.8.8, got %s", resGoogle.FlagEmoji)
	}

	// Telegram IP
	resTelegram := LookupGeoIP("91.108.4.1")
	if resTelegram.IsLocal {
		t.Errorf("expected 91.108.4.1 to be public")
	}
	if resTelegram.CountryCode != "NL" {
		t.Errorf("expected NL for Telegram, got %s", resTelegram.CountryCode)
	}
	if resTelegram.FlagEmoji != "🇳🇱" {
		t.Errorf("expected 🇳🇱 for Telegram, got %s", resTelegram.FlagEmoji)
	}

	// Hetzner Germany IP
	resDE := LookupGeoIP("78.46.10.1")
	if resDE.CountryCode != "DE" {
		t.Errorf("expected DE for 78.46.10.1, got %s", resDE.CountryCode)
	}
	if resDE.FlagEmoji != "🇩🇪" {
		t.Errorf("expected 🇩🇪 for 78.46.10.1, got %s", resDE.FlagEmoji)
	}
}

