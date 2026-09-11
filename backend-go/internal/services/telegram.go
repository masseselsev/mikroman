package services

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

// Telegram API types
type TelegramUser struct {
	ID        int64  `json:"id"`
	Username  string `json:"username"`
	FirstName string `json:"first_name"`
}

type TelegramChat struct {
	ID   int64  `json:"id"`
	Type string `json:"type"`
}

type TelegramMessage struct {
	MessageID int           `json:"message_id"`
	From      *TelegramUser `json:"from,omitempty"`
	Chat      TelegramChat  `json:"chat"`
	Text      string        `json:"text"`
}

type TelegramCallbackQuery struct {
	ID      string           `json:"id"`
	From    TelegramUser     `json:"from"`
	Message *TelegramMessage `json:"message,omitempty"`
	Data    string           `json:"data"`
}

type TelegramUpdate struct {
	UpdateID      int64                  `json:"update_id"`
	Message       *TelegramMessage       `json:"message,omitempty"`
	CallbackQuery *TelegramCallbackQuery `json:"callback_query,omitempty"`
}

type InlineKeyboardButton struct {
	Text         string `json:"text"`
	CallbackData string `json:"callback_data,omitempty"`
}

type InlineKeyboardMarkup struct {
	InlineKeyboard [][]InlineKeyboardButton `json:"inline_keyboard"`
}

// TelegramBotService manages Telegram bot polling, commands, callbacks, and alerts.
type TelegramBotService struct {
	database   *db.DB
	client     *routeros.Client
	httpClient *http.Client

	mu       sync.Mutex
	running  bool
	stopCh   chan struct{}
	token    string
	adminIDs []int64
	mode     string
	lang     string
	botName  string
}

func NewTelegramBotService(database *db.DB, client *routeros.Client) *TelegramBotService {
	return &TelegramBotService{
		database: database,
		client:   client,
		httpClient: &http.Client{
			Timeout: 35 * time.Second,
		},
		stopCh: make(chan struct{}),
	}
}

func (s *TelegramBotService) Start() {
	s.mu.Lock()
	defer s.mu.Unlock()

	s.loadSettingsLocked()
	if s.token == "" {
		log.Printf("[TelegramBot] Token not configured. Bot disabled.")
		return
	}

	if s.running {
		return
	}

	s.running = true
	s.stopCh = make(chan struct{})

	// Check token and get bot name
	go func() {
		botName, err := s.fetchMe()
		if err != nil {
			log.Printf("[TelegramBot] Error fetching bot info: %v", err)
		} else {
			s.mu.Lock()
			s.botName = botName
			s.mu.Unlock()
			log.Printf("[TelegramBot] Connected as @%s", botName)
		}

		// Delete any existing webhook before polling
		_ = s.deleteWebhook()

		// Start polling loop
		s.pollLoop()
	}()
}

func (s *TelegramBotService) Stop() {
	s.mu.Lock()
	if !s.running {
		s.mu.Unlock()
		return
	}
	s.running = false
	close(s.stopCh)
	s.mu.Unlock()

	log.Printf("[TelegramBot] Stopped bot polling")
}

func (s *TelegramBotService) Reconfigure() {
	s.Stop()
	// Short pause to ensure previous HTTP poll connection closes
	time.Sleep(500 * time.Millisecond)
	s.Start()
}

func (s *TelegramBotService) loadSettingsLocked() {
	token, _ := s.database.GetSetting("telegram_bot_token")
	s.token = strings.TrimSpace(token)

	adminIDsStr, _ := s.database.GetSetting("telegram_admin_ids")
	var ids []int64
	for _, p := range strings.Split(adminIDsStr, ",") {
		p = strings.TrimSpace(p)
		if id, err := strconv.ParseInt(p, 10, 64); err == nil {
			ids = append(ids, id)
		}
	}
	s.adminIDs = ids

	mode, _ := s.database.GetSetting("telegram_mode")
	if mode == "" {
		mode = "polling"
	}
	s.mode = mode

	lang, _ := s.database.GetSetting("telegram_lang")
	if lang == "" {
		lang, _ = s.database.GetSetting("lang")
	}
	if lang == "" {
		lang = "en"
	}
	s.lang = lang
}

func (s *TelegramBotService) isAuthorized(userID int64) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.adminIDs) == 0 {
		return true
	}
	for _, id := range s.adminIDs {
		if id == userID {
			return true
		}
	}
	return false
}

func (s *TelegramBotService) fetchMe() (string, error) {
	url := fmt.Sprintf("https://api.telegram.org/bot%s/getMe", s.token)
	resp, err := s.httpClient.Get(url)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	var res struct {
		Ok     bool `json:"ok"`
		Result struct {
			Username  string `json:"username"`
			FirstName string `json:"first_name"`
		} `json:"result"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&res); err != nil {
		return "", err
	}
	if res.Result.Username != "" {
		return res.Result.Username, nil
	}
	return res.Result.FirstName, nil
}

func (s *TelegramBotService) deleteWebhook() error {
	url := fmt.Sprintf("https://api.telegram.org/bot%s/deleteWebhook?drop_pending_updates=false", s.token)
	resp, err := s.httpClient.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return nil
}

func (s *TelegramBotService) pollLoop() {
	log.Printf("[TelegramBot] Starting long polling loop...")
	var offset int64 = 0

	for {
		select {
		case <-s.stopCh:
			return
		default:
		}

		s.mu.Lock()
		token := s.token
		s.mu.Unlock()
		if token == "" {
			return
		}

		url := fmt.Sprintf("https://api.telegram.org/bot%s/getUpdates", token)
		payload := map[string]interface{}{
			"offset":  offset,
			"timeout": 20,
		}
		data, _ := json.Marshal(payload)

		req, err := http.NewRequestWithContext(context.Background(), http.MethodPost, url, bytes.NewReader(data))
		if err != nil {
			time.Sleep(2 * time.Second)
			continue
		}
		req.Header.Set("Content-Type", "application/json")

		resp, err := s.httpClient.Do(req)
		if err != nil {
			select {
			case <-s.stopCh:
				return
			case <-time.After(2 * time.Second):
				continue
			}
		}

		if resp.StatusCode == 409 {
			// Conflict: another instance or poller running
			log.Printf("[TelegramBot] Polling conflict 409, waiting 5s...")
			resp.Body.Close()
			select {
			case <-s.stopCh:
				return
			case <-time.After(5 * time.Second):
				continue
			}
		}

		if resp.StatusCode != http.StatusOK {
			resp.Body.Close()
			time.Sleep(2 * time.Second)
			continue
		}

		var updateRes struct {
			Ok     bool             `json:"ok"`
			Result []TelegramUpdate `json:"result"`
		}
		decodeErr := json.NewDecoder(resp.Body).Decode(&updateRes)
		resp.Body.Close()

		if decodeErr != nil || !updateRes.Ok {
			time.Sleep(2 * time.Second)
			continue
		}

		for _, update := range updateRes.Result {
			if update.UpdateID >= offset {
				offset = update.UpdateID + 1
			}

			if update.Message != nil {
				go s.handleMessage(update.Message)
			} else if update.CallbackQuery != nil {
				go s.handleCallbackQuery(update.CallbackQuery)
			}
		}
	}
}

func (s *TelegramBotService) handleMessage(msg *TelegramMessage) {
	if msg.From == nil {
		return
	}
	if !s.isAuthorized(msg.From.ID) && !s.isAuthorized(msg.Chat.ID) {
		_ = s.SendMessage(msg.Chat.ID, "⛔ Access denied. Your Chat ID is: <code>"+strconv.FormatInt(msg.Chat.ID, 10)+"</code>", "HTML", nil)
		return
	}

	text := strings.TrimSpace(msg.Text)
	if strings.HasPrefix(text, "/") {
		cmd := strings.Split(text, " ")[0]
		cmd = strings.TrimPrefix(cmd, "/")
		if atIdx := strings.Index(cmd, "@"); atIdx != -1 {
			cmd = cmd[:atIdx]
		}

		switch strings.ToLower(cmd) {
		case "start":
			s.cmdStart(msg.Chat.ID)
		case "help":
			s.cmdHelp(msg.Chat.ID)
		case "status":
			s.sendStatus(msg.Chat.ID, 0)
		case "users":
			s.sendUsers(msg.Chat.ID, 0)
		case "routers":
			s.sendRouters(msg.Chat.ID, 0)
		case "reboot":
			s.sendRebootPrompt(msg.Chat.ID, 0)
		default:
			_ = s.SendMessage(msg.Chat.ID, "Unknown command. Type /help to view available commands.", "", nil)
		}
	}
}

func (s *TelegramBotService) handleCallbackQuery(query *TelegramCallbackQuery) {
	if !s.isAuthorized(query.From.ID) {
		_ = s.answerCallback(query.ID, "Access denied", true)
		return
	}

	data := query.Data
	chatID := query.From.ID
	msgID := 0
	if query.Message != nil {
		chatID = query.Message.Chat.ID
		msgID = query.Message.MessageID
	}

	switch {
	case data == "cmd:status":
		_ = s.answerCallback(query.ID, "", false)
		s.sendStatus(chatID, msgID)

	case data == "cmd:users":
		_ = s.answerCallback(query.ID, "", false)
		s.sendUsers(chatID, msgID)

	case data == "cmd:routers":
		_ = s.answerCallback(query.ID, "", false)
		s.sendRouters(chatID, msgID)

	case data == "cmd:reboot_prompt":
		_ = s.answerCallback(query.ID, "", false)
		s.sendRebootPrompt(chatID, msgID)

	case data == "reboot:confirm":
		_ = s.answerCallback(query.ID, "Rebooting router...", true)
		if query.Message != nil {
			_ = s.editMessage(chatID, msgID, "⏳ <b>Router reboot initiated...</b>", "HTML", nil)
		}
		go func() {
			time.Sleep(500 * time.Millisecond)
			if s.client != nil {
				_ = s.client.Reboot(context.Background())
			}
		}()

	case data == "reboot:cancel":
		_ = s.answerCallback(query.ID, "Cancelled", false)
		if query.Message != nil {
			_ = s.editMessage(chatID, msgID, "❌ <i>Router reboot cancelled.</i>", "HTML", nil)
		}

	case strings.HasPrefix(data, "router:select:"):
		rIDStr := strings.TrimPrefix(data, "router:select:")
		rID, _ := strconv.Atoi(rIDStr)
		if rID > 0 {
			_, _ = s.database.SqlDB.Exec("UPDATE routers SET is_default = 0")
			_, _ = s.database.SqlDB.Exec("UPDATE routers SET is_default = 1, is_active = 1 WHERE id = ?", rID)
			_ = s.answerCallback(query.ID, "Switched active router", true)
		}
		s.sendRouters(chatID, msgID)

	case strings.HasPrefix(data, "user:pause:"):
		uIDStr := strings.TrimPrefix(data, "user:pause:")
		uID, _ := strconv.Atoi(uIDStr)
		if u, _ := s.database.GetUser(uID); u != nil {
			u.IsPaused = true
			_ = s.database.UpdateUser(u)
			_ = s.answerCallback(query.ID, fmt.Sprintf("Paused %s", u.Name), false)
		}
		s.sendUsers(chatID, msgID)

	case strings.HasPrefix(data, "user:resume:"):
		uIDStr := strings.TrimPrefix(data, "user:resume:")
		uID, _ := strconv.Atoi(uIDStr)
		if u, _ := s.database.GetUser(uID); u != nil {
			u.IsPaused = false
			_ = s.database.UpdateUser(u)
			_ = s.answerCallback(query.ID, fmt.Sprintf("Resumed %s", u.Name), false)
		}
		s.sendUsers(chatID, msgID)

	case strings.HasPrefix(data, "user:limit:"):
		parts := strings.Split(data, ":")
		if len(parts) >= 4 {
			uID, _ := strconv.Atoi(parts[2])
			limit := parts[3]
			if u, _ := s.database.GetUser(uID); u != nil {
				u.SpeedLimit = limit
				_ = s.database.UpdateUser(u)
				_ = s.answerCallback(query.ID, fmt.Sprintf("Limit set to %s", limit), false)
			}
		}
		s.sendUsers(chatID, msgID)
	}
}

func (s *TelegramBotService) cmdStart(chatID int64) {
	text := "⚡ <b>MikroMan Companion Bot</b>\n\n" +
		"Manage your MikroTik routers, monitor live network metrics, inspect connected user profiles, and trigger instant controls.\n\n" +
		"Use the buttons below or commands:\n" +
		"• /status — Router health & metrics\n" +
		"• /users — User speeds & controls\n" +
		"• /routers — Switch active router\n" +
		"• /reboot — Reboot router"

	kb := InlineKeyboardMarkup{
		InlineKeyboard: [][]InlineKeyboardButton{
			{
				{Text: "📊 Status", CallbackData: "cmd:status"},
				{Text: "👥 Users", CallbackData: "cmd:users"},
			},
			{
				{Text: "🔀 Routers", CallbackData: "cmd:routers"},
				{Text: "⚠️ Reboot Router", CallbackData: "cmd:reboot_prompt"},
			},
		},
	}

	_ = s.SendMessage(chatID, text, "HTML", kb)
}

func (s *TelegramBotService) cmdHelp(chatID int64) {
	text := "📖 <b>Available Commands</b>\n\n" +
		"• /status — Realtime CPU, memory, uptime, temperature, and voltage\n" +
		"• /users — Connected user profiles, speeds, pause/resume, and bandwidth limits\n" +
		"• /routers — List configured routers and switch active router\n" +
		"• /reboot — Safely reboot the active MikroTik router with confirmation\n" +
		"• /help — Show this help message"

	_ = s.SendMessage(chatID, text, "HTML", nil)
}

func (s *TelegramBotService) sendStatus(chatID int64, msgID int) {
	client := s.client
	rtr, _ := s.database.GetDefaultRouter()
	if rtr == nil {
		routers, _ := s.database.GetRouters()
		if len(routers) > 0 {
			rtr = &routers[0]
		}
	}

	if client == nil {
		text := "⚠️ <i>No active router client configured. Connect a router in MikroMan Settings.</i>"
		if msgID > 0 {
			_ = s.editMessage(chatID, msgID, text, "HTML", nil)
		} else {
			_ = s.SendMessage(chatID, text, "HTML", nil)
		}
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	res, _ := client.GetSystemResource(ctx)
	health, _ := client.GetSystemHealth(ctx)
	temp, volt := routeros.ExtractHealthMetrics(health)

	routerName := "MikroTik"
	if rtr != nil {
		routerName = rtr.Name
	}

	board := "RouterOS"
	ver := "7.x"
	cpuStr := "0"
	uptime := "N/A"
	var freeMem, totMem int64
	if res != nil {
		if res.BoardName != "" {
			board = res.BoardName
		}
		if res.Version != "" {
			ver = res.Version
		}
		if res.CPULoad != "" {
			cpuStr = res.CPULoad
		}
		uptime = res.Uptime
		freeMem, _ = strconv.ParseInt(res.FreeMemory, 10, 64)
		totMem, _ = strconv.ParseInt(res.TotalMemory, 10, 64)
	}

	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("📊 <b>%s Status</b>\n\n", routerName))
	sb.WriteString(fmt.Sprintf("🏷 <b>Model:</b> <code>%s</code> (%s)\n", board, ver))
	sb.WriteString(fmt.Sprintf("⚙ <b>CPU Load:</b> <code>%s%%</code> | ⏱ <b>Uptime:</b> <code>%s</code>\n", cpuStr, uptime))
	sb.WriteString(fmt.Sprintf("💾 <b>Memory:</b> <code>%s free</code> / <code>%s</code>\n", formatBytes(freeMem), formatBytes(totMem)))

	if temp != nil {
		sb.WriteString(fmt.Sprintf("🌡 <b>Temperature:</b> <code>%.1f°C</code>\n", *temp))
	}
	if volt != nil {
		sb.WriteString(fmt.Sprintf("⚡ <b>Voltage:</b> <code>%.1f V</code>\n", *volt))
	}

	kb := InlineKeyboardMarkup{
		InlineKeyboard: [][]InlineKeyboardButton{
			{
				{Text: "🔄 Refresh", CallbackData: "cmd:status"},
				{Text: "👥 Users", CallbackData: "cmd:users"},
			},
			{
				{Text: "🔀 Routers", CallbackData: "cmd:routers"},
				{Text: "⚠️ Reboot", CallbackData: "cmd:reboot_prompt"},
			},
		},
	}

	if msgID > 0 {
		_ = s.editMessage(chatID, msgID, sb.String(), "HTML", kb)
	} else {
		_ = s.SendMessage(chatID, sb.String(), "HTML", kb)
	}
}

func (s *TelegramBotService) sendUsers(chatID int64, msgID int) {
	users, err := s.database.GetUsers(nil)
	if err != nil || len(users) == 0 {
		text := "👥 <b>Users</b>\n\n<i>No users created yet. Assign devices to users in Web UI.</i>"
		if msgID > 0 {
			_ = s.editMessage(chatID, msgID, text, "HTML", nil)
		} else {
			_ = s.SendMessage(chatID, text, "HTML", nil)
		}
		return
	}

	var sb strings.Builder
	sb.WriteString("👥 <b>User Profiles & Bandwidth</b>\n\n")

	var rows [][]InlineKeyboardButton
	for _, u := range users {
		status := "🟢 Active"
		if u.IsPaused {
			status = "⏸ Paused"
		}
		limit := u.SpeedLimit
		if limit == "" {
			limit = "unlimited"
		}

		sb.WriteString(fmt.Sprintf("• <b>%s</b> — %s\n", u.Name, status))
		sb.WriteString(fmt.Sprintf("  Devices: %d | Limit: <code>%s</code>\n", len(u.Devices), limit))

		// User action row
		if u.IsPaused {
			rows = append(rows, []InlineKeyboardButton{
				{Text: fmt.Sprintf("▶ Resume %s", u.Name), CallbackData: fmt.Sprintf("user:resume:%d", u.ID)},
			})
		} else {
			rows = append(rows, []InlineKeyboardButton{
				{Text: fmt.Sprintf("⏸ Pause %s", u.Name), CallbackData: fmt.Sprintf("user:pause:%d", u.ID)},
				{Text: "⚡ 20M", CallbackData: fmt.Sprintf("user:limit:%d:20M", u.ID)},
				{Text: "⚡ 50M", CallbackData: fmt.Sprintf("user:limit:%d:50M", u.ID)},
				{Text: "⚡ Max", CallbackData: fmt.Sprintf("user:limit:%d:unlimited", u.ID)},
			})
		}
	}

	rows = append(rows, []InlineKeyboardButton{
		{Text: "🔄 Refresh", CallbackData: "cmd:users"},
		{Text: "📊 Status", CallbackData: "cmd:status"},
	})

	kb := InlineKeyboardMarkup{InlineKeyboard: rows}

	if msgID > 0 {
		_ = s.editMessage(chatID, msgID, sb.String(), "HTML", kb)
	} else {
		_ = s.SendMessage(chatID, sb.String(), "HTML", kb)
	}
}

func (s *TelegramBotService) sendRouters(chatID int64, msgID int) {
	routers, _ := s.database.GetRouters()
	if len(routers) == 0 {
		text := "🔀 <b>Routers</b>\n\n<i>No routers configured. Add a router via Web UI.</i>"
		if msgID > 0 {
			_ = s.editMessage(chatID, msgID, text, "HTML", nil)
		} else {
			_ = s.SendMessage(chatID, text, "HTML", nil)
		}
		return
	}

	var sb strings.Builder
	sb.WriteString("🔀 <b>Configured MikroTik Routers</b>\n\n")

	var rows [][]InlineKeyboardButton
	for _, r := range routers {
		icon := "⚪"
		status := ""
		if r.IsDefault {
			icon = "🟢"
			status = " <i>(Active)</i>"
		}
		sb.WriteString(fmt.Sprintf("%s <b>%s</b> <code>%s:%d</code>%s\n", icon, r.Name, r.Host, r.Port, status))

		if !r.IsDefault {
			rows = append(rows, []InlineKeyboardButton{
				{Text: fmt.Sprintf("👉 Switch to %s", r.Name), CallbackData: fmt.Sprintf("router:select:%d", r.ID)},
			})
		}
	}

	rows = append(rows, []InlineKeyboardButton{
		{Text: "🔄 Refresh", CallbackData: "cmd:routers"},
		{Text: "📊 Status", CallbackData: "cmd:status"},
	})

	kb := InlineKeyboardMarkup{InlineKeyboard: rows}

	if msgID > 0 {
		_ = s.editMessage(chatID, msgID, sb.String(), "HTML", kb)
	} else {
		_ = s.SendMessage(chatID, sb.String(), "HTML", kb)
	}
}

func (s *TelegramBotService) sendRebootPrompt(chatID int64, msgID int) {
	text := "⚠️ <b>Reboot Confirmation</b>\n\nAre you sure you want to reboot the active MikroTik router?\nConnected clients will temporarily lose connection."
	kb := InlineKeyboardMarkup{
		InlineKeyboard: [][]InlineKeyboardButton{
			{
				{Text: "⚠️ Confirm Reboot", CallbackData: "reboot:confirm"},
				{Text: "❌ Cancel", CallbackData: "reboot:cancel"},
			},
		},
	}

	if msgID > 0 {
		_ = s.editMessage(chatID, msgID, text, "HTML", kb)
	} else {
		_ = s.SendMessage(chatID, text, "HTML", kb)
	}
}

func (s *TelegramBotService) SendAlertToAdmins(text string) {
	s.mu.Lock()
	token := s.token
	adminIDs := s.adminIDs
	s.mu.Unlock()

	if token == "" || len(adminIDs) == 0 {
		return
	}

	for _, chatID := range adminIDs {
		_ = s.SendMessage(chatID, text, "HTML", nil)
	}
}

func (s *TelegramBotService) SendMessage(chatID int64, text, parseMode string, replyMarkup interface{}) error {
	s.mu.Lock()
	token := s.token
	s.mu.Unlock()
	if token == "" {
		return fmt.Errorf("bot token not configured")
	}

	url := fmt.Sprintf("https://api.telegram.org/bot%s/sendMessage", token)
	payload := map[string]interface{}{
		"chat_id": chatID,
		"text":    text,
	}
	if parseMode != "" {
		payload["parse_mode"] = parseMode
	}
	if replyMarkup != nil {
		payload["reply_markup"] = replyMarkup
	}

	data, _ := json.Marshal(payload)
	resp, err := s.httpClient.Post(url, "application/json", bytes.NewReader(data))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.ReadAll(resp.Body)
	return nil
}

func (s *TelegramBotService) editMessage(chatID int64, messageID int, text, parseMode string, replyMarkup interface{}) error {
	s.mu.Lock()
	token := s.token
	s.mu.Unlock()
	if token == "" {
		return fmt.Errorf("bot token not configured")
	}

	url := fmt.Sprintf("https://api.telegram.org/bot%s/editMessageText", token)
	payload := map[string]interface{}{
		"chat_id":    chatID,
		"message_id": messageID,
		"text":       text,
	}
	if parseMode != "" {
		payload["parse_mode"] = parseMode
	}
	if replyMarkup != nil {
		payload["reply_markup"] = replyMarkup
	}

	data, _ := json.Marshal(payload)
	resp, err := s.httpClient.Post(url, "application/json", bytes.NewReader(data))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.ReadAll(resp.Body)
	return nil
}

func (s *TelegramBotService) answerCallback(queryID, text string, showAlert bool) error {
	s.mu.Lock()
	token := s.token
	s.mu.Unlock()
	if token == "" {
		return fmt.Errorf("bot token not configured")
	}

	url := fmt.Sprintf("https://api.telegram.org/bot%s/answerCallbackQuery", token)
	payload := map[string]interface{}{
		"callback_query_id": queryID,
		"text":              text,
		"show_alert":        showAlert,
	}

	data, _ := json.Marshal(payload)
	resp, err := s.httpClient.Post(url, "application/json", bytes.NewReader(data))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.ReadAll(resp.Body)
	return nil
}

func formatBytes(bytes int64) string {
	if bytes <= 0 {
		return "0 B"
	}
	const unit = 1024
	if bytes < unit {
		return fmt.Sprintf("%d B", bytes)
	}
	div, exp := int64(unit), 0
	for n := bytes / unit; n >= unit; n /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %cB", float64(bytes)/float64(div), "KMGTPE"[exp])
}
