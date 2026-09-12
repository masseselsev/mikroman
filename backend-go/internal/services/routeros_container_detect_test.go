package services

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestIsRunningInRouterOSContainer_Env(t *testing.T) {
	orig := os.Getenv("MIKROMAN_ON_ROUTER")
	defer os.Setenv("MIKROMAN_ON_ROUTER", orig)

	os.Setenv("MIKROMAN_ON_ROUTER", "true")
	if !IsRunningInRouterOSContainer(context.Background(), nil) {
		t.Errorf("expected true when MIKROMAN_ON_ROUTER=true")
	}

	os.Setenv("MIKROMAN_ON_ROUTER", "1")
	if !IsRunningInRouterOSContainer(context.Background(), nil) {
		t.Errorf("expected true when MIKROMAN_ON_ROUTER=1")
	}

	os.Setenv("MIKROMAN_ON_ROUTER", "false")
	// Should fall through to other checks, which fail with nil client and no /proc/version match
	origProc := procVersionPath
	defer func() { procVersionPath = origProc }()

	tmpDir := t.TempDir()
	fakeProc := filepath.Join(tmpDir, "version")
	_ = os.WriteFile(fakeProc, []byte("Linux version 6.6.0-generic (buildd@ubuntu)"), 0644)
	procVersionPath = fakeProc

	if IsRunningInRouterOSContainer(context.Background(), nil) {
		t.Errorf("expected false for standard ubuntu kernel and MIKROMAN_ON_ROUTER=false")
	}
}

func TestIsRunningInRouterOSContainer_KernelSignature(t *testing.T) {
	orig := os.Getenv("MIKROMAN_ON_ROUTER")
	defer os.Setenv("MIKROMAN_ON_ROUTER", orig)
	os.Setenv("MIKROMAN_ON_ROUTER", "false")

	origProc := procVersionPath
	defer func() { procVersionPath = origProc }()

	tmpDir := t.TempDir()
	fakeProc := filepath.Join(tmpDir, "version")
	_ = os.WriteFile(fakeProc, []byte("Linux version 5.6.3 (mikrotik@build) #1 SMP PREEMPT"), 0644)
	procVersionPath = fakeProc

	if !IsRunningInRouterOSContainer(context.Background(), nil) {
		t.Errorf("expected true when /proc/version contains mikrotik signature")
	}
}

