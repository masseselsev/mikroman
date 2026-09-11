package routeros

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"log"
	"math/big"
	"strconv"
	"strings"
	"time"
)

const (
	BackupFilePrefix      = "mikroman-backup-"
	BackupSettleInterval  = 300 * time.Millisecond
	DefaultBackupTimeout  = 35 * time.Second
	BackupChunkSize       = 32768
)

// GenerateBackupPassword creates a random alphanumeric password for RouterOS binary backup encryption.
func GenerateBackupPassword(length int) string {
	const chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
	b := make([]byte, length)
	for i := range b {
		n, err := rand.Int(rand.Reader, big.NewInt(int64(len(chars))))
		if err != nil {
			b[i] = chars[i%len(chars)]
		} else {
			b[i] = chars[n.Int64()]
		}
	}
	return string(b)
}

// SweepTemporaryFiles deletes lingering backup temporary files on the router flash.
func (c *Client) SweepTemporaryFiles(ctx context.Context, prefix string) (int, error) {
	if prefix == "" {
		prefix = BackupFilePrefix
	}

	var files []struct {
		ID   string `json:".id"`
		Name string `json:"name"`
	}

	// .proplist is critical to avoid receiving file contents
	err := c.Get(ctx, "/file?.proplist=.id,name", &files)
	if err != nil {
		return 0, fmt.Errorf("failed to list files during sweep: %w", err)
	}

	removed := 0
	for _, f := range files {
		if strings.HasPrefix(f.Name, prefix) {
			fileID := f.ID
			if fileID == "" {
				fileID = f.Name
			}
			delErr := c.Delete(ctx, "/file/"+fileID)
			if delErr == nil {
				removed++
			} else {
				// Fallback to /file/remove
				_ = c.Post(ctx, "/file/remove", map[string]string{"numbers": f.Name}, nil)
				removed++
			}
		}
	}

	return removed, nil
}

// WaitForFileSettled polls /file until the file exists, has size > 0, and size is stable across 2 checks.
func (c *Client) WaitForFileSettled(ctx context.Context, filename string, timeout time.Duration) (int64, error) {
	if timeout <= 0 {
		timeout = DefaultBackupTimeout
	}
	deadline := time.Now().Add(timeout)
	var lastSize int64 = -1
	stableCount := 0

	for time.Now().Before(deadline) {
		select {
		case <-ctx.Done():
			return 0, ctx.Err()
		default:
		}

		var files []map[string]interface{}
		// Request only .id, name, and size to avoid fetching large binary contents
		err := c.Get(ctx, "/file?.proplist=.id,name,size", &files)
		if err == nil {
			var currentSize int64 = -1
			for _, f := range files {
				name, _ := f["name"].(string)
				if name == filename {
					switch v := f["size"].(type) {
					case float64:
						currentSize = int64(v)
					case string:
						currentSize, _ = strconv.ParseInt(v, 10, 64)
					case json.Number:
						currentSize, _ = v.Int64()
					}
					break
				}
			}

			if currentSize > 0 && currentSize == lastSize {
				stableCount++
				if stableCount >= 2 {
					return currentSize, nil
				}
			} else {
				stableCount = 0
			}
			lastSize = currentSize
		}

		time.Sleep(BackupSettleInterval)
	}

	return 0, fmt.Errorf("timeout waiting for %s to settle on router flash", filename)
}

// parseChunkData extracts raw data payload from a /file/read JSON response, preserving binary bytes.
func parseChunkData(body []byte) []byte {
	// 1. Try standard JSON decoding first
	var arr []map[string]interface{}
	if err := json.Unmarshal(body, &arr); err == nil && len(arr) > 0 {
		if val, ok := arr[0]["data"].(string); ok {
			return []byte(val)
		}
	}
	var obj map[string]interface{}
	if err := json.Unmarshal(body, &obj); err == nil {
		if val, ok := obj["data"].(string); ok {
			return []byte(val)
		}
	}

	// 2. Binary fallback: if body contains non-UTF8 bytes, extract between "data":" and trailing quote
	key := []byte(`"data":"`)
	idx := bytes.Index(body, key)
	if idx == -1 {
		return nil
	}
	start := idx + len(key)
	trimmed := bytes.TrimRight(body, " \t\r\n]}")
	if len(trimmed) > start && trimmed[len(trimmed)-1] == '"' {
		return trimmed[start : len(trimmed)-1]
	}

	lastQuote := bytes.LastIndexByte(body, '"')
	if lastQuote > start {
		return body[start:lastQuote]
	}
	return nil
}

// ExportConfig executes /export to a temp file, waits for write to finish, reads text, and sweeps.
func (c *Client) ExportConfig(ctx context.Context, stem string, timeout time.Duration) (string, error) {
	base := fmt.Sprintf("%s%s", BackupFilePrefix, stem)
	rscFilename := fmt.Sprintf("%s.rsc", base)

	// Trigger export command
	err := c.Post(ctx, "/export", map[string]string{"file": base}, nil)
	if err != nil {
		return "", fmt.Errorf("export command failed: %w", err)
	}

	// Wait for file to finish writing
	_, err = c.WaitForFileSettled(ctx, rscFilename, timeout)
	if err != nil {
		return "", err
	}

	var chunks []byte
	offset := 0
	for {
		readReq := map[string]interface{}{
			"file":       rscFilename,
			"offset":     offset,
			"chunk-size": BackupChunkSize,
		}
		respBytes, status, postErr := c.PostJSONRaw(ctx, "/file/read", readReq)
		if postErr != nil || status != 200 {
			break
		}

		chunk := parseChunkData(respBytes)
		if len(chunk) == 0 {
			break
		}

		chunks = append(chunks, chunk...)
		offset += len(chunk)
		if len(chunk) < BackupChunkSize {
			break
		}
	}

	// Clean up temp file
	_, _ = c.SweepTemporaryFiles(ctx, base)

	return string(chunks), nil
}

// CreateSystemBackup executes /system/backup/save, waits for settle, reads binary bytes, and sweeps.
func (c *Client) CreateSystemBackup(ctx context.Context, stem, password string, timeout time.Duration) ([]byte, error) {
	base := fmt.Sprintf("%s%s", BackupFilePrefix, stem)
	backupFilename := fmt.Sprintf("%s.backup", base)

	req := map[string]string{
		"name":       base,
		"password":   password,
		"encryption": "aes-sha256",
	}

	err := c.Post(ctx, "/system/backup/save", req, nil)
	if err != nil {
		return nil, fmt.Errorf("backup save command failed: %w", err)
	}

	_, err = c.WaitForFileSettled(ctx, backupFilename, timeout)
	if err != nil {
		return nil, err
	}

	var chunks []byte
	offset := 0
	for {
		readReq := map[string]interface{}{
			"file":       backupFilename,
			"offset":     offset,
			"chunk-size": BackupChunkSize,
		}
		respBytes, status, postErr := c.PostJSONRaw(ctx, "/file/read", readReq)
		if postErr != nil || status != 200 {
			log.Printf("[Backup] Chunk read failed at offset %d: err=%v status=%d", offset, postErr, status)
			break
		}

		chunk := parseChunkData(respBytes)
		if len(chunk) == 0 {
			break
		}

		chunks = append(chunks, chunk...)
		offset += len(chunk)
		if len(chunk) < BackupChunkSize {
			break
		}
	}

	// Clean up temp file
	_, _ = c.SweepTemporaryFiles(ctx, base)

	return chunks, nil
}

