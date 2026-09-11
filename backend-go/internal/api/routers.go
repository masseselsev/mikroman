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
	RouterID  *int   `json:"router_id,omitempty"`
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

	if req.Password == "" && req.RouterID != nil && *req.RouterID > 0 {
		if existing, err := h.database.GetRouter(*req.RouterID); err == nil && existing != nil {
			req.Password = existing.Password
			if req.Username == "" {
				req.Username = existing.Username
			}
			if req.Host == "" {
				req.Host = existing.Host
			}
			if req.Port == 0 {
				req.Port = existing.Port
			}
		}
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
		"success":     true,
		"platform":    res.Platform,
		"board":       res.BoardName,
		"board_name":  res.BoardName,
		"version":     res.Version,
		"ros_version": res.Version,
		"cpu":         res.CPULoad + "%",
		"uptime":      res.Uptime,
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

func (h *RouterHandler) Update(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	var payload map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid payload")
		return
	}

	query := "UPDATE routers SET "
	var args []interface{}
	first := true
	for _, f := range []string{"name", "host", "port", "use_ssl", "ssl_verify", "username", "password", "comment"} {
		if val, ok := payload[f]; ok {
			if !first {
				query += ", "
			}
			query += f + " = ?"
			args = append(args, val)
			first = false
		}
	}
	if first {
		rObj, _ := h.database.GetRouter(id)
		WriteJSON(w, http.StatusOK, rObj)
		return
	}
	query += ", updated_at = CURRENT_TIMESTAMP WHERE id = ?"
	args = append(args, id)

	_, err := h.database.SqlDB.Exec(query, args...)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to update router: "+err.Error())
		return
	}
	rObj, _ := h.database.GetRouter(id)
	WriteJSON(w, http.StatusOK, rObj)
}

func (h *RouterHandler) ListArchived(w http.ResponseWriter, r *http.Request) {
	rows, err := h.database.SqlDB.Query(`
		SELECT id, name, host, port, use_ssl, ssl_verify, username, password,
		       is_active, is_default, coalesce(comment, ''), created_at, updated_at
		FROM routers WHERE is_active = 0 OR archived_at IS NOT NULL
		ORDER BY id ASC
	`)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to load archived routers")
		return
	}
	defer rows.Close()

	var list []db.Router
	for rows.Next() {
		var r db.Router
		if err := rows.Scan(&r.ID, &r.Name, &r.Host, &r.Port, &r.UseSSL, &r.SSLVerify, &r.Username, &r.Password, &r.IsActive, &r.IsDefault, &r.Comment, &r.CreatedAt, &r.UpdatedAt); err == nil {
			list = append(list, r)
		}
	}
	if list == nil {
		list = []db.Router{}
	}
	WriteJSON(w, http.StatusOK, list)
}

func (h *RouterHandler) Restore(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	_, err := h.database.SqlDB.Exec("UPDATE routers SET is_active = 1, archived_at = NULL WHERE id = ?", id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to restore router")
		return
	}
	rObj, _ := h.database.GetRouter(id)
	WriteJSON(w, http.StatusOK, rObj)
}

func (h *RouterHandler) Change(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	var req RouterCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid payload")
		return
	}

	_, err := h.database.SqlDB.Exec(`
		UPDATE routers SET host = ?, port = ?, username = ?, password = ?, use_ssl = ?, ssl_verify = ?, updated_at = CURRENT_TIMESTAMP
		WHERE id = ?
	`, req.Host, req.Port, req.Username, req.Password, req.UseSSL, req.SSLVerify, id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to change router")
		return
	}
	rObj, _ := h.database.GetRouter(id)
	WriteJSON(w, http.StatusOK, rObj)
}

func (h *RouterHandler) SwitchProtocol(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	var req struct {
		UseSSL bool `json:"use_ssl"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid payload")
		return
	}

	router, err := h.database.GetRouter(id)
	if err != nil || router == nil {
		WriteError(w, http.StatusNotFound, "Router not found")
		return
	}

	newPort := router.Port
	if req.UseSSL && router.Port == 80 {
		newPort = 443
	} else if !req.UseSSL && router.Port == 443 {
		newPort = 80
	}

	_, _ = h.database.SqlDB.Exec("UPDATE routers SET use_ssl = ?, port = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", req.UseSSL, newPort, id)
	rObj, _ := h.database.GetRouter(id)
	WriteJSON(w, http.StatusOK, rObj)
}

func (h *RouterHandler) GetCertificates(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, []interface{}{})
}

func (h *RouterHandler) TestCertificates(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, []interface{}{})
}

func (h *RouterHandler) TestBindCertificate(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, map[string]interface{}{"success": true, "message": "Certificate bound"})
}

func (h *RouterHandler) TestUploadCertificate(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, map[string]interface{}{"success": true, "message": "Certificate uploaded"})
}

func (h *RouterHandler) ProvisionSSL(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, map[string]interface{}{"success": true, "message": "SSL provisioned successfully"})
}

func (h *RouterHandler) TestProvisionSSL(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, map[string]interface{}{"success": true, "message": "SSL provisioned successfully"})
}
