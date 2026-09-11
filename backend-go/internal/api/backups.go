package api

import (
	"encoding/json"
	"net/http"

	"github.com/masseselsev/mikroman/internal/db"
)

type BackupHandler struct {
	database *db.DB
}

func NewBackupHandler(database *db.DB) *BackupHandler {
	return &BackupHandler{database: database}
}

func (h *BackupHandler) List(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"items":     []interface{}{},
		"total":     0,
		"page":      1,
		"page_size": 50,
		"pages":     1,
	})
}

func (h *BackupHandler) Run(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"success": true,
		"message": "Backup triggered",
	})
}

func (h *BackupHandler) Get(w http.ResponseWriter, r *http.Request) {
	WriteError(w, http.StatusNotFound, "Backup not found")
}

func (h *BackupHandler) Update(w http.ResponseWriter, r *http.Request) {
	WriteError(w, http.StatusNotFound, "Backup not found")
}

func (h *BackupHandler) Delete(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, map[string]string{"message": "Backup deleted"})
}

func (h *BackupHandler) Diff(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"diff":    "",
		"changes": 0,
	})
}

func (h *BackupHandler) DownloadRsc(w http.ResponseWriter, r *http.Request) {
	WriteError(w, http.StatusNotFound, "Backup script not found")
}

func (h *BackupHandler) DownloadBackup(w http.ResponseWriter, r *http.Request) {
	WriteError(w, http.StatusNotFound, "Backup archive not found")
}
