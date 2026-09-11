package api

import (
	"encoding/json"
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"

	"github.com/masseselsev/mikroman/internal/db"
)

type UserHandler struct {
	database *db.DB
}

func NewUserHandler(database *db.DB) *UserHandler {
	return &UserHandler{database: database}
}

func (h *UserHandler) List(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	var routerID *int
	if rID := q.Get("router_id"); rID != "" {
		if id, err := strconv.Atoi(rID); err == nil {
			routerID = &id
		}
	}

	users, err := h.database.GetUsers(routerID)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to load users")
		return
	}

	allDevices, _ := h.database.GetDevices(routerID)
	devMap := make(map[int][]db.Device)
	for _, dev := range allDevices {
		if dev.UserID != nil {
			devMap[*dev.UserID] = append(devMap[*dev.UserID], dev)
		}
	}

	for i := range users {
		users[i].Devices = devMap[users[i].ID]
		if users[i].Devices == nil {
			users[i].Devices = []db.Device{}
		}
	}

	if users == nil {
		users = []db.User{}
	}
	WriteJSON(w, http.StatusOK, users)
}

func (h *UserHandler) Get(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	user, err := h.database.GetUser(id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to load user")
		return
	}
	if user == nil {
		WriteError(w, http.StatusNotFound, "User not found")
		return
	}

	allDevices, _ := h.database.GetDevices(user.RouterID)
	for _, dev := range allDevices {
		if dev.UserID != nil && *dev.UserID == user.ID {
			user.Devices = append(user.Devices, dev)
		}
	}
	if user.Devices == nil {
		user.Devices = []db.Device{}
	}

	WriteJSON(w, http.StatusOK, user)
}

func (h *UserHandler) Create(w http.ResponseWriter, r *http.Request) {
	var user db.User
	if err := json.NewDecoder(r.Body).Decode(&user); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid payload")
		return
	}

	if user.Name == "" {
		WriteError(w, http.StatusBadRequest, "User name is required")
		return
	}
	if user.AvatarIcon == "" {
		user.AvatarIcon = "user"
	}
	if user.SpeedLimit == "" {
		user.SpeedLimit = "unlimited"
	}

	if err := h.database.CreateUser(&user); err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to create user: "+err.Error())
		return
	}

	user.Devices = []db.Device{}
	WriteJSON(w, http.StatusCreated, user)
}

func (h *UserHandler) Update(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	existing, err := h.database.GetUser(id)
	if err != nil || existing == nil {
		WriteError(w, http.StatusNotFound, "User not found")
		return
	}

	var payload map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid payload")
		return
	}

	if name, ok := payload["name"].(string); ok && name != "" {
		existing.Name = name
	}
	if icon, ok := payload["avatar_icon"].(string); ok && icon != "" {
		existing.AvatarIcon = icon
	}
	if limit, ok := payload["speed_limit"].(string); ok && limit != "" {
		existing.SpeedLimit = limit
	}
	if paused, ok := payload["is_paused"].(bool); ok {
		existing.IsPaused = paused
	}
	if priority, ok := payload["priority"].(float64); ok {
		existing.Priority = int(priority)
	}

	if err := h.database.UpdateUser(existing); err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to update user: "+err.Error())
		return
	}

	WriteJSON(w, http.StatusOK, existing)
}

func (h *UserHandler) Delete(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	id, _ := strconv.Atoi(idStr)

	// Unassign devices first
	_, _ = h.database.SqlDB.Exec("UPDATE devices SET user_id = NULL WHERE user_id = ?", id)

	if err := h.database.DeleteUser(id); err != nil {
		WriteError(w, http.StatusInternalServerError, "Failed to delete user")
		return
	}

	WriteJSON(w, http.StatusOK, map[string]string{"message": "User deleted successfully"})
}

func (h *UserHandler) Reorder(w http.ResponseWriter, r *http.Request) {
	var payload struct {
		UserIDs []int `json:"user_ids"`
	}
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid payload")
		return
	}

	for order, id := range payload.UserIDs {
		_, _ = h.database.SqlDB.Exec("UPDATE users SET sort_order = ? WHERE id = ?", order, id)
	}

	WriteJSON(w, http.StatusOK, map[string]string{"message": "Order updated"})
}
