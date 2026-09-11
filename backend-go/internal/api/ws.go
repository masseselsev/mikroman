package api

import (
	"encoding/json"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool {
		return true // Allow all origins for the SPA
	},
}

type clientConn struct {
	ws       *websocket.Conn
	routerID int
	writeMu  sync.Mutex
}

func (c *clientConn) send(data []byte) error {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	return c.ws.WriteMessage(websocket.TextMessage, data)
}

// Hub manages active WebSocket clients with router scoping and broadcasts events.
type Hub struct {
	mu         sync.RWMutex
	clients    map[*clientConn]bool
	lastFrames map[int][]byte // routerID -> cached telemetry frame (0 = default router)
}

func NewHub() *Hub {
	return &Hub{
		clients:    make(map[*clientConn]bool),
		lastFrames: make(map[int][]byte),
	}
}

func (h *Hub) HandleWS(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}

	routerID := 0
	if rID := r.URL.Query().Get("router_id"); rID != "" {
		if id, err := strconv.Atoi(rID); err == nil && id > 0 {
			routerID = id
		}
	}

	client := &clientConn{
		ws:       conn,
		routerID: routerID,
	}

	h.mu.Lock()
	h.clients[client] = true
	var cachedFrame []byte
	if f, ok := h.lastFrames[routerID]; ok {
		cachedFrame = f
	} else if f, ok := h.lastFrames[0]; ok {
		cachedFrame = f
	}
	h.mu.Unlock()

	defer func() {
		h.mu.Lock()
		delete(h.clients, client)
		h.mu.Unlock()
		_ = conn.Close()
	}()

	// Send initial connected event
	connPayload, _ := json.Marshal(map[string]interface{}{
		"type":      "connected",
		"timestamp": time.Now().Unix(),
		"router_id": routerID,
	})
	_ = client.send(connPayload)

	// If we have a cached telemetry frame for this router, replay it immediately
	// so the dashboard populates instantly on connect instead of waiting 10 seconds.
	if cachedFrame != nil {
		_ = client.send(cachedFrame)
	}

	// Keep alive & read pump
	for {
		_, _, err := conn.ReadMessage()
		if err != nil {
			break
		}
	}
}

// Broadcast sends a JSON event to all connected clients.
func (h *Hub) Broadcast(event interface{}) {
	data, err := json.Marshal(event)
	if err != nil {
		return
	}

	h.mu.RLock()
	targets := make([]*clientConn, 0, len(h.clients))
	for c := range h.clients {
		targets = append(targets, c)
	}
	h.mu.RUnlock()

	for _, c := range targets {
		_ = c.send(data)
	}
}

// BroadcastRouter sends a JSON event to clients subscribed to routerID (or default).
func (h *Hub) BroadcastRouter(routerID int, isDefault bool, event interface{}) {
	data, err := json.Marshal(event)
	if err != nil {
		return
	}

	h.mu.Lock()
	h.lastFrames[routerID] = data
	if isDefault {
		h.lastFrames[0] = data
	}
	targets := make([]*clientConn, 0, len(h.clients))
	for c := range h.clients {
		if c.routerID == routerID || (c.routerID == 0 && isDefault) {
			targets = append(targets, c)
		}
	}
	h.mu.Unlock()

	for _, c := range targets {
		_ = c.send(data)
	}
}
