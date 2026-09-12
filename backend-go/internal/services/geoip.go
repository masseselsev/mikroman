package services

import (
	"net"
	"strings"
)

type GeoLocation struct {
	CountryCode string  `json:"country_code"`
	CountryName string  `json:"country_name"`
	FlagEmoji   string  `json:"flag_emoji"`
	Lat         float64 `json:"lat"`
	Lng         float64 `json:"lng"`
	IsLocal     bool    `json:"is_local"`
}

type geoRange struct {
	network     *net.IPNet
	countryCode string
	countryName string
	flagEmoji   string
	lat         float64
	lng         float64
}

var geoTable []geoRange

func init() {
	// Country Centers
	// US: 37.0902, -95.7129
	// DE: 51.1657, 10.4515
	// NL: 52.1326, 5.2913
	// GB: 55.3781, -3.4360
	// FR: 46.2276, 2.2137
	// RU: 61.5240, 105.3188
	// JP: 36.2048, 138.2529
	// SG: 1.3521, 103.8198
	// FI: 61.9241, 25.7482
	// SE: 60.1282, 18.6435
	// PL: 51.9194, 19.1451
	// UZ: 41.3775, 64.5853
	// KZ: 48.0196, 66.9237
	// UA: 48.3794, 31.1656
	// BR: -14.2350, -51.9253
	// IN: 20.5937, 78.9629
	// AU: -25.2744, 133.7751

	addCIDR := func(cidr, code, name, flag string, lat, lng float64) {
		_, ipnet, err := net.ParseCIDR(cidr)
		if err == nil {
			geoTable = append(geoTable, geoRange{
				network:     ipnet,
				countryCode: code,
				countryName: name,
				flagEmoji:   flag,
				lat:         lat,
				lng:         lng,
			})
		}
	}

	// Major Global Cloud / DNS
	addCIDR("1.0.0.0/24", "US", "Cloudflare (US)", "🇺🇸", 37.0902, -95.7129)
	addCIDR("1.1.1.0/24", "US", "Cloudflare (US)", "🇺🇸", 37.0902, -95.7129)
	addCIDR("8.8.8.0/24", "US", "Google DNS (US)", "🇺🇸", 37.0902, -95.7129)
	addCIDR("8.8.4.0/24", "US", "Google DNS (US)", "🇺🇸", 37.0902, -95.7129)
	addCIDR("9.9.9.0/24", "US", "Quad9 (US)", "🇺🇸", 37.0902, -95.7129)

	// Telegram
	addCIDR("91.108.4.0/22", "NL", "Telegram (NL)", "🇳🇱", 52.1326, 5.2913)
	addCIDR("91.108.8.0/22", "SG", "Telegram (SG)", "🇸🇬", 1.3521, 103.8198)
	addCIDR("91.108.12.0/22", "NL", "Telegram (NL)", "🇳🇱", 52.1326, 5.2913)
	addCIDR("91.108.16.0/22", "NL", "Telegram (NL)", "🇳🇱", 52.1326, 5.2913)
	addCIDR("91.108.56.0/22", "NL", "Telegram (NL)", "🇳🇱", 52.1326, 5.2913)
	addCIDR("149.154.160.0/20", "NL", "Telegram (NL)", "🇳🇱", 52.1326, 5.2913)
	addCIDR("149.154.164.0/22", "NL", "Telegram (NL)", "🇳🇱", 52.1326, 5.2913)
	addCIDR("149.154.168.0/22", "NL", "Telegram (NL)", "🇳🇱", 52.1326, 5.2913)
	addCIDR("149.154.172.0/22", "NL", "Telegram (NL)", "🇳🇱", 52.1326, 5.2913)

	// Yandex / VK
	addCIDR("77.88.0.0/18", "RU", "Yandex (RU)", "🇷🇺", 55.7558, 37.6173)
	addCIDR("87.250.250.0/24", "RU", "Yandex (RU)", "🇷🇺", 55.7558, 37.6173)
	addCIDR("87.240.128.0/18", "RU", "VKontakte (RU)", "🇷🇺", 59.9343, 30.3351)
	addCIDR("93.186.224.0/20", "RU", "VKontakte (RU)", "🇷🇺", 59.9343, 30.3351)

	// Broad Regional Allocations
	// North America (US/CA)
	addCIDR("3.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("4.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("12.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("13.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("15.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("16.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("17.0.0.0/8", "US", "Apple (US)", "🇺🇸", 37.3349, -122.0090)
	addCIDR("20.0.0.0/8", "US", "Microsoft (US)", "🇺🇸", 47.6740, -122.1215)
	addCIDR("23.0.0.0/8", "US", "Akamai (US)", "🇺🇸", 42.3601, -71.0942)
	addCIDR("24.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("34.0.0.0/8", "US", "Google Cloud (US)", "🇺🇸", 37.4220, -122.0841)
	addCIDR("35.0.0.0/8", "US", "Google Cloud (US)", "🇺🇸", 37.4220, -122.0841)
	addCIDR("40.0.0.0/8", "US", "Microsoft (US)", "🇺🇸", 47.6740, -122.1215)
	addCIDR("44.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("52.0.0.0/8", "US", "AWS (US)", "🇺🇸", 37.0902, -95.7129)
	addCIDR("54.0.0.0/8", "US", "AWS (US)", "🇺🇸", 37.0902, -95.7129)
	addCIDR("63.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("64.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("65.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("66.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("67.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("68.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("69.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("70.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("71.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("72.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("73.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("74.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("75.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("76.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("96.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("97.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("98.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("99.0.0.0/8", "US", "United States", "🇺🇸", 37.0902, -95.7129)
	addCIDR("104.16.0.0/12", "US", "Cloudflare (US)", "🇺🇸", 37.0902, -95.7129)
	addCIDR("104.24.0.0/14", "US", "Cloudflare (US)", "🇺🇸", 37.0902, -95.7129)
	addCIDR("142.250.0.0/15", "US", "Google (US)", "🇺🇸", 37.0902, -95.7129)
	addCIDR("172.217.0.0/16", "US", "Google (US)", "🇺🇸", 37.0902, -95.7129)
	addCIDR("173.194.0.0/16", "US", "Google (US)", "🇺🇸", 37.0902, -95.7129)

	// Europe
	// Germany (Hetzner, DE-CIX, Telekom)
	addCIDR("78.46.0.0/15", "DE", "Germany", "🇩🇪", 51.1657, 10.4515)
	addCIDR("88.198.0.0/16", "DE", "Germany", "🇩🇪", 51.1657, 10.4515)
	addCIDR("116.202.0.0/15", "DE", "Germany", "🇩🇪", 51.1657, 10.4515)
	addCIDR("136.243.0.0/16", "DE", "Germany", "🇩🇪", 51.1657, 10.4515)
	addCIDR("138.201.0.0/16", "DE", "Germany", "🇩🇪", 51.1657, 10.4515)
	addCIDR("144.76.0.0/16", "DE", "Germany", "🇩🇪", 51.1657, 10.4515)
	addCIDR("148.251.0.0/16", "DE", "Germany", "🇩🇪", 51.1657, 10.4515)
	addCIDR("159.69.0.0/16", "DE", "Germany", "🇩🇪", 51.1657, 10.4515)
	addCIDR("168.119.0.0/16", "DE", "Germany", "🇩🇪", 51.1657, 10.4515)
	addCIDR("178.63.0.0/16", "DE", "Germany", "🇩🇪", 51.1657, 10.4515)
	addCIDR("195.201.0.0/16", "DE", "Germany", "🇩🇪", 51.1657, 10.4515)

	// Netherlands
	addCIDR("5.188.0.0/16", "NL", "Netherlands", "🇳🇱", 52.1326, 5.2913)
	addCIDR("31.220.0.0/16", "NL", "Netherlands", "🇳🇱", 52.1326, 5.2913)
	addCIDR("94.142.0.0/16", "NL", "Netherlands", "🇳🇱", 52.1326, 5.2913)
	addCIDR("185.107.0.0/16", "NL", "Netherlands", "🇳🇱", 52.1326, 5.2913)

	// France (OVH)
	addCIDR("51.0.0.0/8", "FR", "France", "🇫🇷", 46.2276, 2.2137)
	addCIDR("54.36.0.0/14", "FR", "France", "🇫🇷", 46.2276, 2.2137)
	addCIDR("137.74.0.0/16", "FR", "France", "🇫🇷", 46.2276, 2.2137)
	addCIDR("147.135.0.0/16", "FR", "France", "🇫🇷", 46.2276, 2.2137)
	addCIDR("151.80.0.0/16", "FR", "France", "🇫🇷", 46.2276, 2.2137)

	// United Kingdom
	addCIDR("25.0.0.0/8", "GB", "United Kingdom", "🇬🇧", 55.3781, -3.4360)
	addCIDR("51.140.0.0/14", "GB", "United Kingdom", "🇬🇧", 55.3781, -3.4360)
	addCIDR("81.187.0.0/16", "GB", "United Kingdom", "🇬🇧", 55.3781, -3.4360)

	// Finland
	addCIDR("65.21.0.0/16", "FI", "Finland", "🇫🇮", 61.9241, 25.7482)
	addCIDR("95.216.0.0/15", "FI", "Finland", "🇫🇮", 61.9241, 25.7482)

	// Russia
	addCIDR("77.0.0.0/9", "RU", "Russia", "🇷🇺", 55.7558, 37.6173)
	addCIDR("79.98.0.0/15", "RU", "Russia", "🇷🇺", 55.7558, 37.6173)
	addCIDR("85.112.0.0/12", "RU", "Russia", "🇷🇺", 55.7558, 37.6173)
	addCIDR("93.158.128.0/17", "RU", "Yandex (RU)", "🇷🇺", 55.7558, 37.6173)
	addCIDR("93.180.0.0/16", "RU", "Russia", "🇷🇺", 55.7558, 37.6173)
	addCIDR("93.188.0.0/16", "RU", "Russia", "🇷🇺", 55.7558, 37.6173)
	addCIDR("94.25.0.0/16", "RU", "Russia", "🇷🇺", 55.7558, 37.6173)
	addCIDR("95.173.136.0/21", "RU", "Russia", "🇷🇺", 55.7558, 37.6173)
	addCIDR("176.14.0.0/15", "RU", "Russia", "🇷🇺", 55.7558, 37.6173)
	addCIDR("178.64.0.0/10", "RU", "Russia", "🇷🇺", 55.7558, 37.6173)
	addCIDR("188.162.0.0/15", "RU", "Russia", "🇷🇺", 55.7558, 37.6173)
	addCIDR("212.45.0.0/16", "RU", "Russia", "🇷🇺", 55.7558, 37.6173)
	addCIDR("213.87.0.0/16", "RU", "Russia", "🇷🇺", 55.7558, 37.6173)

	// Central Asia
	// Uzbekistan (UZ)
	addCIDR("84.54.64.0/18", "UZ", "Uzbekistan", "🇺🇿", 41.3775, 64.5853)
	addCIDR("89.236.192.0/18", "UZ", "Uzbekistan", "🇺🇿", 41.3775, 64.5853)
	addCIDR("91.212.88.0/22", "UZ", "Uzbekistan", "🇺🇿", 41.3775, 64.5853)
	addCIDR("195.158.0.0/19", "UZ", "Uzbekistan", "🇺🇿", 41.3775, 64.5853)
	addCIDR("213.230.64.0/18", "UZ", "Uzbekistan", "🇺🇿", 41.3775, 64.5853)
	addCIDR("217.29.112.0/20", "UZ", "Uzbekistan", "🇺🇿", 41.3775, 64.5853)

	// Kazakhstan (KZ)
	addCIDR("88.204.128.0/17", "KZ", "Kazakhstan", "🇰🇿", 48.0196, 66.9237)
	addCIDR("92.46.0.0/15", "KZ", "Kazakhstan", "🇰🇿", 48.0196, 66.9237)
	addCIDR("95.56.0.0/14", "KZ", "Kazakhstan", "🇰🇿", 48.0196, 66.9237)
	addCIDR("178.88.0.0/13", "KZ", "Kazakhstan", "🇰🇿", 48.0196, 66.9237)

	// Asia-Pacific
	// Singapore (SG)
	addCIDR("103.0.0.0/10", "SG", "Singapore", "🇸🇬", 1.3521, 103.8198)
	addCIDR("118.189.0.0/16", "SG", "Singapore", "🇸🇬", 1.3521, 103.8198)

	// Japan (JP)
	addCIDR("114.0.0.0/8", "JP", "Japan", "🇯🇵", 36.2048, 138.2529)
	addCIDR("133.0.0.0/8", "JP", "Japan", "🇯🇵", 36.2048, 138.2529)
	addCIDR("150.0.0.0/8", "JP", "Japan", "🇯🇵", 36.2048, 138.2529)
	addCIDR("163.0.0.0/8", "JP", "Japan", "🇯🇵", 36.2048, 138.2529)

	// Australia (AU)
	addCIDR("1.120.0.0/13", "AU", "Australia", "🇦🇺", -25.2744, 133.7751)
	addCIDR("139.130.0.0/16", "AU", "Australia", "🇦🇺", -25.2744, 133.7751)
}

// isPrivateOrLocal checks if an IP belongs to loopback, RFC 1918, RFC 5737, etc.
func isPrivateOrLocal(ip net.IP) bool {
	if ip == nil {
		return true
	}
	if ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || ip.IsPrivate() {
		return true
	}
	// RFC 5737 Test-Net ranges
	if ip.IsUnspecified() {
		return true
	}
	v4 := ip.To4()
	if v4 == nil {
		return false
	}
	// 192.0.2.0/24 (TEST-NET-1)
	if v4[0] == 192 && v4[1] == 0 && v4[2] == 2 {
		return true
	}
	// 198.51.100.0/24 (TEST-NET-2)
	if v4[0] == 198 && v4[1] == 51 && v4[2] == 100 {
		return true
	}
	// 203.0.113.0/24 (TEST-NET-3)
	if v4[0] == 203 && v4[1] == 0 && v4[2] == 113 {
		return true
	}
	return false
}

// LookupGeoIP resolves an IP address to a GeoLocation.
func LookupGeoIP(ipStr string) GeoLocation {
	ipStr = strings.TrimSpace(ipStr)
	if strings.HasPrefix(ipStr, "[") && strings.Contains(ipStr, "]") {
		end := strings.Index(ipStr, "]")
		if end != -1 {
			ipStr = ipStr[1:end]
		}
	}
	if strings.Count(ipStr, ":") == 1 {
		parts := strings.Split(ipStr, ":")
		ipStr = parts[0]
	}

	ip := net.ParseIP(ipStr)
	if ip == nil || isPrivateOrLocal(ip) {
		return GeoLocation{
			CountryCode: "LOCAL",
			CountryName: "Local Network",
			FlagEmoji:   "🏠",
			Lat:         0,
			Lng:         0,
			IsLocal:     true,
		}
	}

	for _, g := range geoTable {
		if g.network.Contains(ip) {
			return GeoLocation{
				CountryCode: g.countryCode,
				CountryName: g.countryName,
				FlagEmoji:   g.flagEmoji,
				Lat:         g.lat,
				Lng:         g.lng,
				IsLocal:     false,
			}
		}
	}

	return GeoLocation{
		CountryCode: "UN",
		CountryName: "Global Internet",
		FlagEmoji:   "🌐",
		Lat:         20.0,
		Lng:         0.0,
		IsLocal:     false,
	}
}

