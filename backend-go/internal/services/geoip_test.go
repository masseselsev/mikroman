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
		"255.255.255.255", // Broadcast
		"100.64.0.1", // RFC 6598 CGNAT
		"100.89.60.223", // RFC 6598 Tailscale / CGNAT
		"100.127.255.254", // RFC 6598 CGNAT
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

	// Unknown public IP fallback (Antarctica coordinates)
	resUnknown := LookupGeoIP("192.88.99.1") // 6to4 relay anycast or unlisted IP
	if resUnknown.CountryCode == "UN" {
		if resUnknown.Lat != -78.0 || resUnknown.Lng != 0.0 {
			t.Errorf("expected UN to be pinned to Antarctica (-78.0, 0.0), got (%f, %f)", resUnknown.Lat, resUnknown.Lng)
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

func TestISOFlagEmoji(t *testing.T) {
	cases := map[string]string{
		"US": "🇺🇸",
		"NL": "🇳🇱",
		"RU": "🇷🇺",
		"DE": "🇩🇪",
		"UZ": "🇺🇿",
		"KZ": "🇰🇿",
		"FR": "🇫🇷",
		"JP": "🇯🇵",
		"BR": "🇧🇷",
		"12": "🌐",
		"":   "🌐",
		"USA": "🌐",
	}

	for code, expected := range cases {
		actual := ISOFlagEmoji(code)
		if actual != expected {
			t.Errorf("expected %s for %s, got %s", expected, code, actual)
		}
	}
}

func TestGeoIPUpdater_Status(t *testing.T) {
	tmpDir := t.TempDir()
	updater := InitGeoIPUpdater(tmpDir)
	defer updater.Close()

	status := updater.GetStatus()
	if status.Path == "" {
		t.Errorf("expected non-empty path in status")
	}
}

