package routeros

import (
	"strconv"
	"strings"
)

// FlexibleInt64 unmarshals both string numbers ("12345") and JSON numeric numbers (12345).
type FlexibleInt64 int64

func (f *FlexibleInt64) UnmarshalJSON(b []byte) error {
	s := strings.Trim(string(b), "\"")
	if s == "" || s == "null" {
		*f = 0
		return nil
	}
	v, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		*f = 0
		return nil
	}
	*f = FlexibleInt64(v)
	return nil
}

func (f FlexibleInt64) MarshalJSON() ([]byte, error) {
	return []byte(strconv.FormatInt(int64(f), 10)), nil
}

func (f FlexibleInt64) Int64() int64 {
	return int64(f)
}

func (f FlexibleInt64) String() string {
	return strconv.FormatInt(int64(f), 10)
}

// FlexibleBool unmarshals both boolean (true/false) and string ("true"/"false"/"yes"/"no").
type FlexibleBool bool

func (f *FlexibleBool) UnmarshalJSON(b []byte) error {
	s := strings.ToLower(strings.Trim(string(b), "\""))
	if s == "true" || s == "yes" || s == "1" {
		*f = true
		return nil
	}
	*f = false
	return nil
}

func (f FlexibleBool) MarshalJSON() ([]byte, error) {
	if f {
		return []byte("true"), nil
	}
	return []byte("false"), nil
}

func (f FlexibleBool) Bool() bool {
	return bool(f)
}

// Resource represents /system/resource
type Resource struct {
	Uptime           string  `json:"uptime"`
	Version          string  `json:"version"`
	CPULoad          string  `json:"cpu-load"`
	FreeMemory       string  `json:"free-memory"`
	TotalMemory      string  `json:"total-memory"`
	CPUCount         string  `json:"cpu-count"`
	CPUFrequency     string  `json:"cpu-frequency"`
	ArchitectureName string  `json:"architecture-name"`
	BoardName        string  `json:"board-name"`
	Platform         string  `json:"platform"`
}

// HealthItem represents an entry in /system/health
type HealthItem struct {
	Name  string `json:"name"`
	Value string `json:"value"`
	Type  string `json:"type"`
}

// RouterBoard represents /system/routerboard
type RouterBoard struct {
	RouterBoard     string `json:"routerboard"`
	Model           string `json:"model"`
	SerialNumber    string `json:"serial-number"`
	CurrentFirmware string `json:"current-firmware"`
	UpgradeFirmware string `json:"upgrade-firmware"`
}

// Interface represents /interface
type Interface struct {
	ID        string `json:".id"`
	Name      string `json:"name"`
	Type      string `json:"type"`
	Running   string `json:"running"`
	Disabled  string `json:"disabled"`
	Comment   string `json:"comment,omitempty"`
	RxByte    string `json:"rx-byte"`
	TxByte    string `json:"tx-byte"`
	RxPacket  string `json:"rx-packet"`
	TxPacket  string `json:"tx-packet"`
	ActualMTU string `json:"actual-mtu,omitempty"`
}

// DHCPLease represents /ip/dhcp-server/lease
type DHCPLease struct {
	ID           string       `json:".id"`
	Address      string       `json:"address"`
	MacAddress   string       `json:"mac-address"`
	HostName     string       `json:"host-name,omitempty"`
	Status       string       `json:"status"`
	ExpiresAfter string       `json:"expires-after,omitempty"`
	Comment      string       `json:"comment,omitempty"`
	Dynamic      FlexibleBool `json:"dynamic,omitempty"`
	Disabled     FlexibleBool `json:"disabled,omitempty"`
}

// ARPEntry represents /ip/arp
type ARPEntry struct {
	ID         string       `json:".id"`
	Address    string       `json:"address"`
	MacAddress string       `json:"mac-address"`
	Interface  string       `json:"interface"`
	Complete   string       `json:"complete"`
	Dynamic    FlexibleBool `json:"dynamic,omitempty"`
	Disabled   FlexibleBool `json:"disabled,omitempty"`
	Comment    string       `json:"comment,omitempty"`
}

// WiFiRegistration represents /interface/wifi/registration-table or /interface/wireless/registration-table
type WiFiRegistration struct {
	ID         string `json:".id"`
	MacAddress string `json:"mac-address"`
	Interface  string `json:"interface"`
	Signal     string `json:"signal,omitempty"` // e.g. "-65" or "-65dBm"
	SignalAvg  string `json:"signal-avg,omitempty"`
	TxRate     string `json:"tx-rate,omitempty"`
	RxRate     string `json:"rx-rate,omitempty"`
	Uptime     string `json:"uptime,omitempty"`
	RadioName  string `json:"radio-name,omitempty"`
}

// SimpleQueue represents /queue/simple
type SimpleQueue struct {
	ID         string       `json:".id,omitempty"`
	Name       string       `json:"name"`
	Target     string       `json:"target"`
	MaxLimit   string       `json:"max-limit"` // "upload/download" in bps, e.g. "10M/50M" or "0/0"
	LimitAt    string       `json:"limit-at,omitempty"`
	Priority   string       `json:"priority,omitempty"`
	Disabled   FlexibleBool `json:"disabled,omitempty"`
	Parent     string       `json:"parent,omitempty"`
	Comment    string       `json:"comment,omitempty"`
	Bytes      string       `json:"bytes,omitempty"` // "upload/download" cumulative
	TotalBytes string       `json:"total-bytes,omitempty"`
	PacketRate string       `json:"rate,omitempty"`
}

// MangleRule represents /ip/firewall/mangle
type MangleRule struct {
	ID             string        `json:".id,omitempty"`
	Chain          string        `json:"chain"`
	Action         string        `json:"action"`
	SrcAddress     string        `json:"src-address,omitempty"`
	DstAddress     string        `json:"dst-address,omitempty"`
	SrcAddressList string        `json:"src-address-list,omitempty"`
	DstAddressList string        `json:"dst-address-list,omitempty"`
	InInterface    string        `json:"in-interface,omitempty"`
	OutInterface   string        `json:"out-interface,omitempty"`
	Comment        string        `json:"comment,omitempty"`
	Bytes          FlexibleInt64 `json:"bytes,omitempty"`
	Packets        FlexibleInt64 `json:"packets,omitempty"`
	Disabled       FlexibleBool  `json:"disabled,omitempty"`
}

// FirewallConnection represents an entry in /ip/firewall/connection
type FirewallConnection struct {
	ID              string        `json:".id"`
	Protocol        string        `json:"protocol"`
	SrcAddress      string        `json:"src-address"`
	DstAddress      string        `json:"dst-address"`
	ReplySrcAddress string        `json:"reply-src-address,omitempty"`
	ReplyDstAddress string        `json:"reply-dst-address,omitempty"`
	TCPState        string        `json:"tcp-state,omitempty"`
	OrigRate        FlexibleInt64 `json:"orig-rate,omitempty"`
	ReplRate        FlexibleInt64 `json:"repl-rate,omitempty"`
	OrigBytes       FlexibleInt64 `json:"orig-bytes,omitempty"`
	ReplBytes       FlexibleInt64 `json:"repl-bytes,omitempty"`
	Timeout         string        `json:"timeout,omitempty"`
	FastTrack       FlexibleBool  `json:"fasttrack,omitempty"`
}

// FilterRule represents /ip/firewall/filter
type FilterRule struct {
	ID             string `json:".id,omitempty"`
	Chain          string `json:"chain"`
	Action         string `json:"action"`
	SrcAddress     string `json:"src-address,omitempty"`
	DstAddress     string `json:"dst-address,omitempty"`
	SrcAddressList string `json:"src-address-list,omitempty"`
	DstAddressList string `json:"dst-address-list,omitempty"`
	Protocol       string `json:"protocol,omitempty"`
	DstPort        string `json:"dst-port,omitempty"`
	Comment        string `json:"comment,omitempty"`
	Disabled       string `json:"disabled"`
}

// AddressListEntry represents /ip/firewall/address-list
type AddressListEntry struct {
	ID       string `json:".id,omitempty"`
	List     string `json:"list"`
	Address  string `json:"address"`
	Comment  string `json:"comment,omitempty"`
	Disabled string `json:"disabled"`
}

// LogEntry represents /log
type LogEntry struct {
	ID      string `json:".id"`
	Time    string `json:"time"`
	Topics  string `json:"topics"`
	Message string `json:"message"`
}

// IPAddress represents an address entry in /ip/address
type IPAddress struct {
	ID        string `json:".id,omitempty"`
	Address   string `json:"address"`
	Network   string `json:"network,omitempty"`
	Interface string `json:"interface"`
	Dynamic   string `json:"dynamic,omitempty"`
	Disabled  string `json:"disabled,omitempty"`
	Comment   string `json:"comment,omitempty"`
}
