package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type MetricsHandler struct {
	database *db.DB
	client   *routeros.Client
}

func NewMetricsHandler(database *db.DB, client *routeros.Client) *MetricsHandler {
	return &MetricsHandler{database: database, client: client}
}

type MonitoredInterfacesConfigDTO struct {
	RouterID           *int     `json:"router_id,omitempty"`
	SelectedInterfaces []string `json:"selected_interfaces"`
}

func (h *MetricsHandler) GetMonitoredInterfacesConfig(w http.ResponseWriter, r *http.Request) {
	rID := r.URL.Query().Get("router_id")
	settingKey := "monitored_interfaces_default"
	if rID != "" {
		settingKey = fmt.Sprintf("monitored_interfaces_%s", rID)
	}

	val, err := h.database.GetSetting(settingKey)
	var selected []string
	if err == nil && val != "" {
		_ = json.Unmarshal([]byte(val), &selected)
	}
	if selected == nil {
		selected = []string{}
	}

	var routerID *int
	if id, err := strconv.Atoi(rID); err == nil {
		routerID = &id
	}

	WriteJSON(w, http.StatusOK, MonitoredInterfacesConfigDTO{
		RouterID:           routerID,
		SelectedInterfaces: selected,
	})
}

func (h *MetricsHandler) SaveMonitoredInterfacesConfig(w http.ResponseWriter, r *http.Request) {
	var payload MonitoredInterfacesConfigDTO
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid payload")
		return
	}

	settingKey := "monitored_interfaces_default"
	if payload.RouterID != nil {
		settingKey = fmt.Sprintf("monitored_interfaces_%d", *payload.RouterID)
	}

	if payload.SelectedInterfaces == nil {
		payload.SelectedInterfaces = []string{}
	}
	bytesVal, _ := json.Marshal(payload.SelectedInterfaces)
	_ = h.database.SetSetting(settingKey, string(bytesVal), "Monitored WAN interfaces")

	WriteJSON(w, http.StatusOK, payload)
}

func (h *MetricsHandler) ListAvailableInterfaces(w http.ResponseWriter, r *http.Request) {
	if h.client == nil {
		WriteJSON(w, http.StatusOK, []InterfaceDTO{})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()

	rawIfaces, err := h.client.GetInterfaces(ctx)
	if err != nil {
		WriteJSON(w, http.StatusOK, []InterfaceDTO{})
		return
	}

	dtos := make([]InterfaceDTO, 0, len(rawIfaces))
	for _, iface := range rawIfaces {
		rx, _ := strconv.ParseInt(iface.RxByte, 10, 64)
		tx, _ := strconv.ParseInt(iface.TxByte, 10, 64)
		dtos = append(dtos, InterfaceDTO{
			ID:        iface.ID,
			Name:      iface.Name,
			Type:      iface.Type,
			Running:   iface.Running == "true",
			Disabled:  iface.Disabled == "true",
			Comment:   iface.Comment,
			RxByte:    rx,
			TxByte:    tx,
			ActualMTU: iface.ActualMTU,
		})
	}
	WriteJSON(w, http.StatusOK, dtos)
}

