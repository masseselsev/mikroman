package api

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"github.com/masseselsev/mikroman/internal/db"
)

// A client that connects before any live tick exists — a cold server start, or
// a router that is currently unreachable — used to see an empty telemetry bar
// until the first successful poll. The bootstrap frame closes that gap: a
// telemetry_tick assembled from stored history (15-minute medians) plus the
// database's live user/device counts, tagged so it can never be mistaken for
// a real-time reading. It is a placeholder that looks plausible, not a claim
// about the present: the first live tick replaces it within seconds, and a
// router with no history at all still gets nothing, which is the honest state.

// bootstrapWindowBuckets is how many recent buckets the frame summarises.
// Buckets are 15 minutes, so 96 of them span the same horizon as the 24 h
// chart. The most recent rows are taken rather than a wall-clock window: a
// freshly installed router bootstraps from its handful of buckets instead of
// waiting for a full day of history to exist.
const bootstrapWindowBuckets = 96

// bootstrapTTL caps how long a generated frame is reused across connections.
// Cold clients arriving together (a refresh storm while the router is down)
// should not each re-run the median queries; a minute keeps the DB-backed
// counts credible between regenerations.
const bootstrapTTL = time.Minute

type bootstrapEntry struct {
	frame   []byte
	created time.Time
}

// AttachDatabase gives the hub read access to persisted history, enabling the
// bootstrap frame for clients that connect before any live tick. Called once
// at startup; a hub without a database behaves exactly as before, which is
// what the hub's own unit tests rely on.
func (h *Hub) AttachDatabase(database *db.DB) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.bootDB = database
	h.bootCache = make(map[int]bootstrapEntry)
}

// bootstrapFrame returns the frame for routerID (0 = default router, matching
// the WS query-parameter semantics), building it on demand and caching per
// router for bootstrapTTL. Returns nil when there is nothing to show: no
// database, no buckets, unknown router, or no WAN configured — the client
// then simply waits for a live tick like before.
func (h *Hub) bootstrapFrame(routerID int) []byte {
	h.mu.Lock()
	database := h.bootDB
	if database == nil {
		h.mu.Unlock()
		return nil
	}
	if entry, ok := h.bootCache[routerID]; ok && time.Since(entry.created) < bootstrapTTL {
		h.mu.Unlock()
		return entry.frame
	}
	h.mu.Unlock()

	frame := buildBootstrapFrame(database, routerID)

	h.mu.Lock()
	if frame != nil {
		h.bootCache[routerID] = bootstrapEntry{frame: frame, created: time.Now()}
	}
	h.mu.Unlock()
	return frame
}

// buildBootstrapFrame assembles the telemetry_tick JSON. Split out from the
// hub so it is testable directly against any database.
func buildBootstrapFrame(database *db.DB, routerID int) []byte {
	targetID, ok := resolveBootstrapRouter(database, routerID)
	if !ok {
		return nil
	}

	router := map[string]interface{}{
		"id":           targetID,
		"bootstrapped": true,
	}
	// Every measurement is optional and emitted only when a median exists:
	// absent must never render as zero — that is the one sin a bootstrap frame
	// may not commit, and it is why each tile keeps its em-dash placeholder.
	if cpu, memPct, freeMB, totalMB, temp, sysOK := bootstrapSystemReading(database, targetID); sysOK {
		router["cpu_load"] = round1(cpu)
		router["memory_usage_pct"] = round1(memPct)
		router["free_memory_mb"] = round1(freeMB)
		router["total_memory_mb"] = round1(totalMB)
		if temp != nil {
			router["temperature"] = round1(*temp)
		}
	}
	if rx, tx, wanOK := bootstrapWanReading(database, targetID); wanOK {
		router["wan_rx_bps"] = math.Round(rx)
		router["wan_tx_bps"] = math.Round(tx)
	}
	if names, ok := bootstrapMonitoredNames(database, targetID); ok {
		router["monitored_interfaces"] = names
	}
	// User/device counts come straight from the database, not from buckets:
	// the rosters are as current as the last successful sync, and a median
	// would understate them the day after a client was added. A read failure
	// omits the fields; the tiles then show the placeholder rather than a
	// confidently wrong "0".
	users, usersErr := database.GetUsers(&targetID)
	if usersErr == nil {
		router["user_count"] = len(users)
		if devices, err := database.GetDevices(&targetID); err == nil {
			active := 0
			for _, d := range devices {
				if d.IsActive {
					active++
				}
			}
			router["client_device_count"] = len(devices)
			router["active_clients"] = active
		}
	}

	// If not one measurement could be summarised, send nothing: a frame of
	// only the bootstrapped flag would swap an honest placeholder for a fake
	// "alive" bar. (user_count alone still counts as content — a known roster
	// is a known roster.)
	if len(router) <= 2 {
		return nil
	}

	frame, err := json.Marshal(map[string]interface{}{
		"type":      "telemetry_tick",
		"timestamp": float64(time.Now().Unix()),
		"router_id": targetID,
		"bootstrap": true,
		"router":    router,
	})
	if err != nil {
		return nil
	}
	return frame
}

// resolveBootstrapRouter maps the requested id (0 = default) onto a real
// router row. An explicit id that does not exist gets no frame: scoping
// failures must never leak another router's history.
func resolveBootstrapRouter(database *db.DB, routerID int) (int, bool) {
	if routerID == 0 {
		def, err := database.GetDefaultRouter()
		if err != nil || def == nil {
			return 0, false
		}
		return def.ID, true
	}
	routers, err := database.GetRouters()
	if err != nil {
		return 0, false
	}
	for _, r := range routers {
		if r.ID == routerID {
			return r.ID, true
		}
	}
	return 0, false
}

// bootstrapSystemReading returns median CPU %, memory usage %, free/total RAM
// in MB, and — only if the window ever carried it — median temperature over
// the router's recent buckets. Free RAM is derived per bucket from its stored
// used/total pair; a router whose footprint changed mid-window is approximated
// by the median, which is exactly what a placeholder is allowed to do.
func bootstrapSystemReading(database *db.DB, routerID int) (cpu, memPct, freeMB, totalMB float64, temp *float64, ok bool) {
	rows, err := database.SqlDB.Query(`
		SELECT cpu_load_avg,
		       memory_used_bytes_avg, memory_total_bytes_max, temperature_avg
		FROM system_metric_buckets
		WHERE router_id = ?
		ORDER BY bucket_start DESC
		LIMIT ?`, routerID, bootstrapWindowBuckets)
	if err != nil {
		return 0, 0, 0, 0, nil, false
	}
	defer rows.Close()

	var cpus, frees, totals []float64
	var temps []float64
	for rows.Next() {
		var cpuAvg, usedAvg, totalMax float64
		var tempAvg sql.NullFloat64
		if err := rows.Scan(&cpuAvg, &usedAvg, &totalMax, &tempAvg); err != nil {
			continue
		}
		cpus = append(cpus, cpuAvg)
		totalMBv := totalMax / (1024 * 1024)
		frees = append(frees, math.Max(totalMBv-usedAvg/(1024*1024), 0))
		totals = append(totals, totalMBv)
		if tempAvg.Valid {
			temps = append(temps, tempAvg.Float64)
		}
	}
	if len(cpus) == 0 {
		return 0, 0, 0, 0, nil, false
	}
	if rows.Err() != nil {
		return 0, 0, 0, 0, nil, false
	}
	if len(temps) > 0 {
		t := medianFloat(temps)
		temp = &t
	}
	// The memory triple must be coherent (free + used == total, pct matching
	// them): medians taken independently from the stored pct column could land
	// on a bucket whose footprint differed and print an impossible split. The
	// derived pct says exactly what the two printed MB figures agree on.
	freeMed, totalMed := medianFloat(frees), medianFloat(totals)
	if totalMed > 0 {
		memPct = math.Min(math.Max((totalMed-freeMed)/totalMed*100, 0), 100)
	}
	return medianFloat(cpus), memPct, freeMed, totalMed, temp, true
}

// bootstrapWanReading returns median rx/tx of the SUM of the monitored
// interfaces per bucket — the same quantity the live tick reports (WAN rates
// are summed across the selection), just summarised. A bucket where not every
// monitored interface contributed is dropped via HAVING: its partial sum
// describes a quieter router than this one, and letting it into the median
// would bias the placeholder down for reasons unrelated to traffic.
func bootstrapWanReading(database *db.DB, routerID int) (rx, tx float64, ok bool) {
	names, ok := bootstrapMonitoredNames(database, routerID)
	if !ok {
		return 0, 0, false
	}
	args := make([]interface{}, 0, len(names)+3)
	args = append(args, routerID)
	placeholders := make([]string, 0, len(names))
	for _, name := range names {
		placeholders = append(placeholders, "?")
		args = append(args, name)
	}
	args = append(args, len(names), bootstrapWindowBuckets)
	query := fmt.Sprintf(`
		SELECT bucket_start, SUM(rx_rate_bps_sum), SUM(tx_rate_bps_sum)
		FROM interface_metric_buckets
		WHERE router_id = ? AND interface_name IN (%s)
		GROUP BY bucket_start
		HAVING COUNT(DISTINCT interface_name) = ?
		ORDER BY bucket_start DESC
		LIMIT ?`, strings.Join(placeholders, ", "))

	rows, err := database.SqlDB.Query(query, args...)
	if err != nil {
		return 0, 0, false
	}
	defer rows.Close()

	var rxs, txs []float64
	for rows.Next() {
		var start string
		var rxSum, txSum float64
		if err := rows.Scan(&start, &rxSum, &txSum); err != nil {
			continue
		}
		rxs = append(rxs, rxSum)
		txs = append(txs, txSum)
	}
	if len(rxs) == 0 {
		return 0, 0, false
	}
	if rows.Err() != nil {
		return 0, 0, false
	}
	return medianFloat(rxs), medianFloat(txs), true
}

// bootstrapMonitoredNames mirrors the collector's read of the WAN selection:
// the per-router key first, the legacy global key as fallback.
func bootstrapMonitoredNames(database *db.DB, routerID int) ([]string, bool) {
	val, err := database.GetSetting(fmt.Sprintf("monitored_interfaces_%d", routerID))
	if err != nil || val == "" {
		val, err = database.GetSetting("monitored_interfaces_default")
	}
	if err != nil || val == "" {
		return nil, false
	}
	var names []string
	if err := json.Unmarshal([]byte(val), &names); err != nil || len(names) == 0 {
		return nil, false
	}
	return names, true
}

func round1(v float64) float64 { return math.Round(v*10) / 10 }

// medianFloat returns the middle value of a sorted copy of values. Callers
// guard against empty input.
func medianFloat(values []float64) float64 {
	v := append([]float64{}, values...)
	sort.Float64s(v)
	mid := len(v) / 2
	if len(v)%2 == 1 {
		return v[mid]
	}
	return (v[mid-1] + v[mid]) / 2
}
