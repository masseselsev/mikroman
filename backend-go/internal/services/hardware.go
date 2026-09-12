package services

import (
	"strings"
)

// CpuIdentity represents the best available processor identity for a router.
type CpuIdentity struct {
	Model    string
	Exact    bool
	Platform string
}

// socByProductCode maps RouterBOARD model (product code) -> CPU part number.
var socByProductCode = map[string]string{
	// CCR
	"CCR1009-7G-1C-1S+":    "Tilera TILE-Gx8009",
	"CCR1009-7G-1C-1S+PC":  "Tilera TILE-Gx8009",
	"CCR1009-7G-1C-PC":     "Tilera TILE-Gx8009",
	"CCR1009-8G-1S-1S+":    "Tilera TILE-Gx8009",
	"CCR1009-8G-1S-1S+PC":  "Tilera TILE-Gx8009",
	"CCR1016-12G":          "Tilera TILE-Gx8016",
	"CCR1016-12S-1S+":      "Tilera TILE-Gx8016",
	"CCR1036-12G-4S":       "Tilera TILE-Gx8036",
	"CCR1036-12G-4S-EM":    "Tilera TILE-Gx8036",
	"CCR1036-8G-2S+":       "Tilera TILE-Gx8036",
	"CCR1036-8G-2S+EM":     "Tilera TILE-Gx8036",
	"CCR1072-1G-8S+":       "Tilera TILE-Gx8072",
	"CCR2004-1G-12S+2XS":   "AL32400",
	"CCR2004-16G-2S+":      "AL32400",
	"CCR2004-1G-2XS-PCIe":  "AL32400",
	"CCR2116-12G-4S+":      "AL73400",
	"CCR2216-1G-12XS-2XQ":  "AL73400",
	// Wi-Fi 7
	"MA53UG+HbeH":          "IPQ-5322", // hAP be3 Media
	// Wi-Fi 6
	"C53UiG+5HPaxD2HPaxD":  "IPQ-6010", // hAP ax3
	"C52iG-5HaxD2HaxD-TC":  "IPQ-6010", // hAP ax2
	"cAPGi-5HaxD2HaxD":     "IPQ-6010", // cAP ax
	"L41G-2axD":            "IPQ-5010", // hAP ax lite
	// Wi-Fi 5 and earlier
	"RBD53iG-5HacD2HnD":    "IPQ-4019", // hAP ac3
	"RBD52G-5HacD2HnD-TC":  "IPQ-4018", // hAP ac2
	"RB952Ui-5ac2nD":       "QCA9531",  // hAP ac lite
	"RB941-2nD":            "QCA9533",  // hAP lite
	"RBcAPGi-5acD2nD":      "IPQ-4018",  // cAP ac
	"RBwAPG-5HacT2HnD":     "IPQ-4018",  // wAP ac
	// Wired & switches
	"RB5009UG+S+IN":        "Marvell 88F7040",
	"RB5009UPr+S+IN":       "Marvell 88F7040",
	"RB5009UPr+S+OUT":      "Marvell 88F7040",
	"L009UiGS-RM":          "IPQ-5018",
	"L009UiGS-2HaxD-IN":    "IPQ-5018",
	"RB4011iGS+RM":         "AL21400",
	"RB4011iGS+5HacQ2HnD-IN":"AL21400",
	"RB3011UiAS-RM":        "IPQ-8064",
	"RB1100AHx4":           "AL21400",
	"RB1100AHx4 Dude Edition":"AL21400",
	"RB750Gr3":             "MT7621A",  // hEX
	"RB760iGS":             "MT7621A",  // hEX S
	"RB960PGS":             "QCA9557",  // hEX PoE
	"CRS305-1G-4S+IN":      "98DX3236",
	"CRS309-1G-8S+IN":      "98DX8216",
	"CRS317-1G-16S+RM":     "98DX8216",
	"CRS326-24G-2S+IN":     "98DX3236",
	"CRS326-24G-2S+RM":     "98DX3236",
	"CRS328-24P-4S+RM":     "98DX3236",
	"CRS354-48G-4S+2Q+RM":  "QCA9531",
}

// socByBoardName maps board-name string directly observed off hardware.
var socByBoardName = map[string]string{
	"hAP be^3 Media":       "IPQ-5322",
	"hAP be3 Media":        "IPQ-5322",
	"CCR1009-7G-1C-1S+":    "Tilera TILE-Gx8009",
	"RB5009UG+S+":          "Marvell 88F7040",
	"RB4011iGS+":           "AL21400",
	"hAP ax3":              "IPQ-6010",
	"hAP ax2":              "IPQ-6010",
	"hAP ac3":              "IPQ-4019",
	"hAP ac2":              "IPQ-4018",
	"hEX":                  "MT7621A",
	"hEX S":                "MT7621A",
}

// ResolveCPUIdentity finds the accurate processor model part number for MikroTik hardware.
func ResolveCPUIdentity(productCode, boardName, firmwareType, resourceCPU, architecture string) CpuIdentity {
	pCode := strings.TrimSpace(productCode)
	bName := strings.TrimSpace(boardName)
	platform := strings.TrimSpace(firmwareType)
	rCPU := strings.TrimSpace(resourceCPU)
	arch := strings.TrimSpace(architecture)

	// 1. Exact match by RouterBOARD product code
	if pCode != "" {
		if exact, ok := socByProductCode[pCode]; ok {
			return CpuIdentity{Model: exact, Exact: true, Platform: platform}
		}
	}

	// 2. Direct match by board name
	if bName != "" {
		if exact, ok := socByBoardName[bName]; ok {
			return CpuIdentity{Model: exact, Exact: true, Platform: platform}
		}
		// Also check normalized board name
		norm := strings.ReplaceAll(bName, "^", "")
		if exact, ok := socByBoardName[norm]; ok {
			return CpuIdentity{Model: exact, Exact: true, Platform: platform}
		}
	}

	// 3. Prefix matching against known product codes
	if pCode != "" {
		for k, v := range socByProductCode {
			if strings.HasPrefix(pCode, k) || strings.HasPrefix(k, pCode) {
				return CpuIdentity{Model: v, Exact: true, Platform: platform}
			}
		}
	}
	if bName != "" {
		for k, v := range socByProductCode {
			if strings.HasPrefix(bName, k) || strings.HasPrefix(k, bName) {
				return CpuIdentity{Model: v, Exact: true, Platform: platform}
			}
		}
	}

	// 4. Fallback to bootloader platform family
	if platform != "" {
		return CpuIdentity{Model: platform, Exact: false, Platform: platform}
	}

	// 5. x86 and CHR report the actual CPU part in resource_cpu
	if rCPU != "" && !strings.EqualFold(rCPU, "arm64") && !strings.EqualFold(rCPU, "arm") && !strings.EqualFold(rCPU, "mips") {
		return CpuIdentity{Model: rCPU, Exact: true, Platform: ""}
	}

	// 6. Last resort architecture
	if arch != "" {
		return CpuIdentity{Model: arch, Exact: false, Platform: ""}
	}

	return CpuIdentity{Model: "Unknown", Exact: false, Platform: ""}
}
