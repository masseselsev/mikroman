package api

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type FirmwareHandler struct {
	database *db.DB
	client   *routeros.Client
}

func NewFirmwareHandler(database *db.DB, client *routeros.Client) *FirmwareHandler {
	return &FirmwareHandler{database: database, client: client}
}

type PackageUpdateInfo struct {
	Channel       string `json:"channel"`
	Installed     string `json:"installed_version"`
	Latest        string `json:"latest_version"`
	Status        string `json:"status"`
	UpdateAvail   bool   `json:"update_available"`
}

type RouterBoardInfo struct {
	IsRouterboard     bool   `json:"is_routerboard"`
	Model             string `json:"model"`
	SerialNumber      string `json:"serial_number"`
	CurrentFirmware   string `json:"current_firmware"`
	UpgradeFirmware   string `json:"upgrade_firmware"`
	FirmwareAvailable bool   `json:"firmware_available"`
}

type RouterFirmwareStatusOut struct {
	RouterID    int               `json:"router_id"`
	RouterName  string            `json:"router_name"`
	Packages    PackageUpdateInfo `json:"packages"`
	RouterBoard RouterBoardInfo   `json:"routerboard"`
	CheckedAt   time.Time         `json:"checked_at"`
}

type ChangelogOut struct {
	Version string `json:"version"`
	Notes   string `json:"notes"`
}

func (h *FirmwareHandler) getRouterClient(ctx context.Context, routerID int) (*routeros.Client, *db.Router, error) {
	router, err := h.database.GetRouter(routerID)
	if err != nil || router == nil {
		return nil, nil, err
	}
	defaultRouter, _ := h.database.GetDefaultRouter()
	if (defaultRouter == nil || defaultRouter.ID == routerID) && h.client != nil {
		return h.client, router, nil
	}
	c, err := routeros.NewClient(routeros.Config{
		Host:      router.Host,
		Port:      router.Port,
		Username:  router.Username,
		Password:  router.Password,
		UseSSL:    router.UseSSL,
		SSLVerify: router.SSLVerify,
		CACert:    router.CACert.String,
		Timeout:   4 * time.Second,
	})
	return c, router, err
}

func (h *FirmwareHandler) Status(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	routerID, _ := strconv.Atoi(idStr)

	client, router, err := h.getRouterClient(r.Context(), routerID)
	if err != nil || router == nil {
		WriteError(w, http.StatusNotFound, "Router not found")
		return
	}

	rbModel := ""
	rbFw := ""
	rbSerial := ""
	isRb := false
	if client != nil {
		rb, _ := client.GetRouterBoard(r.Context())
		if rb != nil {
			rbModel = rb.Model
			rbFw = rb.CurrentFirmware
			rbSerial = rb.SerialNumber
			isRb = true
		}
	}

	res := RouterFirmwareStatusOut{
		RouterID:   routerID,
		RouterName: router.Name,
		Packages: PackageUpdateInfo{
			Channel:     "stable",
			Installed:   "7.x",
			Latest:      "7.x",
			Status:      "System is up to date",
			UpdateAvail: false,
		},
		RouterBoard: RouterBoardInfo{
			IsRouterboard:     isRb,
			Model:             rbModel,
			SerialNumber:      rbSerial,
			CurrentFirmware:   rbFw,
			UpgradeFirmware:   rbFw,
			FirmwareAvailable: false,
		},
		CheckedAt: time.Now(),
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(res)
}

func (h *FirmwareHandler) Check(w http.ResponseWriter, r *http.Request) {
	h.Status(w, r)
}

func (h *FirmwareHandler) SetChannel(w http.ResponseWriter, r *http.Request) {
	h.Status(w, r)
}

func (h *FirmwareHandler) Changelog(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(ChangelogOut{
		Version: r.URL.Query().Get("version"),
		Notes:   "Changelog available on mikrotik.com/download/changelogs",
	})
}

func (h *FirmwareHandler) Upgrade(w http.ResponseWriter, r *http.Request) {
	WriteError(w, http.StatusBadRequest, "Firmware upgrade requires manual confirmation")
}

func (h *FirmwareHandler) UpgradeBootloader(w http.ResponseWriter, r *http.Request) {
	WriteError(w, http.StatusBadRequest, "Bootloader upgrade requires manual confirmation")
}
