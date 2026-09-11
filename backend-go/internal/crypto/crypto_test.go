package crypto

import (
	"testing"
)

func TestFernetEncryptDecrypt(t *testing.T) {
	// A valid 32-byte URL-safe base64 key
	testKey := "cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E="

	fernet, err := NewFernet(testKey)
	if err != nil {
		t.Fatalf("failed to create Fernet: %v", err)
	}

	plaintext := "SuperSecretPassword123!@#"
	token, err := fernet.Encrypt(plaintext)
	if err != nil {
		t.Fatalf("failed to encrypt: %v", err)
	}

	decrypted, err := fernet.Decrypt(token)
	if err != nil {
		t.Fatalf("failed to decrypt: %v", err)
	}

	if decrypted != plaintext {
		t.Fatalf("expected %q, got %q", plaintext, decrypted)
	}

	// Test Secret helper
	encSecret, err := fernet.EncryptSecret(plaintext)
	if err != nil {
		t.Fatalf("failed to EncryptSecret: %v", err)
	}

	decSecret := fernet.DecryptSecret(encSecret)
	if decSecret != plaintext {
		t.Fatalf("expected %q, got %q", plaintext, decSecret)
	}

	// Plaintext without prefix should return untouched
	plainPassthrough := fernet.DecryptSecret("unencrypted_password")
	if plainPassthrough != "unencrypted_password" {
		t.Fatalf("expected unencrypted_password, got %q", plainPassthrough)
	}
}

func TestPasswordHashingAndVerification(t *testing.T) {
	password := "AdminPass2026!"
	hash, err := HashPassword(password)
	if err != nil {
		t.Fatalf("failed to hash password: %v", err)
	}

	if !VerifyPassword(password, hash) {
		t.Fatalf("verification failed for correct password")
	}

	if VerifyPassword("WrongPassword", hash) {
		t.Fatalf("verification succeeded for incorrect password")
	}
}

func TestSessionTokens(t *testing.T) {
	testKey := "cw_z4pYJ2-8_9V18R5v6R1XbJ9i9w9G1R1XbJ9i9w9E="
	fernet, err := NewFernet(testKey)
	if err != nil {
		t.Fatalf("failed to create Fernet: %v", err)
	}

	token, err := fernet.CreateSessionToken("admin", 7)
	if err != nil {
		t.Fatalf("failed to create session token: %v", err)
	}

	payload, err := fernet.VerifySessionToken(token)
	if err != nil {
		t.Fatalf("failed to verify session token: %v", err)
	}

	if payload.Sub != "admin" {
		t.Fatalf("expected sub 'admin', got %q", payload.Sub)
	}
}
