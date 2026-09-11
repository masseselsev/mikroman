package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/services"
)

type BackupHandler struct {
	database      *db.DB
	backupService *services.BackupService
}

func NewBackupHandler(database *db.DB) *BackupHandler {
	return &BackupHandler{
		database:      database,
		backupService: services.NewBackupService(database),
	}
}

type RouterBackupResponse struct {
	ID           int     `json:"id"`
	RouterID     int     `json:"router_id"`
	CreatedAt    string  `json:"created_at"`
	Outcome      string  `json:"outcome"`
	Source       string  `json:"source"`
	Fingerprint  *string `json:"fingerprint,omitempty"`
	RSCBytes     int64   `json:"rsc_bytes"`
	BackupBytes  int64   `json:"backup_bytes"`
	IsPinned     bool    `json:"is_pinned"`
	Note         *string `json:"note,omitempty"`
	Model        *string `json:"model,omitempty"`
	Serial       *string `json:"serial,omitempty"`
	OSVersion    *string `json:"os_version,omitempty"`
	ErrorMessage *string `json:"error_message,omitempty"`
	DurationMS   int     `json:"duration_ms"`
	HasRSC       bool    `json:"has_rsc"`
	HasBinary    bool    `json:"has_binary"`
}

func toBackupResponse(b *db.RouterBackup) RouterBackupResponse {
	var fp, note, model, serial, osVer, errMsg *string
	if b.Fingerprint.Valid && b.Fingerprint.String != "" {
		s := b.Fingerprint.String
		fp = &s
	}
	if b.Note.Valid && b.Note.String != "" {
		s := b.Note.String
		note = &s
	}
	if b.Model.Valid && b.Model.String != "" {
		s := b.Model.String
		model = &s
	}
	if b.Serial.Valid && b.Serial.String != "" {
		s := b.Serial.String
		serial = &s
	}
	if b.OSVersion.Valid && b.OSVersion.String != "" {
		s := b.OSVersion.String
		osVer = &s
	}
	if b.ErrorMessage.Valid && b.ErrorMessage.String != "" {
		s := b.ErrorMessage.String
		errMsg = &s
	}

	hasRSC := b.RSCBytes > 0 || (b.RSCContent.Valid && b.RSCContent.String != "")
	hasBinary := b.BackupBytes > 0 || (b.BackupFilePath.Valid && b.BackupFilePath.String != "")

	return RouterBackupResponse{
		ID:           b.ID,
		RouterID:     b.RouterID,
		CreatedAt:    b.CreatedAt.UTC().Format("2006-01-02T15:04:05Z"),
		Outcome:      b.Outcome,
		Source:       b.Source,
		Fingerprint:  fp,
		RSCBytes:     b.RSCBytes,
		BackupBytes:  b.BackupBytes,
		IsPinned:     b.IsPinned,
		Note:         note,
		Model:        model,
		Serial:       serial,
		OSVersion:    osVer,
		ErrorMessage: errMsg,
		DurationMS:   b.DurationMS,
		HasRSC:       hasRSC,
		HasBinary:    hasBinary,
	}
}

func (h *BackupHandler) List(w http.ResponseWriter, r *http.Request) {
	routerID, _ := strconv.Atoi(chi.URLParam(r, "id"))
	q := r.URL.Query()

	page := 1
	if p, err := strconv.Atoi(q.Get("page")); err == nil && p > 0 {
		page = p
	}

	pageSize := 50
	if ps, err := strconv.Atoi(q.Get("page_size")); err == nil && ps > 0 {
		pageSize = ps
	}

	var outcome *string
	if o := q.Get("outcome"); o != "" {
		outcome = &o
	}

	pinnedOnly := q.Get("pinned_only") == "true" || q.Get("pinned_only") == "1"

	items, total, err := h.database.ListRouterBackups(routerID, outcome, pinnedOnly, page, pageSize)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to list backups: "+err.Error())
		return
	}

	respItems := make([]RouterBackupResponse, 0, len(items))
	for _, item := range items {
		respItems = append(respItems, toBackupResponse(&item))
	}

	pages := 1
	if pageSize > 0 && total > 0 {
		pages = (total + pageSize - 1) / pageSize
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"items":     respItems,
		"total":     total,
		"page":      page,
		"page_size": pageSize,
		"pages":     pages,
	})
}

func (h *BackupHandler) Run(w http.ResponseWriter, r *http.Request) {
	routerID, _ := strconv.Atoi(chi.URLParam(r, "id"))

	rec, err := h.backupService.RunRouterBackup(r.Context(), routerID, "manual")
	if err != nil && rec == nil {
		WriteError(w, http.StatusInternalServerError, "Backup failed: "+err.Error())
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(toBackupResponse(rec))
}

func (h *BackupHandler) Get(w http.ResponseWriter, r *http.Request) {
	routerID, _ := strconv.Atoi(chi.URLParam(r, "id"))
	backupID, _ := strconv.Atoi(chi.URLParam(r, "backupId"))

	b, err := h.database.GetRouterBackup(backupID)
	if err != nil || b == nil || b.RouterID != routerID {
		WriteError(w, http.StatusNotFound, "Backup not found")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(toBackupResponse(b))
}

type RouterBackupUpdateReq struct {
	IsPinned *bool   `json:"is_pinned"`
	Note     *string `json:"note"`
}

func (h *BackupHandler) Update(w http.ResponseWriter, r *http.Request) {
	routerID, _ := strconv.Atoi(chi.URLParam(r, "id"))
	backupID, _ := strconv.Atoi(chi.URLParam(r, "backupId"))

	b, err := h.database.GetRouterBackup(backupID)
	if err != nil || b == nil || b.RouterID != routerID {
		WriteError(w, http.StatusNotFound, "Backup not found")
		return
	}

	var req RouterBackupUpdateReq
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid request payload")
		return
	}

	updated, err := h.database.UpdateRouterBackup(backupID, req.IsPinned, req.Note)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to update backup")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(toBackupResponse(updated))
}

func (h *BackupHandler) Delete(w http.ResponseWriter, r *http.Request) {
	routerID, _ := strconv.Atoi(chi.URLParam(r, "id"))
	backupID, _ := strconv.Atoi(chi.URLParam(r, "backupId"))

	b, err := h.database.GetRouterBackup(backupID)
	if err != nil || b == nil || b.RouterID != routerID {
		WriteError(w, http.StatusNotFound, "Backup not found")
		return
	}

	if b.BackupFilePath.Valid && b.BackupFilePath.String != "" {
		storageDir := os.Getenv("BACKUP_STORAGE_DIR")
		if storageDir == "" {
			storageDir = "data/backups"
		}
		filePath := filepath.Join(storageDir, b.BackupFilePath.String)
		_ = os.Remove(filePath)
	}

	_ = h.database.DeleteRouterBackup(backupID)
	w.WriteHeader(http.StatusNoContent)
}

func (h *BackupHandler) Diff(w http.ResponseWriter, r *http.Request) {
	routerID, _ := strconv.Atoi(chi.URLParam(r, "id"))
	q := r.URL.Query()

	baseID, err := strconv.Atoi(q.Get("base_id"))
	if err != nil || baseID <= 0 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(services.DiffResult{
			LinesAdded:   0,
			LinesRemoved: 0,
			TotalChanges: 0,
			Hunks:        []services.DiffHunk{},
			RawUnified:   "",
		})
		return
	}

	targetIDStr := q.Get("target_id")
	if targetIDStr == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(services.DiffResult{
			BaseID:       &baseID,
			LinesAdded:   0,
			LinesRemoved: 0,
			TotalChanges: 0,
			Hunks:        []services.DiffHunk{},
			RawUnified:   "",
		})
		return
	}

	baseBackup, err := h.database.GetRouterBackup(baseID)
	if err != nil || baseBackup == nil || baseBackup.RouterID != routerID {
		WriteError(w, http.StatusNotFound, fmt.Sprintf("Baseline backup %d not found", baseID))
		return
	}

	baseText, _ := h.database.GetRouterBackupRSC(baseID)

	if strings.EqualFold(targetIDStr, "live") {
		rtr, rtrErr := h.database.GetRouter(routerID)
		if rtrErr != nil || rtr == nil {
			WriteError(w, http.StatusNotFound, "Router not found")
			return
		}

		client, cErr := h.backupService.GetRouterClient(rtr)
		if cErr != nil {
			WriteError(w, http.StatusInternalServerError, "Failed to connect to router: "+cErr.Error())
			return
		}

		rawRSC, expErr := client.ExportConfig(r.Context(), "live_diff", 35*time.Second)
		if expErr != nil {
			WriteError(w, http.StatusInternalServerError, "Failed to export live configuration: "+expErr.Error())
			return
		}

		targetText := services.NormalizeRSC(rawRSC)
		diffRes := services.DiffTexts(
			baseText,
			targetText,
			fmt.Sprintf("backup_%d.rsc", baseID),
			"live_router.rsc",
			3,
			&baseID,
			nil,
			true,
		)

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(diffRes)
		return
	}

	targetID, err := strconv.Atoi(targetIDStr)
	if err != nil || targetID <= 0 {
		WriteError(w, http.StatusBadRequest, "target_id must be a numeric backup ID or 'live'")
		return
	}

	targetBackup, err := h.database.GetRouterBackup(targetID)
	if err != nil || targetBackup == nil || targetBackup.RouterID != routerID {
		WriteError(w, http.StatusNotFound, fmt.Sprintf("Target backup %d not found", targetID))
		return
	}

	targetText, _ := h.database.GetRouterBackupRSC(targetID)
	diffRes := services.DiffTexts(
		baseText,
		targetText,
		fmt.Sprintf("backup_%d.rsc", baseID),
		fmt.Sprintf("backup_%d.rsc", targetID),
		3,
		&baseID,
		&targetID,
		false,
	)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(diffRes)
}

func (h *BackupHandler) DownloadRsc(w http.ResponseWriter, r *http.Request) {
	routerID, _ := strconv.Atoi(chi.URLParam(r, "id"))
	backupID, _ := strconv.Atoi(chi.URLParam(r, "backupId"))

	b, err := h.database.GetRouterBackup(backupID)
	if err != nil || b == nil || b.RouterID != routerID {
		WriteError(w, http.StatusNotFound, "Backup not found")
		return
	}

	rscText, err := h.database.GetRouterBackupRSC(backupID)
	if err != nil || rscText == "" {
		WriteError(w, http.StatusNotFound, "No configuration script available for this backup")
		return
	}

	filename := fmt.Sprintf("router_%d_backup_%d.rsc", routerID, backupID)
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=\"%s\"", filename))
	_, _ = w.Write([]byte(rscText))
}

func (h *BackupHandler) DownloadBackup(w http.ResponseWriter, r *http.Request) {
	routerID, _ := strconv.Atoi(chi.URLParam(r, "id"))
	backupID, _ := strconv.Atoi(chi.URLParam(r, "backupId"))

	b, err := h.database.GetRouterBackup(backupID)
	if err != nil || b == nil || b.RouterID != routerID {
		WriteError(w, http.StatusNotFound, "Backup not found")
		return
	}

	if !b.BackupFilePath.Valid || b.BackupFilePath.String == "" {
		WriteError(w, http.StatusNotFound, "No binary backup available for this run")
		return
	}

	storageDir := os.Getenv("BACKUP_STORAGE_DIR")
	if storageDir == "" {
		storageDir = "data/backups"
	}
	filePath := filepath.Join(storageDir, b.BackupFilePath.String)
	if _, err := os.Stat(filePath); err != nil {
		WriteError(w, http.StatusNotFound, "Binary backup file missing from storage disk")
		return
	}

	filename := fmt.Sprintf("router_%d_backup_%d.backup", routerID, backupID)
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename=\"%s\"", filename))
	if b.BackupPassword.Valid && b.BackupPassword.String != "" {
		w.Header().Set("X-Backup-Password", b.BackupPassword.String)
	}

	http.ServeFile(w, r, filePath)
}
