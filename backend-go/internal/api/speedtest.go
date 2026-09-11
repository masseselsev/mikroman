package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
	"github.com/masseselsev/mikroman/internal/services"
)

type SpeedTestHandler struct {
	database *db.DB
	client   *routeros.Client
	mu       sync.Mutex
	clients  map[int]*routeros.Client
}

func NewSpeedTestHandler(database *db.DB, client *routeros.Client) *SpeedTestHandler {
	clients := make(map[int]*routeros.Client)
	if client != nil {
		if def, err := database.GetDefaultRouter(); err == nil && def != nil {
			clients[def.ID] = client
		}
	}
	return &SpeedTestHandler{
		database: database,
		client:   client,
		clients:  clients,
	}
}

func (h *SpeedTestHandler) getClient(routerID int) (*routeros.Client, error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	if c, ok := h.clients[routerID]; ok && c != nil {
		return c, nil
	}

	defaultRouter, _ := h.database.GetDefaultRouter()
	if (defaultRouter == nil || defaultRouter.ID == routerID) && h.client != nil {
		h.clients[routerID] = h.client
		return h.client, nil
	}

	router, err := h.database.GetRouter(routerID)
	if err != nil || router == nil {
		if h.client != nil {
			return h.client, nil
		}
		return nil, fmt.Errorf("router %d not found", routerID)
	}

	newClient, err := routeros.NewClient(routeros.Config{
		Host:      router.Host,
		Port:      router.Port,
		Username:  router.Username,
		Password:  router.Password,
		UseSSL:    router.UseSSL,
		SSLVerify: router.SSLVerify,
		CACert:    router.CACert.String,
		Timeout:   10 * time.Second,
	})
	if err != nil {
		return nil, err
	}

	h.clients[routerID] = newClient
	return newClient, nil
}

type SpeedTestStatusDTO struct {
	CanRun          bool        `json:"can_run"`
	Reason          string      `json:"reason"`
	ContainerID     *string     `json:"container_id,omitempty"`
	ContainerStatus *string     `json:"container_status,omitempty"`
	LoggingEnabled  bool        `json:"logging_enabled"`
	LastResult      interface{} `json:"last_result"`
}

func (h *SpeedTestHandler) Status(w http.ResponseWriter, r *http.Request) {
	routerID, err := strconv.Atoi(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid router ID")
		return
	}

	lastResult, _ := h.database.GetLatestSpeedTestResult(routerID)

	client, err := h.getClient(routerID)
	if err != nil || client == nil {
		WriteJSON(w, http.StatusOK, SpeedTestStatusDTO{
			CanRun:     false,
			Reason:     "unreachable",
			LastResult: lastResult,
		})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	runner := services.NewSpeedTestRunner(client)
	container, err := runner.FindContainer(ctx)
	if err != nil {
		WriteJSON(w, http.StatusOK, SpeedTestStatusDTO{
			CanRun:     false,
			Reason:     "unreachable",
			LastResult: lastResult,
		})
		return
	}

	if container == nil {
		WriteJSON(w, http.StatusOK, SpeedTestStatusDTO{
			CanRun:     false,
			Reason:     "no_container",
			LastResult: lastResult,
		})
		return
	}

	cID := container.ID
	cStatus := container.Status
	if cStatus == "" {
		cStatus = "stopped"
	}

	WriteJSON(w, http.StatusOK, SpeedTestStatusDTO{
		CanRun:          true,
		Reason:          "ready",
		ContainerID:     &cID,
		ContainerStatus: &cStatus,
		LoggingEnabled:  true,
		LastResult:      lastResult,
	})
}

func (h *SpeedTestHandler) Run(w http.ResponseWriter, r *http.Request) {
	routerID, err := strconv.Atoi(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid router ID")
		return
	}

	client, err := h.getClient(routerID)
	if err != nil || client == nil {
		WriteError(w, http.StatusBadGateway, "Router is unreachable")
		return
	}

	runner := services.NewSpeedTestRunner(client)
	reading, err := runner.Run(r.Context(), 120*time.Second)
	if err != nil && reading.Status != "ok" {
		errMsg := err.Error()
		if reading.Error != nil && *reading.Error != "" {
			errMsg = *reading.Error
		}
		dbModel := reading.ToDBModel(routerID)
		_ = h.database.InsertSpeedTestResult(dbModel)
		WriteJSON(w, http.StatusOK, map[string]interface{}{"result": dbModel, "error": errMsg})
		return
	}

	dbModel := reading.ToDBModel(routerID)
	_ = h.database.InsertSpeedTestResult(dbModel)
	WriteJSON(w, http.StatusOK, map[string]interface{}{"result": dbModel})
}

type CreateContainerPayload struct {
	Interface string `json:"interface"`
	RootDir   string `json:"root_dir"`
	Image     string `json:"image"`
}

func (h *SpeedTestHandler) CreateContainer(w http.ResponseWriter, r *http.Request) {
	routerID, err := strconv.Atoi(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid router ID")
		return
	}

	var payload CreateContainerPayload
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid request payload")
		return
	}

	if strings.TrimSpace(payload.Interface) == "" || strings.TrimSpace(payload.RootDir) == "" {
		WriteError(w, http.StatusBadRequest, "Interface and root_dir are required")
		return
	}

	client, err := h.getClient(routerID)
	if err != nil || client == nil {
		WriteError(w, http.StatusBadGateway, "Router is unreachable")
		return
	}

	runner := services.NewSpeedTestRunner(client)
	if err := runner.CreateContainer(r.Context(), payload.Interface, payload.RootDir, payload.Image); err != nil {
		WriteError(w, http.StatusInternalServerError, fmt.Sprintf("Failed to create container: %v", err))
		return
	}

	WriteJSON(w, http.StatusOK, map[string]string{"message": "Speed test container created successfully"})
}

func (h *SpeedTestHandler) History(w http.ResponseWriter, r *http.Request) {
	routerID, err := strconv.Atoi(chi.URLParam(r, "id"))
	if err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid router ID")
		return
	}

	limit := 20
	if lStr := r.URL.Query().Get("limit"); lStr != "" {
		if l, err := strconv.Atoi(lStr); err == nil && l > 0 {
			limit = l
		}
	}

	history, err := h.database.GetSpeedTestHistory(routerID, limit)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to load speed test history")
		return
	}

	WriteJSON(w, http.StatusOK, history)
}
