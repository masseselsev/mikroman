package crypto

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"golang.org/x/crypto/pbkdf2"
)

const (
	PBKDF2Iterations = 600000
	SaltLength       = 16
	KeyLength        = 32
)

// SessionPayload matches the JSON payload stored in the stateless session cookie.
type SessionPayload struct {
	Sub   string `json:"sub"`
	Iat   int64  `json:"iat"`
	Exp   int64  `json:"exp"`
	Nonce string `json:"nonce"`
}

// HashPassword hashes a password using PBKDF2-HMAC-SHA256 with 600,000 iterations.
func HashPassword(password string) (string, error) {
	salt := make([]byte, SaltLength)
	if _, err := rand.Read(salt); err != nil {
		return "", fmt.Errorf("failed to generate salt: %w", err)
	}

	derived := pbkdf2.Key([]byte(password), salt, PBKDF2Iterations, KeyLength, sha256.New)
	return fmt.Sprintf("pbkdf2_sha256$%d$%s$%s", PBKDF2Iterations, hex.EncodeToString(salt), hex.EncodeToString(derived)), nil
}

// VerifyPassword verifies a plain password against a stored PBKDF2 hash.
func VerifyPassword(password, storedHash string) bool {
	if password == "" || storedHash == "" {
		return false
	}

	parts := strings.Split(storedHash, "$")
	if len(parts) != 4 || parts[0] != "pbkdf2_sha256" {
		return false
	}

	iterations, err := strconv.Atoi(parts[1])
	if err != nil || iterations <= 0 {
		return false
	}

	salt, err := hex.DecodeString(parts[2])
	if err != nil {
		return false
	}

	expectedDerived, err := hex.DecodeString(parts[3])
	if err != nil {
		return false
	}

	derived := pbkdf2.Key([]byte(password), salt, iterations, len(expectedDerived), sha256.New)
	return subtle.ConstantTimeCompare(derived, expectedDerived) == 1
}

// CreateSessionToken encrypts a stateless session payload into a Fernet token.
func (f *Fernet) CreateSessionToken(username string, expireDays int) (string, error) {
	if expireDays <= 0 {
		expireDays = 7
	}
	now := time.Now().Unix()

	nonceBytes := make([]byte, 8)
	_, _ = rand.Read(nonceBytes)

	payload := SessionPayload{
		Sub:   username,
		Iat:   now,
		Exp:   now + int64(expireDays*86400),
		Nonce: hex.EncodeToString(nonceBytes),
	}

	data, err := json.Marshal(payload)
	if err != nil {
		return "", err
	}

	return f.Encrypt(string(data))
}

// VerifySessionToken decrypts and validates a stateless session token.
func (f *Fernet) VerifySessionToken(token string) (*SessionPayload, error) {
	if token == "" {
		return nil, errors.New("empty token")
	}

	data, err := f.Decrypt(token)
	if err != nil {
		return nil, err
	}

	var payload SessionPayload
	if err := json.Unmarshal([]byte(data), &payload); err != nil {
		return nil, err
	}

	if payload.Exp < time.Now().Unix() {
		return nil, errors.New("session expired")
	}

	return &payload, nil
}

// GenerateCSRFToken generates a 32-byte (64 hex char) random CSRF token.
func GenerateCSRFToken() string {
	b := make([]byte, 32)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// VerifyCSRFToken constant-time compares header against cookie.
func VerifyCSRFToken(headerVal, cookieVal string) bool {
	if headerVal == "" || cookieVal == "" {
		return false
	}
	return hmac.Equal([]byte(headerVal), []byte(cookieVal))
}
