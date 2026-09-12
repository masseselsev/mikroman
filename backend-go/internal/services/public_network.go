package services

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"regexp"
	"strings"
	"sync"
	"time"
)

var twoLabelTLDs = map[string]bool{
	"co.uk": true, "org.uk": true, "gov.uk": true, "ac.uk": true, "co.jp": true,
	"com.au": true, "net.au": true, "org.au": true, "com.br": true, "com.tr": true,
	"com.ua": true, "net.ua": true, "co.nz": true, "co.za": true, "com.cn": true,
	"com.hk": true, "com.sg": true, "com.mx": true, "co.in": true, "co.kr": true,
	"com.pl": true, "co.il": true, "com.ar": true, "com.co": true,
}

var reLegalEntity = regexp.MustCompile(`(?i)\b(?:LLC|L\.L\.C\.?|LLP|Ltd\.?|Limited|Liability|Company|Co\.?|Inc\.?|Corp\.?|GmbH|S\.?A\.?|S\.?R\.?L\.?|B\.?V\.?|PLC|JSC|OOO|PJSC|OJSC)\b`)
var reTrailingSuffix = regexp.MustCompile(`(?i)\s+(?:Net(?:work)?\s*\d*|AS\d+|\d+)$`)

type PublicNetworkInfo struct {
	IP        string
	ISP       string
	ASN       string
	UpdatedAt time.Time
}

type PublicNetworkService struct {
	mu     sync.RWMutex
	cache  map[int]*PublicNetworkInfo
	busy   map[int]bool
	client *http.Client
}

func NewPublicNetworkService() *PublicNetworkService {
	return &PublicNetworkService{
		cache: make(map[int]*PublicNetworkInfo),
		busy:  make(map[int]bool),
		client: &http.Client{
			Timeout: 4 * time.Second,
		},
	}
}

// BrandFromDomain extracts a human-readable brand name from an ISP's primary domain.
func BrandFromDomain(domain string) string {
	host := strings.ToLower(strings.Trim(domain, "."))
	if host == "" {
		return ""
	}
	parts := strings.Split(host, ".")
	if len(parts) < 2 {
		return ""
	}
	suffixLen := 1
	if len(parts) >= 3 {
		lastTwo := parts[len(parts)-2] + "." + parts[len(parts)-1]
		if twoLabelTLDs[lastTwo] {
			suffixLen = 2
		}
	}
	labelIdx := len(parts) - suffixLen - 1
	if labelIdx < 0 {
		return ""
	}
	label := parts[labelIdx]
	if len(label) < 2 {
		return ""
	}
	// Capitalize words separated by hyphens (e.g. "t-mobile" -> "T-Mobile", "ucell" -> "Ucell")
	subParts := strings.Split(label, "-")
	for i, sp := range subParts {
		if len(sp) > 0 {
			subParts[i] = strings.ToUpper(sp[:1]) + sp[1:]
		}
	}
	return strings.Join(subParts, "-")
}

// CleanTradingName removes noise, entity tags, and registry numbering suffixes.
func CleanTradingName(raw string) string {
	text := strings.TrimSpace(raw)
	if text == "" {
		return ""
	}
	text = strings.TrimSpace(reTrailingSuffix.ReplaceAllString(text, ""))
	if text == "" || reLegalEntity.MatchString(text) {
		return ""
	}
	if len(text) > 40 {
		text = text[:40]
	}
	return text
}

// GetISP retrieves the cached ISP name for a router or initiates a background lookup.
func (s *PublicNetworkService) GetISP(routerID int, publicIP string) string {
	ipStr := strings.TrimSpace(publicIP)
	if ipStr == "" {
		return ""
	}
	parsed := net.ParseIP(ipStr)
	if parsed == nil || isPrivateOrLocal(parsed) {
		return ""
	}

	s.mu.RLock()
	info, exists := s.cache[routerID]
	isBusy := s.busy[routerID]
	s.mu.RUnlock()

	if exists && info != nil && info.IP == ipStr {
		if time.Since(info.UpdatedAt) < 30*time.Minute {
			return info.ISP
		}
	}

	if isBusy {
		if exists && info != nil {
			return info.ISP
		}
		return ""
	}

	s.mu.Lock()
	s.busy[routerID] = true
	s.mu.Unlock()

	go func() {
		defer func() {
			s.mu.Lock()
			delete(s.busy, routerID)
			s.mu.Unlock()
		}()

		ispName, asnName := s.lookupIP(ipStr)
		s.mu.Lock()
		s.cache[routerID] = &PublicNetworkInfo{
			IP:        ipStr,
			ISP:       ispName,
			ASN:       asnName,
			UpdatedAt: time.Now(),
		}
		s.mu.Unlock()
	}()

	if exists && info != nil {
		return info.ISP
	}
	return ""
}

type ipWhoIsResp struct {
	Success    bool `json:"success"`
	Connection struct {
		ASN    interface{} `json:"asn"`
		Org    string      `json:"org"`
		ISP    string      `json:"isp"`
		Domain string      `json:"domain"`
	} `json:"connection"`
}

type ipApiResp struct {
	Status string `json:"status"`
	ISP    string `json:"isp"`
	Org    string `json:"org"`
	AS     string `json:"as"`
}

func (s *PublicNetworkService) lookupIP(ipStr string) (string, string) {
	// 1. Primary: ipwho.is (HTTPS)
	url := fmt.Sprintf("https://ipwho.is/%s", ipStr)
	resp, err := s.client.Get(url)
	if err == nil && resp != nil {
		defer resp.Body.Close()
		if resp.StatusCode == http.StatusOK {
			var body ipWhoIsResp
			if jsonErr := json.NewDecoder(resp.Body).Decode(&body); jsonErr == nil && body.Success {
				asn := ""
				if body.Connection.ASN != nil {
					asn = fmt.Sprintf("%v", body.Connection.ASN)
				}
				brand := BrandFromDomain(body.Connection.Domain)
				if brand == "" {
					brand = CleanTradingName(body.Connection.Org)
				}
				if brand == "" {
					brand = CleanTradingName(body.Connection.ISP)
				}
				if brand == "" && body.Connection.ISP != "" {
					brand = body.Connection.ISP
				}
				if brand != "" || asn != "" {
					return brand, asn
				}
			}
		}
	}

	// 2. Fallback: ip-api.com
	fallbackURL := fmt.Sprintf("http://ip-api.com/json/%s?fields=status,isp,org,as", ipStr)
	fResp, fErr := s.client.Get(fallbackURL)
	if fErr == nil && fResp != nil {
		defer fResp.Body.Close()
		if fResp.StatusCode == http.StatusOK {
			var body ipApiResp
			if jsonErr := json.NewDecoder(fResp.Body).Decode(&body); jsonErr == nil && body.Status == "success" {
				brand := CleanTradingName(body.Org)
				if brand == "" {
					brand = CleanTradingName(body.ISP)
				}
				if brand == "" {
					brand = body.ISP
				}
				return brand, body.AS
			}
		}
	}

	return "", ""
}
