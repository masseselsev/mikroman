package api

import (
	"net/http"

	"github.com/masseselsev/mikroman/internal/db"
)

type ContainerHandler struct {
	database *db.DB
}

func NewContainerHandler(database *db.DB) *ContainerHandler {
	return &ContainerHandler{database: database}
}

type ContainerSupportDTO struct {
	Status  string `json:"status"`
	Message string `json:"message"`
}

type ContainerOverviewDTO struct {
	Support    ContainerSupportDTO `json:"support"`
	Containers []interface{}       `json:"containers"`
	Mounts     []interface{}       `json:"mounts"`
	Envs       []interface{}       `json:"envs"`
	Config     map[string]string   `json:"config"`
}

func (h *ContainerHandler) List(w http.ResponseWriter, r *http.Request) {
	overview := ContainerOverviewDTO{
		Support: ContainerSupportDTO{
			Status:  "no_package",
			Message: "Container package is not installed or enabled on this router",
		},
		Containers: []interface{}{},
		Mounts:     []interface{}{},
		Envs:       []interface{}{},
		Config:     map[string]string{},
	}
	WriteJSON(w, http.StatusOK, overview)
}

func (h *ContainerHandler) SetupPlan(w http.ResponseWriter, r *http.Request) {
	WriteError(w, http.StatusConflict, "Containers are not available on this router")
}

func (h *ContainerHandler) SetupApply(w http.ResponseWriter, r *http.Request) {
	WriteError(w, http.StatusConflict, "Containers are not available on this router")
}

func (h *ContainerHandler) Storage(w http.ResponseWriter, r *http.Request) {
	WriteJSON(w, http.StatusOK, map[string]interface{}{
		"items":   []interface{}{},
		"default": nil,
	})
}

func (h *ContainerHandler) Format(w http.ResponseWriter, r *http.Request) {
	WriteError(w, http.StatusConflict, "Storage format not supported")
}

func (h *ContainerHandler) Action(w http.ResponseWriter, r *http.Request) {
	WriteError(w, http.StatusNotFound, "Container not found")
}

func (h *ContainerHandler) Create(w http.ResponseWriter, r *http.Request) {
	WriteError(w, http.StatusConflict, "Containers not supported")
}
