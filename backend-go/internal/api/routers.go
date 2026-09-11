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

type RouterHandler struct {
	database *db.DB
}

func NewRouterHandler(database *db.DB) *RouterHandler {
	return &RouterHandler{database: database}
}

type RouterCreateRequest struct {
	Name      string `json:"name"`
	Host      string `json:"host"`
	Port      int    `json:"port"`
	UseSSL    bool   `json:"use_ssl"`
	SSLVerify bool   `json:"ssl_verify"`
	Username  string `json:"username"`
	Password  string `json:"password"`
	Comment   string `json:"comment"`
	IsDefault bool   `json:"is_default"`
}

type RouterTestRequest struct {
	Host      string `json:"host"`
	Port      int    `json:"port"`
	UseSSL    bool   `json:"use_ssl"`
	SSLVerify bool   `json:"ssl_verify"`
	Username  string `json:"username"`
	Password  string `json:"password"`
}

func (h *RouterHandler) List(w http.ResponseWriter, r *http.Request) {
	routers, err := h.database.GetRouters()
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to load routers: "+err.Error())
		return
	}
	if routers == nil {
		routers = []db.Router{}
	}
	WriteJSON(w, http.StatusOK, routers)
}

func (h *RouterHandler) Get(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	router, err := h.database.GetRouter(id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to get router")
		return
	}
	if router == nil {
		WriteError(w, http.StatusNotFound, "Router not found")
		return
	}

	WriteJSON(w, http.StatusOK, router)
}

func (h *RouterHandler) Create(w http.ResponseWriter, r *http.Request) {
	var req RouterCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid request payload")
		return
	}

	if req.Name == "" || req.Host == "" {
		WriteError(w, http.StatusBadRequest, "Name and host are required")
		return
	}

	router := db.Router{
		Name:      req.Name,
		Host:      req.Host,
		Port:      req.Port,
		UseSSL:    req.UseSSL,
		SSLVerify: req.SSLVerify,
		Username:  req.Username,
		Password:  req.Password,
		IsActive:  true,
		IsDefault: req.IsDefault,
	}

	if err := h.database.CreateRouter(&router); err != nil {
		WriteError(w, http.StatusBadRequest, "Failed to create router: "+err.Error())
		return
	}

	WriteJSON(w, http.StatusCreated, router)
}

func (h *RouterHandler) TestConnection(w http.ResponseWriter, r *http.Request) {
	var req RouterTestRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid test payload")
		return
	}

	client, err := routeros.NewClient(routeros.Config{
		Host:      req.Host,
		Port:      req.Port,
		Username:  req.Username,
		Password:  req.Password,
		UseSSL:    req.UseSSL,
		SSLVerify: req.SSLVerify,
		Timeout:   4 * time.Second,
	})
	if err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid client config: "+err.Error())
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 4*time.Second)
	defer cancel()

	res, err := client.GetSystemResource(ctx)
	if err != nil {
		WriteError(w, http.StatusBadRequest, "Connection failed: "+err.Error())
		return
	}

	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"success":  true,
		"platform": res.Platform,
		"board":    res.BoardName,
		"version":  res.Version,
		"cpu":      res.CPULoad + "%",
		"uptime":   res.Uptime,
	})
}

func (h *RouterHandler) Activate(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	// Clear default on all, set on target
	_, _ = h.database.SqlDB.Exec("UPDATE routers SET is_default = 0")
	_, err := h.database.SqlDB.Exec("UPDATE routers SET is_default = 1 WHERE id = ?", id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to activate router")
		return
	}

	WriteJSON(w, http.StatusOK, map[string]string{"message": "Router activated successfully"})
}

func (h *RouterHandler) Delete(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	var payload struct {
		Mode string `json:"mode"`
	}
	_ = json.NewDecoder(r.Body).Decode(&payload)

	if payload.Mode == "purge" {
		_, err := h.database.SqlDB.Exec("DELETE FROM routers WHERE id = ?", id)
		if err != nil {
			WriteError(w, http.StatusInternalServerError, "Failed to purge router")
			return
		}
	} else {
		// Archive mode: set archived_at
		_, err := h.database.SqlDB.Exec("UPDATE routers SET is_active = 0, is_default = 0, archived_at = CURRENT_TIMESTAMP WHERE id = ?", id)
		if err != nil {
			WriteError(w, http.StatusInternalServerError, "Failed to archive router")
			return
		}
	}

	WriteJSON(w, http.StatusOK, map[string]string{"message": "Router removed successfully"})
}
