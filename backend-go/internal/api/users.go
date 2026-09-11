package api

import (
	"encoding/json"
	"net/http"
	"strconv"
	"time"

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

	now := time.Now()
	today := now.Format("2006-01-02")
	anchorDay := h.database.GetBillingAnchorDay()
	cycleStart := db.CalculateBillingCycleStart(anchorDay, now)

	devStats, _ := h.database.GetDeviceVolumeStats(cycleStart, today)
	userStats, _ := h.database.GetUserVolumeStats(cycleStart, today)

	allDevices, _ := h.database.GetDevices(routerID)
	devMap := make(map[int][]db.Device)
	for _, dev := range allDevices {
		if dev.UserID != nil {
			if s, ok := devStats[dev.ID]; ok {
				dev.BytesTotalIn = s.TotalIn
				dev.BytesTotalOut = s.TotalOut
				dev.BytesCycleIn = s.CycleIn
				dev.BytesCycleOut = s.CycleOut
				dev.BytesTodayIn = s.TodayIn
				dev.BytesTodayOut = s.TodayOut
			}
			devMap[*dev.UserID] = append(devMap[*dev.UserID], dev)
		}
	}

	for i := range users {
		u := &users[i]
		u.Devices = devMap[u.ID]
		if u.Devices == nil {
			u.Devices = []db.Device{}
		}

		var uTotIn, uTotOut, uCycIn, uCycOut, uTodIn, uTodOut int64
		var maxSeen *time.Time

		for _, d := range u.Devices {
			uTotIn += d.BytesTotalIn
			uTotOut += d.BytesTotalOut
			uCycIn += d.BytesCycleIn
			uCycOut += d.BytesCycleOut
			uTodIn += d.BytesTodayIn
			uTodOut += d.BytesTodayOut

			if !d.LastSeen.IsZero() {
				if maxSeen == nil || d.LastSeen.After(*maxSeen) {
					t := d.LastSeen
					maxSeen = &t
				}
			}
		}

		if len(u.Devices) == 0 {
			if s, ok := userStats[u.ID]; ok {
				uTotIn = s.TotalIn
				uTotOut = s.TotalOut
				uCycIn = s.CycleIn
				uCycOut = s.CycleOut
				uTodIn = s.TodayIn
				uTodOut = s.TodayOut
			}
		}

		u.BytesTotalIn = uTotIn
		u.BytesTotalOut = uTotOut
		u.BytesCycleIn = uCycIn
		u.BytesCycleOut = uCycOut
		u.BytesTodayIn = uTodIn
		u.BytesTodayOut = uTodOut
		u.LastSeen = maxSeen
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

	now := time.Now()
	today := now.Format("2006-01-02")
	anchorDay := h.database.GetBillingAnchorDay()
	cycleStart := db.CalculateBillingCycleStart(anchorDay, now)

	devStats, _ := h.database.GetDeviceVolumeStats(cycleStart, today)
	userStats, _ := h.database.GetUserVolumeStats(cycleStart, today)

	allDevices, _ := h.database.GetDevices(user.RouterID)
	var maxSeen *time.Time
	var uTotIn, uTotOut, uCycIn, uCycOut, uTodIn, uTodOut int64

	for _, dev := range allDevices {
		if dev.UserID != nil && *dev.UserID == user.ID {
			if s, ok := devStats[dev.ID]; ok {
				dev.BytesTotalIn = s.TotalIn
				dev.BytesTotalOut = s.TotalOut
				dev.BytesCycleIn = s.CycleIn
				dev.BytesCycleOut = s.CycleOut
				dev.BytesTodayIn = s.TodayIn
				dev.BytesTodayOut = s.TodayOut
			}
			uTotIn += dev.BytesTotalIn
			uTotOut += dev.BytesTotalOut
			uCycIn += dev.BytesCycleIn
			uCycOut += dev.BytesCycleOut
			uTodIn += dev.BytesTodayIn
			uTodOut += dev.BytesTodayOut

			if !dev.LastSeen.IsZero() {
				if maxSeen == nil || dev.LastSeen.After(*maxSeen) {
					t := dev.LastSeen
					maxSeen = &t
				}
			}
			user.Devices = append(user.Devices, dev)
		}
	}
	if user.Devices == nil {
		user.Devices = []db.Device{}
	}

	if len(user.Devices) == 0 {
		if s, ok := userStats[user.ID]; ok {
			uTotIn = s.TotalIn
			uTotOut = s.TotalOut
			uCycIn = s.CycleIn
			uCycOut = s.CycleOut
			uTodIn = s.TodayIn
			uTodOut = s.TodayOut
		}
	}

	user.BytesTotalIn = uTotIn
	user.BytesTotalOut = uTotOut
	user.BytesCycleIn = uCycIn
	user.BytesCycleOut = uCycOut
	user.BytesTodayIn = uTodIn
	user.BytesTodayOut = uTodOut
	user.LastSeen = maxSeen

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
