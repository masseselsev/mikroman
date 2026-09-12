package db

import (
	"database/sql"
	"time"
)

// InsertSpeedTestResult stores a newly completed or timed out speed test run.
func (db *DB) InsertSpeedTestResult(res *SpeedTestResult) error {
	query := `
		INSERT INTO speed_test_results (
			router_id, download_mbps, upload_mbps, ping_ms, jitter_ms,
			packet_loss_pct, server_name, isp, result_url, status, error, raw_output
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
	`

	row, err := db.SqlDB.Exec(
		query,
		res.RouterID,
		res.DownloadMbps,
		res.UploadMbps,
		res.PingMs,
		res.JitterMs,
		res.PacketLossPct,
		res.ServerName,
		res.ISP,
		res.ResultURL,
		res.Status,
		res.Error,
		res.RawOutput,
	)
	if err != nil {
		return err
	}

	id, err := row.LastInsertId()
	if err == nil {
		res.ID = int(id)
	}
	res.CreatedAt = time.Now().UTC()
	return nil
}

// GetLatestSpeedTestResult retrieves the most recent speed test measurement for a router.
func (db *DB) GetLatestSpeedTestResult(routerID int) (*SpeedTestResult, error) {
	query := `
		SELECT id, router_id, created_at, download_mbps, upload_mbps, ping_ms, jitter_ms,
		       packet_loss_pct, server_name, isp, result_url, status, error, raw_output
		FROM speed_test_results
		WHERE router_id = ?
		ORDER BY id DESC LIMIT 1
	`
	var r SpeedTestResult

	err := db.SqlDB.QueryRow(query, routerID).Scan(
		&r.ID,
		&r.RouterID,
		&r.CreatedAt,
		&r.DownloadMbps,
		&r.UploadMbps,
		&r.PingMs,
		&r.JitterMs,
		&r.PacketLossPct,
		&r.ServerName,
		&r.ISP,
		&r.ResultURL,
		&r.Status,
		&r.Error,
		&r.RawOutput,
	)
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return nil, err
	}

	return &r, nil
}

// GetSpeedTestHistory returns the last N speed test results for a router.
func (db *DB) GetSpeedTestHistory(routerID int, limit int) ([]SpeedTestResult, error) {
	if limit <= 0 {
		limit = 20
	}
	if limit > 100 {
		limit = 100
	}

	query := `
		SELECT id, router_id, created_at, download_mbps, upload_mbps, ping_ms, jitter_ms,
		       packet_loss_pct, server_name, isp, result_url, status, error, raw_output
		FROM speed_test_results
		WHERE router_id = ?
		ORDER BY id DESC LIMIT ?
	`
	rows, err := db.SqlDB.Query(query, routerID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var list []SpeedTestResult
	for rows.Next() {
		var r SpeedTestResult
		if err := rows.Scan(
			&r.ID,
			&r.RouterID,
			&r.CreatedAt,
			&r.DownloadMbps,
			&r.UploadMbps,
			&r.PingMs,
			&r.JitterMs,
			&r.PacketLossPct,
			&r.ServerName,
			&r.ISP,
			&r.ResultURL,
			&r.Status,
			&r.Error,
			&r.RawOutput,
		); err == nil {
			list = append(list, r)
		}
	}

	if list == nil {
		list = []SpeedTestResult{}
	}
	return list, nil
}

