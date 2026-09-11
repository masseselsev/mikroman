package api

import (
	"encoding/json"
	"net/http"
)

// APIResponse standardizes JSON responses matching Python's APIResponse[T].
type APIResponse struct {
	Success bool        `json:"success"`
	Message *string     `json:"message,omitempty"`
	Data    interface{} `json:"data"`
	Detail  string      `json:"detail,omitempty"`
}

// WriteJSON sends a successful JSON response.
func WriteJSON(w http.ResponseWriter, statusCode int, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	_ = json.NewEncoder(w).Encode(APIResponse{
		Success: true,
		Data:    data,
	})
}

// WriteError sends a JSON error response matching FastAPI's detail field.
func WriteError(w http.ResponseWriter, statusCode int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	_ = json.NewEncoder(w).Encode(APIResponse{
		Success: false,
		Message: &message,
		Detail:  message,
		Data:    nil,
	})
}
