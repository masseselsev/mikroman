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

// QueueReconciler triggers queue and firewall rule reconciliation in RouterOS.
type QueueReconciler interface {
	ReconcileQueues(ctx context.Context, routerID int) error
}

// telegramPollRun is one generation of the long-poll loop. It exists so that a
// loop owns the channel it watches: the loop captures its own stopCh instead of
// re-reading a service field that the next Start() replaces.
//
//   - stopCh is closed by Stop()/Reconfigure() to tell *this* loop to return;
//   - cancel aborts an in-flight getUpdates long poll, so the loop does not have
//     to sit out the 20 s Telegram poll (up to 35 s with the client timeout)
//     before it can notice that it was asked to stop;
//   - done is closed when the loop goroutine has actually returned, which lets
//     Stop() and Reconfigure() wait for the loop instead of hoping it went away;
//   - ctx carries the same cancellation into the other Telegram calls made by the
//     goroutine (fetchMe, deleteWebhook), so none of them can outlive the run.
type telegramPollRun struct {
	ctx    context.Context
	stopCh chan struct{}
	cancel context.CancelFunc
	done   chan struct{}
}

// newTelegramPollRun builds a fresh, fully independent poll run.
func newTelegramPollRun() *telegramPollRun {
	ctx, cancel := context.WithCancel(context.Background())
	return &telegramPollRun{
		ctx:    ctx,
		stopCh: make(chan struct{}),
		cancel: cancel,
		done:   make(chan struct{}),
	}
}

// TelegramBotService manages Telegram bot polling, commands, callbacks, and alerts.
type TelegramBotService struct {
	database            *db.DB
	client              *routeros.Client
	reconciler          QueueReconciler
	httpClient          *http.Client
	mu                  sync.Mutex
	running             bool
	poll                *telegramPollRun
	token               string
	adminIDs            []int64
	mode                string
	lang                string
	botName             string
	activeRouterPerChat map[int64]int
	clients             map[int]*routeros.Client
}

func NewTelegramBotService(database *db.DB, client *routeros.Client, reconciler QueueReconciler) *TelegramBotService {
	return &TelegramBotService{
		database:            database,
		client:              client,
		reconciler:          reconciler,
		httpClient:          &http.Client{Timeout: 35 * time.Second},
		activeRouterPerChat: make(map[int64]int),
		clients:             make(map[int]*routeros.Client),
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

	// Every start gets a fresh run, and the loop below receives it as an argument
	// rather than reading a service field: a loop can therefore only ever watch
	// the channel that belongs to its own run, so a later Start() cannot re-point
	// a still-running loop at a new channel.
	run := newTelegramPollRun()
	s.poll = run
	s.running = true

	go func() {
		defer close(run.done)
		defer run.cancel()

		botName, err := s.fetchMe(run.ctx)
		if err != nil {
			log.Printf("[TelegramBot] Error fetching bot info: %v", err)
		} else {
			s.mu.Lock()
			s.botName = botName
			s.mu.Unlock()
			log.Printf("[TelegramBot] Connected as @%s", botName)
		}

		_ = s.deleteWebhook(run.ctx)
		s.pollLoop(run)
	}()
}

// Stop stops the long-poll loop and returns only after the loop goroutine has
// actually exited, so "stopped" means stopped and not "asked to stop".
//
// It is idempotent: the running flag is flipped under s.mu, so only the caller
// that performs running -> stopped closes the run's channel, and a second Stop()
// (or a Stop() after Reconfigure()) is a no-op instead of a panic on closing an
// already closed channel.
func (s *TelegramBotService) Stop() {
	run := s.detachPollRun()
	if run == nil {
		return
	}

	<-run.done
	log.Printf("[TelegramBot] Stopped bot polling")
}

// detachPollRun takes the active poll run out of the service and signals it to
// stop, returning nil when the bot was not running. The caller owns the returned
// run and should wait on its done channel before assuming the loop is gone.
func (s *TelegramBotService) detachPollRun() *telegramPollRun {
	s.mu.Lock()
	defer s.mu.Unlock()

	if !s.running {
		return nil
	}
	s.running = false

	run := s.poll
	s.poll = nil
	if run != nil {
		close(run.stopCh)
		// Abort the in-flight getUpdates: closing stopCh alone would leave the
		// loop parked in the long poll until Telegram (or the 35 s client
		// timeout) answered it.
		run.cancel()
	}
	return run
}

// Reconfigure applies freshly saved settings by restarting the poll loop.
//
// Waiting inside Stop() is the whole point here: Start() installs a new run
// while the previous loop would otherwise still be alive, and Telegram serves a
// single getUpdates consumer per bot token. Two concurrent pollers overwrite each
// other's offset and answer each other with HTTP 409 "terminated by other
// getUpdates request", so the previous loop must be gone — goroutine and HTTP
// request included — before (or as) the new one starts. The old 500 ms sleep
// between stop and start is gone: it was a guess, and it did not hold while the
// loop was parked in a 20 s long poll.
func (s *TelegramBotService) Reconfigure() {
	s.Stop()
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

func (s *TelegramBotService) isRussian() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return strings.HasPrefix(strings.ToLower(s.lang), "ru")
}

func (s *TelegramBotService) tr(en, ru string) string {
	if s.isRussian() {
		return ru
	}
	return en
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

func (s *TelegramBotService) getActiveRouter(chatID int64) (*db.Router, error) {
	s.mu.Lock()
	rID, ok := s.activeRouterPerChat[chatID]
	s.mu.Unlock()

	if ok && rID > 0 {
		rtr, err := s.database.GetRouter(rID)
		if err == nil && rtr != nil {
			return rtr, nil
		}
	}

	def, err := s.database.GetDefaultRouter()
	if err == nil && def != nil {
		return def, nil
	}

	routers, err := s.database.GetRouters()
	if err == nil && len(routers) > 0 {
		return &routers[0], nil
	}

	return nil, fmt.Errorf("no routers configured")
}

func (s *TelegramBotService) setActiveRouter(chatID int64, routerID int) {
	s.mu.Lock()
	s.activeRouterPerChat[chatID] = routerID
	s.mu.Unlock()
}

func (s *TelegramBotService) getClientForRouter(routerID int) (*routeros.Client, error) {
	s.mu.Lock()
	if c, ok := s.clients[routerID]; ok && c != nil {
		s.mu.Unlock()
		return c, nil
	}
	s.mu.Unlock()

	router, err := s.database.GetRouter(routerID)
	if err != nil {
		return nil, err
	}
	if router == nil {
		return nil, fmt.Errorf("router %d not found", routerID)
	}

	if s.client != nil && s.client.Matches(router.Host, router.Port) {
		s.mu.Lock()
		s.clients[routerID] = s.client
		s.mu.Unlock()
		return s.client, nil
	}

	newClient, err := routeros.NewClient(routeros.Config{
		Host:      router.Host,
		Port:      router.Port,
		Username:  router.Username,
		Password:  router.Password,
		UseSSL:    router.UseSSL,
		SSLVerify: router.SSLVerify,
		CACert:    router.CACert.String,
		Timeout:   5 * time.Second,
	})
	if err != nil {
		return nil, err
	}

	s.mu.Lock()
	s.clients[routerID] = newClient
	s.mu.Unlock()
	return newClient, nil
}

func (s *TelegramBotService) fetchMe(ctx context.Context) (string, error) {
	s.mu.Lock()
	token := s.token
	s.mu.Unlock()

	url := fmt.Sprintf("https://api.telegram.org/bot%s/getMe", token)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return "", err
	}
	resp, err := s.httpClient.Do(req)
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

func (s *TelegramBotService) deleteWebhook(ctx context.Context) error {
	s.mu.Lock()
	token := s.token
	s.mu.Unlock()

	url := fmt.Sprintf("https://api.telegram.org/bot%s/deleteWebhook?drop_pending_updates=false", token)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	resp, err := s.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return nil
}

// pauseUnlessStopped waits for d and reports true when the wait elapsed normally.
// It returns false as soon as the run is asked to stop, so a backoff sleep never
// delays a Stop() or Reconfigure() past the current scheduling slot.
func pauseUnlessStopped(stopCh <-chan struct{}, d time.Duration) bool {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-stopCh:
		return false
	case <-timer.C:
		return true
	}
}

// pollLoop is the long-poll supervisor for one run. It receives the run as an
// argument on purpose: a loop must capture the channel that belongs to it, since
// a loop that re-read a service field would silently follow the channel installed
// by the *next* Start() and never see its own close. That stranded loop keeps
// polling next to its successor, which is what produces the 409 conflicts.
func (s *TelegramBotService) pollLoop(run *telegramPollRun) {
	log.Printf("[TelegramBot] Starting long polling loop...")
	var offset int64 = 0

	for {
		select {
		case <-run.stopCh:
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

		// The request is bound to the run's context, so Stop()/Reconfigure() can
		// abort a 20 s long poll in flight instead of waiting it out.
		req, err := http.NewRequestWithContext(run.ctx, http.MethodPost, url, bytes.NewReader(data))
		if err != nil {
			if !pauseUnlessStopped(run.stopCh, 2*time.Second) {
				return
			}
			continue
		}
		req.Header.Set("Content-Type", "application/json")

		resp, err := s.httpClient.Do(req)
		if err != nil {
			select {
			case <-run.stopCh:
				return
			case <-time.After(2 * time.Second):
				continue
			}
		}

		if resp.StatusCode == 409 {
			log.Printf("[TelegramBot] Polling conflict 409, waiting 5s...")
			resp.Body.Close()
			select {
			case <-run.stopCh:
				return
			case <-time.After(5 * time.Second):
				continue
			}
		}

		if resp.StatusCode != http.StatusOK {
			resp.Body.Close()
			if !pauseUnlessStopped(run.stopCh, 2*time.Second) {
				return
			}
			continue
		}

		var updateRes struct {
			Ok     bool             `json:"ok"`
			Result []TelegramUpdate `json:"result"`
		}
		decodeErr := json.NewDecoder(resp.Body).Decode(&updateRes)
		resp.Body.Close()

		if decodeErr != nil || !updateRes.Ok {
			if !pauseUnlessStopped(run.stopCh, 2*time.Second) {
				return
			}
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
		case "start", "menu":
			s.sendMainMenu(msg.Chat.ID, 0)
		case "help":
			s.cmdHelp(msg.Chat.ID)
		case "status":
			s.sendStatus(msg.Chat.ID, 0)
		case "users":
			s.sendUsers(msg.Chat.ID, 0)
		case "devices":
			s.sendDevices(msg.Chat.ID, 0)
		case "routers":
			s.sendRouters(msg.Chat.ID, 0)
		case "reboot":
			s.sendRebootPrompt(msg.Chat.ID, 0)
		default:
			_ = s.SendMessage(msg.Chat.ID, s.tr("Unknown command. Type /help to view available commands.", "Неизвестная команда. Введите /help для справки."), "", nil)
		}
	}
}

func (s *TelegramBotService) handleCallbackQuery(query *TelegramCallbackQuery) {
	if !s.isAuthorized(query.From.ID) {
		_ = s.answerCallback(query.ID, s.tr("Access denied", "Доступ запрещен"), true)
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
	case data == "menu:main":
		_ = s.answerCallback(query.ID, "", false)
		s.sendMainMenu(chatID, msgID)

	case data == "cmd:status":
		_ = s.answerCallback(query.ID, "", false)
		s.sendStatus(chatID, msgID)

	case data == "cmd:users":
		_ = s.answerCallback(query.ID, "", false)
		s.sendUsers(chatID, msgID)

	case strings.HasPrefix(data, "user:view:"):
		_ = s.answerCallback(query.ID, "", false)
		uIDStr := strings.TrimPrefix(data, "user:view:")
		uID, _ := strconv.Atoi(uIDStr)
		s.sendUserDetail(chatID, msgID, uID)

	case data == "cmd:devices":
		_ = s.answerCallback(query.ID, "", false)
		s.sendDevices(chatID, msgID)

	case data == "cmd:routers":
		_ = s.answerCallback(query.ID, "", false)
		s.sendRouters(chatID, msgID)

	case data == "cmd:reboot_prompt":
		_ = s.answerCallback(query.ID, "", false)
		s.sendRebootPrompt(chatID, msgID)

	case data == "reboot:confirm":
		rtr, _ := s.getActiveRouter(chatID)
		rtrName := "router"
		if rtr != nil {
			rtrName = rtr.Name
		}
		_ = s.answerCallback(query.ID, s.tr("Rebooting router...", "Перезагрузка роутера..."), true)
		kb := InlineKeyboardMarkup{
			InlineKeyboard: [][]InlineKeyboardButton{
				{{Text: s.tr("🏠 Main Menu", "🏠 Главное меню"), CallbackData: "menu:main"}},
			},
		}
		msg := fmt.Sprintf(s.tr("⏳ <b>Reboot initiated for %s...</b>", "⏳ <b>Инициирована перезагрузка %s...</b>"), rtrName)
		_ = s.sendOrEdit(chatID, msgID, msg, kb)
		if rtr != nil {
			go func(rID int) {
				time.Sleep(500 * time.Millisecond)
				client, err := s.getClientForRouter(rID)
				if err == nil && client != nil {
					_ = client.Reboot(context.Background())
				}
			}(rtr.ID)
		}

	case data == "reboot:cancel":
		_ = s.answerCallback(query.ID, s.tr("Cancelled", "Отменено"), false)
		s.sendMainMenu(chatID, msgID)

	case strings.HasPrefix(data, "router:select:"):
		rIDStr := strings.TrimPrefix(data, "router:select:")
		rID, _ := strconv.Atoi(rIDStr)
		if rID > 0 {
			s.setActiveRouter(chatID, rID)
			_, _ = s.database.SqlDB.Exec("UPDATE routers SET is_default = 0")
			_, _ = s.database.SqlDB.Exec("UPDATE routers SET is_default = 1, is_active = 1 WHERE id = ?", rID)
			_ = s.answerCallback(query.ID, s.tr("Switched active router", "Активный роутер переключен"), false)
		}
		s.sendRouters(chatID, msgID)

	case strings.HasPrefix(data, "user:pause:"):
		uIDStr := strings.TrimPrefix(data, "user:pause:")
		uID, _ := strconv.Atoi(uIDStr)
		if u, _ := s.database.GetUser(uID); u != nil {
			u.IsPaused = true
			_ = s.database.UpdateUser(u)
			if s.reconciler != nil && u.RouterID != nil {
				go func(rID int) {
					ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
					defer cancel()
					_ = s.reconciler.ReconcileQueues(ctx, rID)
				}(*u.RouterID)
			}
			_ = s.answerCallback(query.ID, fmt.Sprintf(s.tr("Paused %s", "Приостановлен %s"), u.Name), false)
			s.sendUserDetail(chatID, msgID, uID)
		} else {
			s.sendUsers(chatID, msgID)
		}

	case strings.HasPrefix(data, "user:resume:"):
		uIDStr := strings.TrimPrefix(data, "user:resume:")
		uID, _ := strconv.Atoi(uIDStr)
		if u, _ := s.database.GetUser(uID); u != nil {
			u.IsPaused = false
			_ = s.database.UpdateUser(u)
			if s.reconciler != nil && u.RouterID != nil {
				go func(rID int) {
					ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
					defer cancel()
					_ = s.reconciler.ReconcileQueues(ctx, rID)
				}(*u.RouterID)
			}
			_ = s.answerCallback(query.ID, fmt.Sprintf(s.tr("Resumed %s", "Возобновлен %s"), u.Name), false)
			s.sendUserDetail(chatID, msgID, uID)
		} else {
			s.sendUsers(chatID, msgID)
		}

	case strings.HasPrefix(data, "user:limit:"):
		parts := strings.Split(data, ":")
		if len(parts) >= 4 {
			uID, _ := strconv.Atoi(parts[2])
			limit := parts[3]
			if limit == "unlimited" {
				limit = ""
			}
			if u, _ := s.database.GetUser(uID); u != nil {
				u.SpeedLimit = limit
				_ = s.database.UpdateUser(u)
				if s.reconciler != nil && u.RouterID != nil {
					go func(rID int) {
						ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
						defer cancel()
						_ = s.reconciler.ReconcileQueues(ctx, rID)
					}(*u.RouterID)
				}
				dispLimit := limit
				if dispLimit == "" {
					dispLimit = s.tr("unlimited", "безлимит")
				}
				_ = s.answerCallback(query.ID, fmt.Sprintf(s.tr("Limit set to %s", "Лимит установлен: %s"), dispLimit), false)
				s.sendUserDetail(chatID, msgID, uID)
			} else {
				s.sendUsers(chatID, msgID)
			}
		}
	}
}

func (s *TelegramBotService) sendMainMenu(chatID int64, msgID int) {
	isRU := s.isRussian()
	rtr, _ := s.getActiveRouter(chatID)

	var sb strings.Builder
	if isRU {
		sb.WriteString("⚡ <b>MikroMan Бот-помощник</b>\n\n")
		if rtr != nil {
			sb.WriteString(fmt.Sprintf("🌐 <b>Активный роутер:</b> %s (<code>%s</code>)\n\n", rtr.Name, rtr.Host))
		} else {
			sb.WriteString("⚠️ <i>Роутер не настроен. Добавьте его в панели MikroMan.</i>\n\n")
		}
		sb.WriteString("Выберите раздел меню:")
	} else {
		sb.WriteString("⚡ <b>MikroMan Companion Bot</b>\n\n")
		if rtr != nil {
			sb.WriteString(fmt.Sprintf("🌐 <b>Active Router:</b> %s (<code>%s</code>)\n\n", rtr.Name, rtr.Host))
		} else {
			sb.WriteString("⚠️ <i>No router configured. Add one in MikroMan Settings.</i>\n\n")
		}
		sb.WriteString("Select an option below:")
	}

	kb := InlineKeyboardMarkup{
		InlineKeyboard: [][]InlineKeyboardButton{
			{
				{Text: s.tr("📊 Status", "📊 Статус"), CallbackData: "cmd:status"},
				{Text: s.tr("👥 Users", "👥 Пользователи"), CallbackData: "cmd:users"},
			},
			{
				{Text: s.tr("📱 Devices", "📱 Устройства"), CallbackData: "cmd:devices"},
				{Text: s.tr("🔀 Routers", "🔀 Роутеры"), CallbackData: "cmd:routers"},
			},
			{
				{Text: s.tr("⚠️ Reboot", "⚠️ Перезагрузка"), CallbackData: "cmd:reboot_prompt"},
				{Text: s.tr("🔄 Refresh", "🔄 Обновить"), CallbackData: "menu:main"},
			},
		},
	}

	s.sendOrEdit(chatID, msgID, sb.String(), kb)
}

func (s *TelegramBotService) cmdStart(chatID int64) {
	s.sendMainMenu(chatID, 0)
}

func (s *TelegramBotService) cmdHelp(chatID int64) {
	isRU := s.isRussian()
	var text string
	if isRU {
		text = "📖 <b>Команды MikroMan Bot</b>\n\n" +
			"• /menu или /start — Главное интерактивное меню\n" +
			"• /status — Мониторинг CPU, памяти, аптайма и температуры\n" +
			"• /users — Пользователи, скорости, пауза и лимиты\n" +
			"• /devices — Список подключенных устройств\n" +
			"• /routers — Переключение активного роутера\n" +
			"• /reboot — Безопасная перезагрузка роутера\n" +
			"• /help — Справка по командам"
	} else {
		text = "📖 <b>MikroMan Bot Commands</b>\n\n" +
			"• /menu or /start — Main interactive menu\n" +
			"• /status — Realtime CPU, memory, uptime, temp, and voltage\n" +
			"• /users — User profiles, speeds, pause/resume, and limits\n" +
			"• /devices — Connected and unassigned devices\n" +
			"• /routers — List and switch active router\n" +
			"• /reboot — Safely reboot active MikroTik router\n" +
			"• /help — Show this help message"
	}

	kb := InlineKeyboardMarkup{
		InlineKeyboard: [][]InlineKeyboardButton{
			{{Text: s.tr("🏠 Main Menu", "🏠 Главное меню"), CallbackData: "menu:main"}},
		},
	}
	_ = s.SendMessage(chatID, text, "HTML", kb)
}

func (s *TelegramBotService) sendStatus(chatID int64, msgID int) {
	isRU := s.isRussian()
	rtr, _ := s.getActiveRouter(chatID)
	if rtr == nil {
		text := s.tr("⚠️ <i>No active router configured. Connect a router in MikroMan Settings.</i>",
			"⚠️ <i>Роутер не настроен. Добавьте его в настройках MikroMan.</i>")
		kb := InlineKeyboardMarkup{
			InlineKeyboard: [][]InlineKeyboardButton{
				{{Text: s.tr("🏠 Main Menu", "🏠 Главное меню"), CallbackData: "menu:main"}},
			},
		}
		s.sendOrEdit(chatID, msgID, text, kb)
		return
	}

	client, err := s.getClientForRouter(rtr.ID)
	if err != nil || client == nil {
		text := fmt.Sprintf(
			s.tr("⚠️ <i>Failed to connect to router %s (%s).</i>", "⚠️ <i>Не удалось подключиться к роутеру %s (%s).</i>"),
			rtr.Name, rtr.Host,
		)
		kb := InlineKeyboardMarkup{
			InlineKeyboard: [][]InlineKeyboardButton{
				{{Text: s.tr("🔄 Refresh", "🔄 Обновить"), CallbackData: "cmd:status"}},
				{{Text: s.tr("🏠 Main Menu", "🏠 Главное меню"), CallbackData: "menu:main"}},
			},
		}
		s.sendOrEdit(chatID, msgID, text, kb)
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	res, _ := client.GetSystemResource(ctx)
	health, _ := client.GetSystemHealth(ctx)
	temp, volt := routeros.ExtractHealthMetrics(health)

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
	if isRU {
		sb.WriteString(fmt.Sprintf("📊 <b>Статус: %s</b>\n\n", rtr.Name))
		sb.WriteString(fmt.Sprintf("🏷 <b>Модель:</b> <code>%s</code> (%s)\n", board, ver))
		sb.WriteString(fmt.Sprintf("⚙ <b>Нагрузка CPU:</b> <code>%s%%</code> | ⏱ <b>Аптайм:</b> <code>%s</code>\n", cpuStr, uptime))
		sb.WriteString(fmt.Sprintf("💾 <b>Память:</b> <code>%s свободно</code> / <code>%s</code>\n", formatBytes(freeMem), formatBytes(totMem)))
		if temp != nil {
			sb.WriteString(fmt.Sprintf("🌡 <b>Температура:</b> <code>%.1f°C</code>\n", *temp))
		}
		if volt != nil {
			sb.WriteString(fmt.Sprintf("⚡ <b>Напряжение:</b> <code>%.1f V</code>\n", *volt))
		}
	} else {
		sb.WriteString(fmt.Sprintf("📊 <b>%s Status</b>\n\n", rtr.Name))
		sb.WriteString(fmt.Sprintf("🏷 <b>Model:</b> <code>%s</code> (%s)\n", board, ver))
		sb.WriteString(fmt.Sprintf("⚙ <b>CPU Load:</b> <code>%s%%</code> | ⏱ <b>Uptime:</b> <code>%s</code>\n", cpuStr, uptime))
		sb.WriteString(fmt.Sprintf("💾 <b>Memory:</b> <code>%s free</code> / <code>%s</code>\n", formatBytes(freeMem), formatBytes(totMem)))
		if temp != nil {
			sb.WriteString(fmt.Sprintf("🌡 <b>Temperature:</b> <code>%.1f°C</code>\n", *temp))
		}
		if volt != nil {
			sb.WriteString(fmt.Sprintf("⚡ <b>Voltage:</b> <code>%.1f V</code>\n", *volt))
		}
	}

	kb := InlineKeyboardMarkup{
		InlineKeyboard: [][]InlineKeyboardButton{
			{
				{Text: s.tr("🔄 Refresh", "🔄 Обновить"), CallbackData: "cmd:status"},
				{Text: s.tr("👥 Users", "👥 Пользователи"), CallbackData: "cmd:users"},
			},
			{
				{Text: s.tr("📱 Devices", "📱 Устройства"), CallbackData: "cmd:devices"},
				{Text: s.tr("🔀 Routers", "🔀 Роутеры"), CallbackData: "cmd:routers"},
			},
			{
				{Text: s.tr("🏠 Main Menu", "🏠 Главное меню"), CallbackData: "menu:main"},
			},
		},
	}

	s.sendOrEdit(chatID, msgID, sb.String(), kb)
}

func (s *TelegramBotService) sendUsers(chatID int64, msgID int) {
	isRU := s.isRussian()
	rtr, _ := s.getActiveRouter(chatID)
	if rtr == nil {
		text := s.tr("⚠️ <i>No active router configured.</i>", "⚠️ <i>Роутер не настроен.</i>")
		kb := InlineKeyboardMarkup{
			InlineKeyboard: [][]InlineKeyboardButton{
				{{Text: s.tr("🏠 Main Menu", "🏠 Главное меню"), CallbackData: "menu:main"}},
			},
		}
		s.sendOrEdit(chatID, msgID, text, kb)
		return
	}

	users, err := s.database.GetUsers(&rtr.ID)
	allDevices, _ := s.database.GetDevices(&rtr.ID)

	devMap := make(map[int][]db.Device)
	for _, dev := range allDevices {
		if dev.UserID != nil {
			devMap[*dev.UserID] = append(devMap[*dev.UserID], dev)
		}
	}
	for i := range users {
		users[i].Devices = devMap[users[i].ID]
	}

	if err != nil || len(users) == 0 {
		text := fmt.Sprintf(
			s.tr("👥 <b>Users (%s)</b>\n\n<i>No user profiles created for this router yet. Assign devices to users in Web UI.</i>",
				"👥 <b>Пользователи (%s)</b>\n\n<i>Для этого роутера пользователи ещё не созданы. Привяжите устройства в Web UI.</i>"),
			rtr.Name,
		)
		kb := InlineKeyboardMarkup{
			InlineKeyboard: [][]InlineKeyboardButton{
				{{Text: s.tr("📱 Devices", "📱 Устройства"), CallbackData: "cmd:devices"}},
				{{Text: s.tr("🏠 Main Menu", "🏠 Главное меню"), CallbackData: "menu:main"}},
			},
		}
		s.sendOrEdit(chatID, msgID, text, kb)
		return
	}

	var sb strings.Builder
	if isRU {
		sb.WriteString(fmt.Sprintf("👥 <b>Профили пользователей — %s</b>\n\n", rtr.Name))
	} else {
		sb.WriteString(fmt.Sprintf("👥 <b>User Profiles & Bandwidth — %s</b>\n\n", rtr.Name))
	}

	var userButtons []InlineKeyboardButton
	for _, u := range users {
		statusIcon := "🟢"
		statusText := s.tr("Active", "Активен")
		if u.IsPaused {
			statusIcon = "⏸"
			statusText = s.tr("Paused", "На паузе")
		}

		limit := u.SpeedLimit
		if limit == "" {
			limit = s.tr("unlimited", "безлимит")
		}

		onlineCount := 0
		for _, d := range u.Devices {
			if d.IsActive {
				onlineCount++
			}
		}

		if isRU {
			sb.WriteString(fmt.Sprintf("• <b>%s</b> — %s %s\n", u.Name, statusIcon, statusText))
			sb.WriteString(fmt.Sprintf("  Устройств: <b>%d</b> (онлайн: %d) | Лимит: <code>%s</code>\n", len(u.Devices), onlineCount, limit))
		} else {
			sb.WriteString(fmt.Sprintf("• <b>%s</b> — %s %s\n", u.Name, statusIcon, statusText))
			sb.WriteString(fmt.Sprintf("  Devices: <b>%d</b> (online: %d) | Limit: <code>%s</code>\n", len(u.Devices), onlineCount, limit))
		}

		userButtons = append(userButtons, InlineKeyboardButton{
			Text:         fmt.Sprintf("👤 %s (%d)", u.Name, len(u.Devices)),
			CallbackData: fmt.Sprintf("user:view:%d", u.ID),
		})
	}

	var rows [][]InlineKeyboardButton
	for i := 0; i < len(userButtons); i += 2 {
		end := i + 2
		if end > len(userButtons) {
			end = len(userButtons)
		}
		rows = append(rows, userButtons[i:end])
	}

	rows = append(rows, []InlineKeyboardButton{
		{Text: s.tr("📱 Devices", "📱 Устройства"), CallbackData: "cmd:devices"},
		{Text: s.tr("🔄 Refresh", "🔄 Обновить"), CallbackData: "cmd:users"},
	})
	rows = append(rows, []InlineKeyboardButton{
		{Text: s.tr("🏠 Main Menu", "🏠 Главное меню"), CallbackData: "menu:main"},
	})

	kb := InlineKeyboardMarkup{InlineKeyboard: rows}
	s.sendOrEdit(chatID, msgID, sb.String(), kb)
}

func (s *TelegramBotService) sendUserDetail(chatID int64, msgID int, userID int) {
	u, err := s.database.GetUser(userID)
	if err != nil || u == nil {
		s.sendUsers(chatID, msgID)
		return
	}

	rtrName := "Router"
	if u.RouterID != nil {
		if rtr, _ := s.database.GetRouter(*u.RouterID); rtr != nil {
			rtrName = rtr.Name
		}
	}

	allDevices, _ := s.database.GetDevices(u.RouterID)
	var userDevices []db.Device
	for _, dev := range allDevices {
		if dev.UserID != nil && *dev.UserID == u.ID {
			userDevices = append(userDevices, dev)
		}
	}

	isRU := s.isRussian()
	statusIcon := "🟢"
	statusText := s.tr("Active", "Активен")
	if u.IsPaused {
		statusIcon = "⏸"
		statusText = s.tr("Paused", "На паузе")
	}
	limit := u.SpeedLimit
	if limit == "" {
		limit = s.tr("unlimited", "безлимит")
	}

	var sb strings.Builder
	if isRU {
		sb.WriteString(fmt.Sprintf("👤 <b>Пользователь: %s</b>\n", u.Name))
		sb.WriteString(fmt.Sprintf("🌐 <b>Роутер:</b> %s\n", rtrName))
		sb.WriteString(fmt.Sprintf("⚙ <b>Статус:</b> %s %s\n", statusIcon, statusText))
		sb.WriteString(fmt.Sprintf("⚡ <b>Ограничение скорости:</b> <code>%s</code>\n\n", limit))
		sb.WriteString(fmt.Sprintf("📱 <b>Привязанные устройства (%d):</b>\n", len(userDevices)))
	} else {
		sb.WriteString(fmt.Sprintf("👤 <b>User: %s</b>\n", u.Name))
		sb.WriteString(fmt.Sprintf("🌐 <b>Router:</b> %s\n", rtrName))
		sb.WriteString(fmt.Sprintf("⚙ <b>Status:</b> %s %s\n", statusIcon, statusText))
		sb.WriteString(fmt.Sprintf("⚡ <b>Speed Limit:</b> <code>%s</code>\n\n", limit))
		sb.WriteString(fmt.Sprintf("📱 <b>Assigned Devices (%d):</b>\n", len(userDevices)))
	}

	if len(userDevices) == 0 {
		if isRU {
			sb.WriteString("<i>Нет привязанных устройств</i>\n")
		} else {
			sb.WriteString("<i>No devices assigned yet</i>\n")
		}
	} else {
		for _, d := range userDevices {
			dName := getDeviceDisplayName(d)
			dIP := getDeviceIP(d)
			onlineIcon := "⚪"
			onlineText := s.tr("Offline", "Не в сети")
			if d.IsActive {
				onlineIcon = "🟢"
				onlineText = s.tr("Online", "В сети")
			}
			sb.WriteString(fmt.Sprintf("• %s <b>%s</b> (<code>%s</code>) — %s\n", onlineIcon, dName, dIP, onlineText))
		}
	}

	var rows [][]InlineKeyboardButton
	if u.IsPaused {
		rows = append(rows, []InlineKeyboardButton{
			{Text: s.tr("▶ Resume Access", "▶ Возобновить доступ"), CallbackData: fmt.Sprintf("user:resume:%d", u.ID)},
		})
	} else {
		rows = append(rows, []InlineKeyboardButton{
			{Text: s.tr("⏸ Pause Access", "⏸ Поставить на паузу"), CallbackData: fmt.Sprintf("user:pause:%d", u.ID)},
		})
	}

	rows = append(rows, []InlineKeyboardButton{
		{Text: "⚡ 10M", CallbackData: fmt.Sprintf("user:limit:%d:10M", u.ID)},
		{Text: "⚡ 20M", CallbackData: fmt.Sprintf("user:limit:%d:20M", u.ID)},
		{Text: "⚡ 50M", CallbackData: fmt.Sprintf("user:limit:%d:50M", u.ID)},
		{Text: s.tr("⚡ Max", "⚡ Безлимит"), CallbackData: fmt.Sprintf("user:limit:%d:unlimited", u.ID)},
	})

	rows = append(rows, []InlineKeyboardButton{
		{Text: s.tr("🔙 Back to Users", "🔙 К пользователям"), CallbackData: "cmd:users"},
		{Text: s.tr("🏠 Main Menu", "🏠 Главное меню"), CallbackData: "menu:main"},
	})

	kb := InlineKeyboardMarkup{InlineKeyboard: rows}
	s.sendOrEdit(chatID, msgID, sb.String(), kb)
}

func (s *TelegramBotService) sendDevices(chatID int64, msgID int) {
	isRU := s.isRussian()
	rtr, _ := s.getActiveRouter(chatID)
	if rtr == nil {
		text := s.tr("⚠️ <i>No active router configured.</i>", "⚠️ <i>Роутер не настроен.</i>")
		kb := InlineKeyboardMarkup{
			InlineKeyboard: [][]InlineKeyboardButton{
				{{Text: s.tr("🏠 Main Menu", "🏠 Главное меню"), CallbackData: "menu:main"}},
			},
		}
		s.sendOrEdit(chatID, msgID, text, kb)
		return
	}

	allDevices, _ := s.database.GetDevices(&rtr.ID)
	users, _ := s.database.GetUsers(&rtr.ID)
	userNames := make(map[int]string)
	for _, u := range users {
		userNames[u.ID] = u.Name
	}

	activeCount := 0
	var unassigned []db.Device
	var assigned []db.Device
	for _, dev := range allDevices {
		if dev.IsActive {
			activeCount++
		}
		if dev.UserID == nil {
			unassigned = append(unassigned, dev)
		} else {
			assigned = append(assigned, dev)
		}
	}

	var sb strings.Builder
	if isRU {
		sb.WriteString(fmt.Sprintf("📱 <b>Устройства — %s</b>\n", rtr.Name))
		sb.WriteString(fmt.Sprintf("Всего устройств: <b>%d</b> | В сети: <b>%d</b>\n\n", len(allDevices), activeCount))
	} else {
		sb.WriteString(fmt.Sprintf("📱 <b>Devices — %s</b>\n", rtr.Name))
		sb.WriteString(fmt.Sprintf("Total Devices: <b>%d</b> | Online: <b>%d</b>\n\n", len(allDevices), activeCount))
	}

	if len(unassigned) > 0 {
		if isRU {
			sb.WriteString(fmt.Sprintf("❓ <b>Непривяз. устройства (%d):</b>\n", len(unassigned)))
		} else {
			sb.WriteString(fmt.Sprintf("❓ <b>Unassigned Devices (%d):</b>\n", len(unassigned)))
		}
		limitDisplay := 10
		for i, d := range unassigned {
			if i >= limitDisplay {
				rem := len(unassigned) - limitDisplay
				if isRU {
					sb.WriteString(fmt.Sprintf("<i>...и ещё %d устр.</i>\n", rem))
				} else {
					sb.WriteString(fmt.Sprintf("<i>...and %d more</i>\n", rem))
				}
				break
			}
			name := getDeviceDisplayName(d)
			dIP := getDeviceIP(d)
			icon := "⚪"
			if d.IsActive {
				icon = "🟢"
			}
			sb.WriteString(fmt.Sprintf("• %s %s (<code>%s</code>)\n", icon, name, dIP))
		}
		sb.WriteString("\n")
	}

	if len(assigned) > 0 {
		if isRU {
			sb.WriteString(fmt.Sprintf("👤 <b>Привязанные устройства (%d):</b>\n", len(assigned)))
		} else {
			sb.WriteString(fmt.Sprintf("👤 <b>Assigned Devices (%d):</b>\n", len(assigned)))
		}
		limitDisplay := 12
		for i, d := range assigned {
			if i >= limitDisplay {
				rem := len(assigned) - limitDisplay
				if isRU {
					sb.WriteString(fmt.Sprintf("<i>...и ещё %d устр.</i>\n", rem))
				} else {
					sb.WriteString(fmt.Sprintf("<i>...and %d more</i>\n", rem))
				}
				break
			}
			name := getDeviceDisplayName(d)
			dIP := getDeviceIP(d)
			owner := userNames[*d.UserID]
			icon := "⚪"
			if d.IsActive {
				icon = "🟢"
			}
			sb.WriteString(fmt.Sprintf("• %s %s → <b>%s</b> (<code>%s</code>)\n", icon, name, owner, dIP))
		}
	}

	kb := InlineKeyboardMarkup{
		InlineKeyboard: [][]InlineKeyboardButton{
			{
				{Text: s.tr("👥 Users", "👥 Пользователи"), CallbackData: "cmd:users"},
				{Text: s.tr("🔄 Refresh", "🔄 Обновить"), CallbackData: "cmd:devices"},
			},
			{
				{Text: s.tr("🏠 Main Menu", "🏠 Главное меню"), CallbackData: "menu:main"},
			},
		},
	}

	s.sendOrEdit(chatID, msgID, sb.String(), kb)
}

func (s *TelegramBotService) sendRouters(chatID int64, msgID int) {
	isRU := s.isRussian()
	routers, _ := s.database.GetRouters()
	if len(routers) == 0 {
		text := s.tr("🔀 <b>Routers</b>\n\n<i>No routers configured. Add a router in Web UI.</i>",
			"🔀 <b>Роутеры</b>\n\n<i>Роутеры не настроены. Добавьте роутер через Web UI.</i>")
		kb := InlineKeyboardMarkup{
			InlineKeyboard: [][]InlineKeyboardButton{
				{{Text: s.tr("🏠 Main Menu", "🏠 Главное меню"), CallbackData: "menu:main"}},
			},
		}
		s.sendOrEdit(chatID, msgID, text, kb)
		return
	}

	activeRtr, _ := s.getActiveRouter(chatID)

	var sb strings.Builder
	if isRU {
		sb.WriteString("🔀 <b>Настроенные роутеры MikroTik</b>\n\n")
	} else {
		sb.WriteString("🔀 <b>Configured MikroTik Routers</b>\n\n")
	}

	var rows [][]InlineKeyboardButton
	for _, r := range routers {
		icon := "⚪"
		status := ""
		isActive := activeRtr != nil && activeRtr.ID == r.ID
		if isActive {
			icon = "🟢"
			if isRU {
				status = " <i>(Активный)</i>"
			} else {
				status = " <i>(Active)</i>"
			}
		}
		sb.WriteString(fmt.Sprintf("%s <b>%s</b> <code>%s:%d</code>%s\n", icon, r.Name, r.Host, r.Port, status))

		if !isActive {
			rows = append(rows, []InlineKeyboardButton{
				{
					Text:         fmt.Sprintf("%s %s", s.tr("👉 Switch to", "👉 Выбрать"), r.Name),
					CallbackData: fmt.Sprintf("router:select:%d", r.ID),
				},
			})
		}
	}

	rows = append(rows, []InlineKeyboardButton{
		{Text: s.tr("🔄 Refresh", "🔄 Обновить"), CallbackData: "cmd:routers"},
		{Text: s.tr("🏠 Main Menu", "🏠 Главное меню"), CallbackData: "menu:main"},
	})

	kb := InlineKeyboardMarkup{InlineKeyboard: rows}
	s.sendOrEdit(chatID, msgID, sb.String(), kb)
}

func (s *TelegramBotService) sendRebootPrompt(chatID int64, msgID int) {
	isRU := s.isRussian()
	rtr, _ := s.getActiveRouter(chatID)
	rtrName := "MikroTik"
	if rtr != nil {
		rtrName = rtr.Name
	}

	var text string
	if isRU {
		text = fmt.Sprintf("⚠️ <b>Подтверждение перезагрузки</b>\n\nВы действительно хотите перезагрузить роутер <b>%s</b>?\nПодключенные устройства временно потеряют соединение с интернетом.", rtrName)
	} else {
		text = fmt.Sprintf("⚠️ <b>Reboot Confirmation</b>\n\nAre you sure you want to reboot <b>%s</b>?\nConnected clients will temporarily lose network connection.", rtrName)
	}

	kb := InlineKeyboardMarkup{
		InlineKeyboard: [][]InlineKeyboardButton{
			{
				{Text: s.tr("⚠️ Confirm Reboot", "⚠️ Перезагрузить"), CallbackData: "reboot:confirm"},
				{Text: s.tr("❌ Cancel", "❌ Отмена"), CallbackData: "reboot:cancel"},
			},
		},
	}

	s.sendOrEdit(chatID, msgID, text, kb)
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

func (s *TelegramBotService) sendOrEdit(chatID int64, msgID int, text string, kb interface{}) error {
	if msgID > 0 {
		return s.editMessage(chatID, msgID, text, "HTML", kb)
	}
	return s.SendMessage(chatID, text, "HTML", kb)
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

func getDeviceDisplayName(d db.Device) string {
	if d.CustomName.Valid && strings.TrimSpace(d.CustomName.String) != "" {
		return d.CustomName.String
	}
	if d.Hostname.Valid && strings.TrimSpace(d.Hostname.String) != "" {
		return d.Hostname.String
	}
	return d.MacAddress
}

func getDeviceIP(d db.Device) string {
	if d.IPAddress.Valid && strings.TrimSpace(d.IPAddress.String) != "" {
		return d.IPAddress.String
	}
	return "N/A"
}
