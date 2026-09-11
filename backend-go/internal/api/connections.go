package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type ConnectionsHandler struct {
	database *db.DB
	client   *routeros.Client
	mu       sync.Mutex
	clients  map[int]*routeros.Client
}

func NewConnectionsHandler(database *db.DB, client *routeros.Client) *ConnectionsHandler {
	clients := make(map[int]*routeros.Client)
	if client != nil {
		if def, err := database.GetDefaultRouter(); err == nil && def != nil {
			clients[def.ID] = client
		}
	}
	return &ConnectionsHandler{
		database: database,
		client:   client,
		clients:  clients,
	}
}

func (h *ConnectionsHandler) getClient(routerID *int) (*routeros.Client, error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	var targetID int
	if routerID != nil && *routerID > 0 {
		targetID = *routerID
	} else {
		def, err := h.database.GetDefaultRouter()
		if err != nil || def == nil {
			if h.client != nil {
				return h.client, nil
			}
			return nil, fmt.Errorf("no default router configured")
		}
		targetID = def.ID
	}

	if c, ok := h.clients[targetID]; ok && c != nil {
		return c, nil
	}

	defaultRouter, _ := h.database.GetDefaultRouter()
	if (defaultRouter == nil || defaultRouter.ID == targetID) && h.client != nil {
		h.clients[targetID] = h.client
		return h.client, nil
	}

	router, err := h.database.GetRouter(targetID)
	if err != nil || router == nil {
		if h.client != nil {
			return h.client, nil
		}
		return nil, fmt.Errorf("router %d not found", targetID)
	}

	newClient, err := routeros.NewClient(routeros.Config{
		Host:      router.Host,
		Port:      router.Port,
		Username:  router.Username,
		Password:  router.Password,
		UseSSL:    router.UseSSL,
		SSLVerify: router.SSLVerify,
		CACert:    router.CACert.String,
		Timeout:   5 * time.Second,
	})
	if err != nil {
		return nil, err
	}

	h.clients[targetID] = newClient
	return newClient, nil
}

type LiveConnectionItem struct {
	ID          string  `json:"id"`
	Protocol    string  `json:"protocol"`
	SrcIP       string  `json:"src_ip"`
	SrcPort     *int    `json:"src_port"`
	DstIP       string  `json:"dst_ip"`
	DstPort     *int    `json:"dst_port"`
	DeviceID    *int    `json:"device_id"`
	DeviceName  *string `json:"device_name"`
	UserID      *int    `json:"user_id"`
	UserName    *string `json:"user_name"`
	Domain      *string `json:"domain"`
	CountryCode *string `json:"country_code"`
	CountryName *string `json:"country_name"`
	FlagEmoji   *string `json:"flag_emoji"`
	TCPState    *string `json:"tcp_state"`
	OrigRate    int64   `json:"orig_rate"`
	ReplRate    int64   `json:"repl_rate"`
	OrigBytes   int64   `json:"orig_bytes"`
	ReplBytes   int64   `json:"repl_bytes"`
	TotalBytes  int64   `json:"total_bytes"`
	Timeout     *string `json:"timeout"`
	IsImmune    bool    `json:"is_immune"`
}

type PaginatedLiveConnections struct {
	Total int                  `json:"total"`
	Items []LiveConnectionItem `json:"items"`
}

type KillConnectionRequest struct {
	RouterID *int   `json:"router_id,omitempty"`
	SrcIP    string `json:"src_ip,omitempty"`
	DstIP    string `json:"dst_ip,omitempty"`
}

func splitEndpoint(addr string) (string, *int) {
	if addr == "" {
		return "", nil
	}
	raw := strings.TrimSpace(addr)
	if strings.HasPrefix(raw, "[") {
		end := strings.Index(raw, "]")
		if end != -1 {
			host := raw[1:end]
			rest := raw[end+1:]
			if strings.HasPrefix(rest, ":") {
				if p, err := strconv.Atoi(rest[1:]); err == nil {
					return host, &p
				}
			}
			return host, nil
		}
	}
	if strings.Count(raw, ":") == 1 {
		parts := strings.Split(raw, ":")
		if p, err := strconv.Atoi(parts[1]); err == nil {
			return parts[0], &p
		}
		return parts[0], nil
	}
	return raw, nil
}

var rfc5737TestNet1 = net.IPNet{IP: net.IPv4(192, 0, 2, 0), Mask: net.CIDRMask(24, 32)}

func isPrivateOrLocalIP(ipStr string) bool {
	ip := net.ParseIP(ipStr)
	if ip == nil {
		return false
	}
	return ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || rfc5737TestNet1.Contains(ip)
}

func (h *ConnectionsHandler) GetLiveConnections(w http.ResponseWriter, r *http.Request) {
	var routerID *int
	if rID := r.URL.Query().Get("router_id"); rID != "" {
		if id, err := strconv.Atoi(rID); err == nil && id > 0 {
			routerID = &id
		}
	}

	var devIDFilter *int
	if dID := r.URL.Query().Get("device_id"); dID != "" {
		if id, err := strconv.Atoi(dID); err == nil && id > 0 {
			devIDFilter = &id
		}
	}

	var userIDFilter *int
	if uID := r.URL.Query().Get("user_id"); uID != "" {
		if id, err := strconv.Atoi(uID); err == nil && id > 0 {
			userIDFilter = &id
		}
	}

	protoFilter := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("protocol")))
	searchFilter := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("search")))

	limit := 250
	if l := r.URL.Query().Get("limit"); l != "" {
		if val, err := strconv.Atoi(l); err == nil && val > 0 {
			if val > 1000 {
				limit = 1000
			} else {
				limit = val
			}
		}
	}

	client, err := h.getClient(routerID)
	if err != nil {
		WriteError(w, http.StatusServiceUnavailable, "Router client not available: "+err.Error())
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 6*time.Second)
	defer cancel()

	conns, err := client.GetFirewallConnections(ctx)
	if err != nil {
		WriteError(w, http.StatusBadGateway, "Failed to read firewall connections: "+err.Error())
		return
	}

	// Build Device & User attribution maps
	devices, _ := h.database.GetDevices(routerID)
	users, _ := h.database.GetUsers(routerID)

	userMap := make(map[int]db.User)
	for _, u := range users {
		userMap[u.ID] = u
	}

	deviceByIP := make(map[string]db.Device)
	for _, d := range devices {
		if d.IPAddress.Valid && d.IPAddress.String != "" {
			deviceByIP[strings.TrimSpace(d.IPAddress.String)] = d
		}
	}

	matched := 0
	items := make([]LiveConnectionItem, 0, len(conns))

	for _, c := range conns {
		srcIP, srcPort := splitEndpoint(c.SrcAddress)
		dstIP, dstPort := splitEndpoint(c.DstAddress)
		proto := strings.ToLower(c.Protocol)

		if protoFilter != "" && proto != protoFilter {
			continue
		}

		// Device / User attribution
		var matchedDev *db.Device
		var matchedUser *db.User

		if d, ok := deviceByIP[srcIP]; ok {
			matchedDev = &d
		} else if d, ok := deviceByIP[dstIP]; ok {
			matchedDev = &d
		}

		if matchedDev != nil && matchedDev.UserID != nil {
			if u, ok := userMap[*matchedDev.UserID]; ok {
				matchedUser = &u
			}
		}

		if devIDFilter != nil && (matchedDev == nil || matchedDev.ID != *devIDFilter) {
			continue
		}
		if userIDFilter != nil && (matchedUser == nil || matchedUser.ID != *userIDFilter) {
			continue
		}

		var devName *string
		if matchedDev != nil {
			name := matchedDev.CustomName.String
			if name == "" {
				name = matchedDev.Hostname.String
			}
			if name != "" {
				devName = &name
			}
		}

		var userName *string
		if matchedUser != nil && matchedUser.Name != "" {
			userName = &matchedUser.Name
		}

		// Remote endpoint resolution & geo classification
		countryCode := "LOCAL"
		countryName := "Local Network"
		flagEmoji := "🏠"

		remoteIP := dstIP
		if !isPrivateOrLocalIP(dstIP) {
			remoteIP = dstIP
			countryCode = "??"
			countryName = "External"
			flagEmoji = "🌐"
		} else if !isPrivateOrLocalIP(srcIP) {
			remoteIP = srcIP
			countryCode = "??"
			countryName = "External"
			flagEmoji = "🌐"
		}

		if searchFilter != "" {
			haystack := strings.ToLower(fmt.Sprintf("%s %s %s %s %s",
				srcIP, dstIP, remoteIP,
				derefStr(devName), derefStr(userName),
			))
			if !strings.Contains(haystack, searchFilter) {
				continue
			}
		}

		matched++
		if len(items) >= limit {
			continue
		}

		origRate := c.OrigRate.Int64()
		replRate := c.ReplRate.Int64()
		origBytes := c.OrigBytes.Int64()
		replBytes := c.ReplBytes.Int64()
		totalBytes := origBytes + replBytes

		var tcpState *string
		if c.TCPState != "" {
			tcpState = &c.TCPState
		}

		var timeout *string
		if c.Timeout != "" {
			timeout = &c.Timeout
		}

		var dID *int
		if matchedDev != nil {
			dID = &matchedDev.ID
		}

		var uID *int
		if matchedUser != nil {
			uID = &matchedUser.ID
		}

		items = append(items, LiveConnectionItem{
			ID:          c.ID,
			Protocol:    proto,
			SrcIP:       srcIP,
			SrcPort:     srcPort,
			DstIP:       dstIP,
			DstPort:     dstPort,
			DeviceID:    dID,
			DeviceName:  devName,
			UserID:      uID,
			UserName:    userName,
			CountryCode: &countryCode,
			CountryName: &countryName,
			FlagEmoji:   &flagEmoji,
			TCPState:    tcpState,
			OrigRate:    origRate,
			ReplRate:    replRate,
			OrigBytes:   origBytes,
			ReplBytes:   replBytes,
			TotalBytes:  totalBytes,
			Timeout:     timeout,
			IsImmune:    false,
		})
	}

	WriteJSON(w, http.StatusOK, PaginatedLiveConnections{
		Total: matched,
		Items: items,
	})
}

func (h *ConnectionsHandler) KillConnection(w http.ResponseWriter, r *http.Request) {
	connID := chi.URLParam(r, "id")
	if connID == "" {
		WriteError(w, http.StatusBadRequest, "Connection ID required")
		return
	}

	var req KillConnectionRequest
	_ = json.NewDecoder(r.Body).Decode(&req)

	client, err := h.getClient(req.RouterID)
	if err != nil {
		WriteError(w, http.StatusServiceUnavailable, "Router client not available: "+err.Error())
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	if err := client.DeleteFirewallConnection(ctx, connID); err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to terminate connection: "+err.Error())
		return
	}

	WriteJSON(w, http.StatusOK, true)
}

func derefStr(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}
