package api

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
)

type TelegramHandler struct {
	database *db.DB
}

func NewTelegramHandler(database *db.DB) *TelegramHandler {
	return &TelegramHandler{database: database}
}

type TelegramTestRequest struct {
	BotToken string `json:"bot_token"`
	AdminIDs string `json:"admin_ids"`
}

func (h *TelegramHandler) Test(w http.ResponseWriter, r *http.Request) {
	var req TelegramTestRequest
	_ = json.NewDecoder(r.Body).Decode(&req)

	token := strings.TrimSpace(req.BotToken)
	adminIDsStr := strings.TrimSpace(req.AdminIDs)

	if token == "" {
		token, _ = h.database.GetSetting("telegram_bot_token")
		token = strings.TrimSpace(token)
	}
	if adminIDsStr == "" {
		adminIDsStr, _ = h.database.GetSetting("telegram_admin_ids")
		adminIDsStr = strings.TrimSpace(adminIDsStr)
	}

	if token == "" {
		WriteError(w, http.StatusBadRequest, "Telegram bot token not configured")
		return
	}

	httpClient := &http.Client{Timeout: 5 * time.Second}

	// Verify bot token with getMe
	getMeURL := fmt.Sprintf("https://api.telegram.org/bot%s/getMe", token)
	resp, err := httpClient.Get(getMeURL)
	if err != nil {
		WriteError(w, http.StatusBadRequest, "Failed to connect to Telegram: "+err.Error())
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		WriteError(w, http.StatusBadRequest, fmt.Sprintf("Invalid Telegram Bot Token (HTTP %d)", resp.StatusCode))
		return
	}

	var meResult struct {
		Ok     bool `json:"ok"`
		Result struct {
			Username  string `json:"username"`
			FirstName string `json:"first_name"`
		} `json:"result"`
	}
	_ = json.NewDecoder(resp.Body).Decode(&meResult)
	botName := meResult.Result.Username
	if botName == "" {
		botName = meResult.Result.FirstName
	}

	if adminIDsStr == "" {
		msg := fmt.Sprintf("Bot @%s verified. Enter Admin Chat ID to receive test messages.", botName)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_ = json.NewEncoder(w).Encode(APIResponse{
			Success: true,
			Message: &msg,
			Data:    true,
		})
		return
	}

	// Send test message to configured admin chat IDs
	text := fmt.Sprintf("🧪 <b>MikroMan Test Alert</b>\n\nYour Telegram bot @%s is successfully connected to MikroMan!\nType <code>/start</code> or <code>/help</code> to view available commands.", botName)

	for _, p := range strings.Split(adminIDsStr, ",") {
		chatID := strings.TrimSpace(p)
		if chatID == "" {
			continue
		}

		sendBody, _ := json.Marshal(map[string]interface{}{
			"chat_id":    chatID,
			"text":       text,
			"parse_mode": "HTML",
		})
		sendURL := fmt.Sprintf("https://api.telegram.org/bot%s/sendMessage", token)
		sResp, sErr := httpClient.Post(sendURL, "application/json", bytes.NewReader(sendBody))
		if sErr == nil {
			sResp.Body.Close()
		}
	}

	msg := fmt.Sprintf("Test alert sent to Telegram admins via @%s", botName)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(APIResponse{
		Success: true,
		Message: &msg,
		Data:    true,
	})
}
