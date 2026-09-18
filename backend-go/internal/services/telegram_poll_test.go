package services

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/masseselsev/mikroman/internal/crypto"
	"github.com/masseselsev/mikroman/internal/db"
)

// fakeTelegramPoller is an httptest stand-in for the Telegram Bot API. It parks
// every getUpdates long poll until the run that issued it is cancelled (or the
// test releases it) and counts how many polls are parked at the same time.
//
// That counter is the leak detector: Telegram serves a single getUpdates consumer
// per bot token, so more than one parked poll from this process means a previous
// poll loop is still alive next to the current one. Parking the request is what
// makes the check deterministic — a real 20 s long poll is emulated instead of
// hoping that the loops happen to be inside their HTTP call when the test looks.
type fakeTelegramPoller struct {
	server   *httptest.Server
	inFlight int32
	requests int32
	hold     chan struct{}
}

func newFakeTelegramPoller() *fakeTelegramPoller {
	f := &fakeTelegramPoller{hold: make(chan struct{})}
	f.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&f.requests, 1)
		w.Header().Set("Content-Type", "application/json")

		if strings.Contains(r.URL.Path, "getUpdates") {
			// Drain the request body first. Go's server only watches the client
			// connection (and therefore cancels r.Context()) once the request body
			// has been read; without this the parked handler would never learn
			// that the poller went away and the counter below would lie.
			_, _ = io.Copy(io.Discard, r.Body)
			_ = r.Body.Close()

			atomic.AddInt32(&f.inFlight, 1)
			select {
			case <-f.hold:
			case <-r.Context().Done():
			}
			atomic.AddInt32(&f.inFlight, -1)

			_ = json.NewEncoder(w).Encode(map[string]interface{}{
				"ok":     true,
				"result": []interface{}{},
			})
			return
		}

		// getMe / deleteWebhook answer immediately.
		_ = json.NewEncoder(w).Encode(map[string]interface{}{
			"ok":     true,
			"result": map[string]interface{}{},
		})
	}))
	return f
}

// release unblocks every parked poll and shuts the fake API down. It exists so a
// run that leaks a loop (the behaviour these tests guard against) still lets the
// test binary finish cleanly.
func (f *fakeTelegramPoller) release() {
	close(f.hold)
	f.server.Close()
}

// newTestTelegramBot wires a service to the fake poller. The token lives in a
// temporary database because Start() loads its settings from there, and the HTTP
// client is redirected with the same transport trick the other service tests use,
// so no request ever leaves the machine.
func newTestTelegramBot(t *testing.T, f *fakeTelegramPoller) (*TelegramBotService, *db.DB) {
	t.Helper()

	fernet, _ := crypto.NewFernet("cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E=")
	database, err := db.Open(filepath.Join(t.TempDir(), "test_telegram_poll.db"), fernet)
	if err != nil {
		t.Fatalf("failed to open database: %v", err)
	}
	if err := database.SetSetting("telegram_bot_token", "TEST_TOKEN", ""); err != nil {
		t.Fatalf("failed to store test bot token: %v", err)
	}

	svc := NewTelegramBotService(database, nil, nil)
	client := f.server.Client()
	client.Transport = &testServerTransport{targetURL: f.server.URL}
	svc.httpClient = client

	return svc, database
}

// waitForInFlight waits until want polls are parked, and fails the test if that
// state is never reached inside the deadline.
func waitForInFlight(t *testing.T, f *fakeTelegramPoller, want int32, timeout time.Duration) {
	t.Helper()

	deadline := time.Now().Add(timeout)
	for {
		if got := atomic.LoadInt32(&f.inFlight); got == want {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("expected %d parked getUpdates poll(s) within %s, got %d",
				want, timeout, atomic.LoadInt32(&f.inFlight))
		}
		time.Sleep(10 * time.Millisecond)
	}
}

// settledInFlight returns the number of parked polls once that number has stopped
// changing for stableFor, or the last observed value when timeout expires first.
// "Stopped changing" is required because a restart legitimately overlaps by a few
// milliseconds: the old request is aborted while the new one is already on its way.
func settledInFlight(f *fakeTelegramPoller, timeout, stableFor time.Duration) int32 {
	deadline := time.Now().Add(timeout)
	last := atomic.LoadInt32(&f.inFlight)
	changedAt := time.Now()

	for time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
		cur := atomic.LoadInt32(&f.inFlight)
		if cur != last {
			last = cur
			changedAt = time.Now()
			continue
		}
		if time.Since(changedAt) >= stableFor {
			return cur
		}
	}
	return last
}

// TestTelegramBot_ReconfigureLeavesSinglePollLoop saves settings six times in a
// row and requires the service to end up with exactly one poll loop. Every
// Reconfigure() replaces the run, so a loop that follows the service field
// instead of its own channel survives the stop and keeps polling next to the
// fresh loop — the condition that produces 409 "terminated by other getUpdates
// request" answers in production.
func TestTelegramBot_ReconfigureLeavesSinglePollLoop(t *testing.T) {
	f := newFakeTelegramPoller()
	defer f.release()

	svc, database := newTestTelegramBot(t, f)
	defer database.Close()

	svc.Start()
	waitForInFlight(t, f, 1, 5*time.Second)

	const reconfigures = 5
	for i := 0; i < reconfigures; i++ {
		svc.Reconfigure()
	}

	if got := settledInFlight(f, 3*time.Second, 300*time.Millisecond); got != 1 {
		t.Errorf("after 1 start + %d Reconfigure() calls exactly 1 poll loop must be parked in getUpdates, got %d "+
			"(the %d earlier loops were stranded instead of exiting)", reconfigures, got, got-1)
	}

	svc.Stop()
	if got := settledInFlight(f, 3*time.Second, 200*time.Millisecond); got != 0 {
		t.Errorf("after Stop() no poll loop may stay parked in getUpdates, got %d", got)
	}
}

// TestTelegramBot_StopTerminatesPollLoopPromptly requires Stop() to return only
// after the loop is actually gone, and within a bounded wait while a long poll is
// parked (the test never releases it before the assertions). A fixed sleep or a
// loop that only notices the stop between polls both fail here.
func TestTelegramBot_StopTerminatesPollLoopPromptly(t *testing.T) {
	f := newFakeTelegramPoller()
	defer f.release()

	svc, database := newTestTelegramBot(t, f)
	defer database.Close()

	svc.Start()
	waitForInFlight(t, f, 1, 5*time.Second)

	stopped := make(chan struct{})
	go func() {
		svc.Stop()
		close(stopped)
	}()

	select {
	case <-stopped:
	case <-time.After(3 * time.Second):
		t.Fatalf("Stop() did not return within 3s while a getUpdates long poll was parked")
	}

	if got := settledInFlight(f, 3*time.Second, 200*time.Millisecond); got != 0 {
		t.Errorf("Stop() returned but %d poll loop(s) are still parked in getUpdates — the loop did not exit", got)
	}
}

// TestTelegramBot_StopIsIdempotent repeats Stop() around a Reconfigure() and
// requires two things: no panic on a repeated stop (the stop path must close the
// run's channel at most once, including when two Stops race) and no loop left
// behind by any of the stops.
func TestTelegramBot_StopIsIdempotent(t *testing.T) {
	f := newFakeTelegramPoller()
	defer f.release()

	svc, database := newTestTelegramBot(t, f)
	defer database.Close()

	svc.Start()
	waitForInFlight(t, f, 1, 5*time.Second)

	svc.Stop()
	svc.Stop() // repeated stop on an already stopped service: must be a no-op

	svc.Reconfigure()
	waitForInFlight(t, f, 1, 5*time.Second)

	// Two stops racing for the same run: exactly one of them may close its
	// channel, otherwise the second close panics on the closed channel.
	var wg sync.WaitGroup
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			svc.Stop()
		}()
	}
	wg.Wait()

	if got := settledInFlight(f, 3*time.Second, 200*time.Millisecond); got != 0 {
		t.Errorf("after repeated Stop() calls no poll loop may stay parked in getUpdates, got %d", got)
	}
}
