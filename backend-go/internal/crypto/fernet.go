package crypto

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	// Prefix used for encrypted strings in database.
	Prefix = "enc:v1:"
	// FernetVersion byte required by specification.
	FernetVersion byte = 0x80
)

var (
	ErrInvalidToken = errors.New("invalid fernet token")
	ErrSignature    = errors.New("invalid token signature")
	ErrPadding      = errors.New("invalid pkcs7 padding")
)

// Fernet handles encryption and decryption matching Python's cryptography.fernet.
type Fernet struct {
	signingKey    []byte
	encryptionKey []byte
}

// NewFernet parses a 32-byte URL-safe base64-encoded key.
func NewFernet(keyB64 string) (*Fernet, error) {
	key, err := base64.URLEncoding.DecodeString(keyB64)
	if err != nil {
		return nil, fmt.Errorf("failed to decode base64 key: %w", err)
	}
	if len(key) != 32 {
		return nil, fmt.Errorf("invalid key length %d, expected 32 bytes", len(key))
	}
	return &Fernet{
		signingKey:    key[:16],
		encryptionKey: key[16:],
	}, nil
}

// ResolveKey attempts to find a Fernet key from environment, secret file, or generates one.
func ResolveKey(explicitKey, dataDir string) (*Fernet, error) {
	if explicitKey != "" {
		return NewFernet(explicitKey)
	}

	keyPath := filepath.Join(dataDir, ".secret_key")
	if data, err := os.ReadFile(keyPath); err == nil {
		k := strings.TrimSpace(string(data))
		if k != "" {
			return NewFernet(k)
		}
	}
	altKeyPath := filepath.Join(dataDir, "secret.key")
	if data, err := os.ReadFile(altKeyPath); err == nil {
		k := strings.TrimSpace(string(data))
		if k != "" {
			return NewFernet(k)
		}
	}

	// Generate a new 32-byte key
	rawKey := make([]byte, 32)
	if _, err := io.ReadFull(rand.Reader, rawKey); err != nil {
		return nil, fmt.Errorf("failed to generate random key: %w", err)
	}
	keyB64 := base64.URLEncoding.EncodeToString(rawKey)

	// Ensure directory exists
	if err := os.MkdirAll(dataDir, 0700); err == nil {
		_ = os.WriteFile(keyPath, []byte(keyB64+"\n"), 0600)
	}

	return NewFernet(keyB64)
}

// Decrypt decodes a Fernet token.
func (f *Fernet) Decrypt(tokenStr string) (string, error) {
	token, err := base64.URLEncoding.DecodeString(tokenStr)
	if err != nil {
		return "", fmt.Errorf("%w: %v", ErrInvalidToken, err)
	}

	// 1 (version) + 8 (timestamp) + 16 (IV) + 16 (min cipher block) + 32 (HMAC) = 73 bytes minimum
	if len(token) < 73 {
		return "", ErrInvalidToken
	}

	if token[0] != FernetVersion {
		return "", fmt.Errorf("%w: unexpected version %x", ErrInvalidToken, token[0])
	}

	// Split payload and HMAC
	payload := token[:len(token)-32]
	expectedHMAC := token[len(token)-32:]

	mac := hmac.New(sha256.New, f.signingKey)
	mac.Write(payload)
	actualHMAC := mac.Sum(nil)

	if !hmac.Equal(actualHMAC, expectedHMAC) {
		return "", ErrSignature
	}

	iv := payload[9:25]
	ciphertext := payload[25:]

	block, err := aes.NewCipher(f.encryptionKey)
	if err != nil {
		return "", err
	}

	if len(ciphertext)%aes.BlockSize != 0 {
		return "", fmt.Errorf("%w: ciphertext not a multiple of block size", ErrInvalidToken)
	}

	plaintext := make([]byte, len(ciphertext))
	mode := cipher.NewCBCDecrypter(block, iv)
	mode.CryptBlocks(plaintext, ciphertext)

	unpadded, err := pkcs7Unpad(plaintext, aes.BlockSize)
	if err != nil {
		return "", err
	}

	return string(unpadded), nil
}

// Encrypt generates a Fernet token for plaintext.
func (f *Fernet) Encrypt(plaintext string) (string, error) {
	padded := pkcs7Pad([]byte(plaintext), aes.BlockSize)

	iv := make([]byte, aes.BlockSize)
	if _, err := io.ReadFull(rand.Reader, iv); err != nil {
		return "", err
	}

	block, err := aes.NewCipher(f.encryptionKey)
	if err != nil {
		return "", err
	}

	ciphertext := make([]byte, len(padded))
	mode := cipher.NewCBCEncrypter(block, iv)
	mode.CryptBlocks(ciphertext, padded)

	// Build payload: version(1) + timestamp(8) + iv(16) + ciphertext
	payload := make([]byte, 1+8+len(iv)+len(ciphertext))
	payload[0] = FernetVersion
	binary.BigEndian.PutUint64(payload[1:9], uint64(time.Now().Unix()))
	copy(payload[9:25], iv)
	copy(payload[25:], ciphertext)

	// HMAC over payload
	mac := hmac.New(sha256.New, f.signingKey)
	mac.Write(payload)
	signature := mac.Sum(nil)

	token := append(payload, signature...)
	return base64.URLEncoding.EncodeToString(token), nil
}

// DecryptSecret decrypts a database string if prefixed with "enc:v1:".
func (f *Fernet) DecryptSecret(value string) string {
	if !strings.HasPrefix(value, Prefix) {
		return value
	}
	token := strings.TrimPrefix(value, Prefix)
	plain, err := f.Decrypt(token)
	if err != nil {
		return ""
	}
	return plain
}

// EncryptSecret encrypts a database string with "enc:v1:" prefix.
func (f *Fernet) EncryptSecret(value string) (string, error) {
	if strings.HasPrefix(value, Prefix) || value == "" {
		return value, nil
	}
	token, err := f.Encrypt(value)
	if err != nil {
		return "", err
	}
	return Prefix + token, nil
}

func pkcs7Pad(data []byte, blockSize int) []byte {
	padding := blockSize - (len(data) % blockSize)
	padtext := bytes.Repeat([]byte{byte(padding)}, padding)
	return append(data, padtext...)
}

func pkcs7Unpad(data []byte, blockSize int) ([]byte, error) {
	length := len(data)
	if length == 0 {
		return nil, ErrPadding
	}
	pad := int(data[length-1])
	if pad == 0 || pad > blockSize || pad > length {
		return nil, ErrPadding
	}
	for i := length - pad; i < length; i++ {
		if data[i] != byte(pad) {
			return nil, ErrPadding
		}
	}
	return data[:length-pad], nil
}
