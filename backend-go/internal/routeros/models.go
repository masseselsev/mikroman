package routeros

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
	ID           string `json:".id"`
	Address      string `json:"address"`
	MacAddress   string `json:"mac-address"`
	HostName     string `json:"host-name,omitempty"`
	Status       string `json:"status"`
	ExpiresAfter string `json:"expires-after,omitempty"`
	Comment      string `json:"comment,omitempty"`
	Dynamic      string `json:"dynamic"`
	Disabled     string `json:"disabled"`
}

// ARPEntry represents /ip/arp
type ARPEntry struct {
	ID         string `json:".id"`
	Address    string `json:"address"`
	MacAddress string `json:"mac-address"`
	Interface  string `json:"interface"`
	Complete   string `json:"complete"`
	Dynamic    string `json:"dynamic"`
	Disabled   string `json:"disabled"`
	Comment    string `json:"comment,omitempty"`
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
	ID          string `json:".id,omitempty"`
	Name        string `json:"name"`
	Target      string `json:"target"`
	MaxLimit    string `json:"max-limit"` // "upload/download" in bps, e.g. "10M/50M" or "0/0"
	LimitAt     string `json:"limit-at,omitempty"`
	Priority    string `json:"priority,omitempty"`
	Disabled    string `json:"disabled"`
	Comment     string `json:"comment,omitempty"`
	Bytes       string `json:"bytes,omitempty"` // "upload/download" cumulative
	TotalBytes  string `json:"total-bytes,omitempty"`
	PacketRate  string `json:"rate,omitempty"`
}

// MangleRule represents /ip/firewall/mangle
type MangleRule struct {
	ID             string `json:".id,omitempty"`
	Chain          string `json:"chain"`
	Action         string `json:"action"`
	SrcAddress     string `json:"src-address,omitempty"`
	DstAddress     string `json:"dst-address,omitempty"`
	SrcAddressList string `json:"src-address-list,omitempty"`
	DstAddressList string `json:"dst-address-list,omitempty"`
	InInterface    string `json:"in-interface,omitempty"`
	OutInterface   string `json:"out-interface,omitempty"`
	Comment        string `json:"comment,omitempty"`
	Bytes          string `json:"bytes,omitempty"`
	Packets        string `json:"packets,omitempty"`
	Disabled       string `json:"disabled"`
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
