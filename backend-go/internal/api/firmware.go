package api

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
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
	if defaultRouter != nil && defaultRouter.ID == routerID && h.client != nil {
		return h.client, router, nil
	}
	if defaultRouter == nil && h.client != nil {
		routers, _ := h.database.GetRouters()
		if len(routers) <= 1 {
			return h.client, router, nil
		}
	}

	c, err := routeros.NewClient(routeros.Config{
		Host:      router.Host,
		Port:      router.Port,
		Username:  router.Username,
		Password:  router.Password,
		UseSSL:    router.UseSSL,
		SSLVerify: router.SSLVerify,
		CACert:    router.CACert.String,
		Timeout:   5 * time.Second,
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
	rbUpgradeFw := ""
	rbSerial := ""
	isRb := false
	fwAvail := false

	if client != nil {
		rb, _ := client.GetRouterBoard(r.Context())
		if rb != nil {
			rbModel = rb.Model
			rbFw = strings.TrimSpace(rb.CurrentFirmware)
			rbUpgradeFw = strings.TrimSpace(rb.UpgradeFirmware)
			rbSerial = rb.SerialNumber
			isRb = true
			if rbUpgradeFw != "" && rbFw != "" && rbUpgradeFw != rbFw {
				fwAvail = true
			}
		}
	}

	channel := "stable"
	installedVer := "7.x"
	latestVer := "7.x"
	statusText := "System is up to date"
	updateAvail := false

	if client != nil {
		if pkgStatus, err := client.GetPackageUpdateStatus(r.Context()); err == nil && pkgStatus != nil {
			if pkgStatus.Channel != "" {
				channel = pkgStatus.Channel
			}
			if pkgStatus.InstalledVersion != "" {
				installedVer = pkgStatus.InstalledVersion
			}
			if pkgStatus.LatestVersion != "" {
				latestVer = pkgStatus.LatestVersion
			}
			if pkgStatus.Status != "" {
				statusText = pkgStatus.Status
			}
			if (latestVer != "" && latestVer != installedVer && latestVer != "7.x") || strings.Contains(strings.ToLower(statusText), "new version") {
				updateAvail = true
			}
		}
	}

	res := RouterFirmwareStatusOut{
		RouterID:   routerID,
		RouterName: router.Name,
		Packages: PackageUpdateInfo{
			Channel:     channel,
			Installed:   installedVer,
			Latest:      latestVer,
			Status:      statusText,
			UpdateAvail: updateAvail,
		},
		RouterBoard: RouterBoardInfo{
			IsRouterboard:     isRb,
			Model:             rbModel,
			SerialNumber:      rbSerial,
			CurrentFirmware:   rbFw,
			UpgradeFirmware:   rbUpgradeFw,
			FirmwareAvailable: fwAvail,
		},
		CheckedAt: time.Now(),
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(res)
}

func (h *FirmwareHandler) Check(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	routerID, _ := strconv.Atoi(idStr)

	client, _, err := h.getRouterClient(r.Context(), routerID)
	if err == nil && client != nil {
		_, _ = client.CheckForPackageUpdates(r.Context())
	}
	h.Status(w, r)
}

type SetChannelRequest struct {
	Channel string `json:"channel"`
}

func (h *FirmwareHandler) SetChannel(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	routerID, _ := strconv.Atoi(idStr)

	var req SetChannelRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err == nil && req.Channel != "" {
		client, _, err := h.getRouterClient(r.Context(), routerID)
		if err == nil && client != nil {
			_, _ = client.SetPackageUpdateChannel(r.Context(), req.Channel)
		}
	}
	h.Status(w, r)
}

func (h *FirmwareHandler) Changelog(w http.ResponseWriter, r *http.Request) {
	ver := r.URL.Query().Get("version")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(ChangelogOut{
		Version: ver,
		Notes:   "Official RouterOS changelog available at https://mikrotik.com/download/changelogs",
	})
}

func (h *FirmwareHandler) Upgrade(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	routerID, _ := strconv.Atoi(idStr)

	client, _, err := h.getRouterClient(r.Context(), routerID)
	if err != nil || client == nil {
		WriteError(w, http.StatusNotFound, "Router not found")
		return
	}

	if err := client.InstallPackageUpdate(r.Context()); err != nil {
		WriteError(w, http.StatusBadRequest, "Upgrade command failed: "+err.Error())
		return
	}

	WriteJSON(w, http.StatusOK, map[string]string{
		"message": "Package update initiated. Router is rebooting.",
	})
}

func (h *FirmwareHandler) UpgradeBootloader(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	routerID, _ := strconv.Atoi(idStr)

	client, _, err := h.getRouterClient(r.Context(), routerID)
	if err != nil || client == nil {
		WriteError(w, http.StatusNotFound, "Router not found")
		return
	}

	if err := client.UpgradeRouterBoardFirmware(r.Context()); err != nil {
		WriteError(w, http.StatusBadRequest, "Bootloader upgrade failed: "+err.Error())
		return
	}

	WriteJSON(w, http.StatusOK, map[string]string{
		"message": "Bootloader upgrade initiated. Reboot required to apply.",
	})
}
