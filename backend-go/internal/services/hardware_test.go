package services

import (
	"testing"
)

func TestResolveCPUIdentity(t *testing.T) {
	// 1. hAP be3 Media (User's router in screenshot!)
	id := ResolveCPUIdentity("MA53UG+HbeH", "hAP be^3 Media", "ipq5300", "ARM64", "arm64")
	if id.Model != "IPQ-5322" || !id.Exact {
		t.Errorf("expected IPQ-5322 exact, got %+v", id)
	}

	// 2. hAP be3 Media by board name only
	id2 := ResolveCPUIdentity("", "hAP be3 Media", "ipq5300", "ARM64", "arm64")
	if id2.Model != "IPQ-5322" || !id2.Exact {
		t.Errorf("expected IPQ-5322 exact from board name, got %+v", id2)
	}

	// 3. RB5009
	id3 := ResolveCPUIdentity("RB5009UG+S+IN", "", "", "", "arm64")
	if id3.Model != "Marvell 88F7040" || !id3.Exact {
		t.Errorf("expected Marvell 88F7040, got %+v", id3)
	}

	// 4. x86 CHR
	id4 := ResolveCPUIdentity("", "", "", "Intel(R) Xeon(R) CPU E5-2680", "x86_64")
	if id4.Model != "Intel(R) Xeon(R) CPU E5-2680" || !id4.Exact {
		t.Errorf("expected Intel Xeon, got %+v", id4)
	}

	// 5. Unknown routerboard with platform
	id5 := ResolveCPUIdentity("", "CustomBoard", "al21400", "ARM", "arm")
	if id5.Model != "al21400" || id5.Exact {
		t.Errorf("expected platform fallback al21400 inexact, got %+v", id5)
	}
}
