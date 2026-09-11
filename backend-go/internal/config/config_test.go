package config

import (
	"testing"
)

func TestCleanDBPath(t *testing.T) {
	tests := []struct {
		input    string
		expected string
	}{
		{"sqlite+aiosqlite:////data/app.db", "/data/app.db"},
		{"sqlite+aiosqlite:///data/app.db", "/data/app.db"},
		{"sqlite:////data/app.db", "/data/app.db"},
		{"sqlite:///data/app.db", "/data/app.db"},
		{"sqlite://data/app.db", "data/app.db"},
		{"/var/lib/mikroman/app.db", "/var/lib/mikroman/app.db"},
		{"data/app.db", "data/app.db"},
	}

	for _, tc := range tests {
		got := CleanDBPath(tc.input)
		if got != tc.expected {
			t.Errorf("CleanDBPath(%q) = %q; expected %q", tc.input, got, tc.expected)
		}
	}
}
