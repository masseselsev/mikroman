package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/masseselsev/mikroman/internal/db"
	"github.com/masseselsev/mikroman/internal/routeros"
)

type ContainerHandler struct {
	database *db.DB
	client   *routeros.Client
}

func NewContainerHandler(database *db.DB, client *routeros.Client) *ContainerHandler {
	return &ContainerHandler{database: database, client: client}
}

func (h *ContainerHandler) getRouterClient(ctx context.Context, routerID int) (*routeros.Client, *db.Router, error) {
	router, err := h.database.GetRouter(routerID)
	if err != nil || router == nil {
		return nil, nil, err
	}
	defaultRouter, _ := h.database.GetDefaultRouter()
	if defaultRouter != nil && defaultRouter.ID == routerID && h.client != nil {
		return h.client, router, nil
	}
	if defaultRouter == nil && h.client != nil {
		routers, _ := h.database.GetRouters()
		if len(routers) <= 1 {
			return h.client, router, nil
		}
	}

	c, err := routeros.NewClient(routeros.Config{
		Host:      router.Host,
		Port:      router.Port,
		Username:  router.Username,
		Password:  router.Password,
		UseSSL:    router.UseSSL,
		SSLVerify: router.SSLVerify,
		CACert:    router.CACert.String,
		Timeout:   5 * time.Second,
	})
	return c, router, err
}

type ContainerSupportDTO struct {
	Installed bool    `json:"installed"`
	Enabled   bool    `json:"enabled"`
	Version   *string `json:"version,omitempty"`
	Status    string  `json:"status"` // ready | not_installed | disabled | unreachable
	Message   string  `json:"message,omitempty"`
}

type ContainerDTO struct {
	ID                 string   `json:"id"`
	Name               *string  `json:"name,omitempty"`
	Tag                *string  `json:"tag,omitempty"`
	Status             *string  `json:"status,omitempty"`
	Running            *bool    `json:"running,omitempty"`
	OS                 *string  `json:"os,omitempty"`
	Arch               *string  `json:"arch,omitempty"`
	Interface          *string  `json:"interface,omitempty"`
	RootDir            *string  `json:"root_dir,omitempty"`
	Mounts             *string  `json:"mounts,omitempty"`
	Envlist            *string  `json:"envlist,omitempty"`
	Cmd                *string  `json:"cmd,omitempty"`
	Entrypoint         *string  `json:"entrypoint,omitempty"`
	Hostname           *string  `json:"hostname,omitempty"`
	Logging            *bool    `json:"logging,omitempty"`
	StartOnBoot        *bool    `json:"start_on_boot,omitempty"`
	Comment            *string  `json:"comment,omitempty"`
	CPUUsagePct        *float64 `json:"cpu_usage_pct,omitempty"`
	MemoryCurrentBytes *int64   `json:"memory_current_bytes,omitempty"`
	MemoryHighBytes    *int64   `json:"memory_high_bytes,omitempty"`
	MemoryMaxBytes     *int64   `json:"memory_max_bytes,omitempty"`
	DiskSizeBytes      *int64   `json:"disk_size_bytes,omitempty"`
	RestartCount       *int64   `json:"restart_count,omitempty"`
	StopTimeSeconds    *int64   `json:"stop_time_seconds,omitempty"`
}

type ContainerHostDTO struct {
	CPULoadPct       *int    `json:"cpu_load_pct,omitempty"`
	TotalMemoryBytes *int64  `json:"total_memory_bytes,omitempty"`
	FreeMemoryBytes  *int64  `json:"free_memory_bytes,omitempty"`
	Uptime           *string `json:"uptime,omitempty"`
}

type ContainerMountDTO struct {
	ID   string  `json:"id"`
	Name *string `json:"name,omitempty"`
	Src  *string `json:"src,omitempty"`
	Dst  *string `json:"dst,omitempty"`
}

type ContainerEnvDTO struct {
	ID    string  `json:"id"`
	Name  *string `json:"name,omitempty"`
	Key   *string `json:"key,omitempty"`
	Value *string `json:"value,omitempty"`
}

type ContainerConfigDTO struct {
	TmpDir             *string `json:"tmpdir,omitempty"`
	RegistryURL        *string `json:"registry_url,omitempty"`
	RAMHigh            *string `json:"ram_high,omitempty"`
	LayerDir           *string `json:"layer_dir,omitempty"`
	MemoryCurrentBytes *int64  `json:"memory_current_bytes,omitempty"`
	MemoryHighBytes    *int64  `json:"memory_high_bytes,omitempty"`
	MemoryMaxBytes     *int64  `json:"memory_max_bytes,omitempty"`
}

type ContainerDiskDTO struct {
	Slot                 string   `json:"slot"`
	Parent               *string  `json:"parent,omitempty"`
	IsPartition          bool     `json:"is_partition"`
	Model                *string  `json:"model,omitempty"`
	Serial               *string  `json:"serial,omitempty"`
	FS                   *string  `json:"fs,omitempty"`
	MountPoint           *string  `json:"mount_point,omitempty"`
	Mounted              bool     `json:"mounted"`
	ReadOnly             bool     `json:"read_only"`
	Formatting           bool     `json:"formatting"`
	Disabled             bool     `json:"disabled"`
	SizeBytes            *int64   `json:"size_bytes,omitempty"`
	FreeBytes            *int64   `json:"free_bytes,omitempty"`
	UsedPct              *int     `json:"used_pct,omitempty"`
	TemperatureC         *int     `json:"temperature_c,omitempty"`
	IOErrors             *int     `json:"io_errors,omitempty"`
	UsableForContainers  bool     `json:"usable_for_containers"`
	Formatable           bool     `json:"formatable"`
	Note                 string   `json:"note"`
}

type ContainerStorageDTO struct {
	Ready         bool               `json:"ready"`
	StorageDir    string             `json:"storage_dir"`
	MatchedSlot   *string            `json:"matched_slot,omitempty"`
	FreeBytes     *int64             `json:"free_bytes,omitempty"`
	SizeBytes     *int64             `json:"size_bytes,omitempty"`
	FS            *string            `json:"fs,omitempty"`
	Problems      []string           `json:"problems"`
	Warnings      []string           `json:"warnings"`
	Disks         []ContainerDiskDTO `json:"disks"`
	RequiredBytes int64              `json:"required_bytes"`
}

type ContainerOverviewDTO struct {
	Support    ContainerSupportDTO `json:"support"`
	Host       ContainerHostDTO    `json:"host"`
	Containers []ContainerDTO      `json:"containers"`
	Mounts     []ContainerMountDTO `json:"mounts"`
	Envs       []ContainerEnvDTO   `json:"envs"`
	Config     ContainerConfigDTO  `json:"config"`
	Storage    ContainerStorageDTO `json:"storage"`
}

type ContainerSetupStepDTO struct {
	Key     string `json:"key"`
	Action  string `json:"action"`
	Detail  string `json:"detail"`
	Applied bool   `json:"applied"`
}

type ContainerSetupPlanDTO struct {
	OK          bool                    `json:"ok"`
	StorageDir  string                  `json:"storage_dir"`
	GatewayIP   string                  `json:"gateway_ip"`
	ContainerIP string                  `json:"container_ip"`
	Storage     ContainerStorageDTO     `json:"storage"`
	Steps       []ContainerSetupStepDTO `json:"steps"`
	Blockers    []string                `json:"blockers"`
}

func (h *ContainerHandler) probeSupport(ctx context.Context, client *routeros.Client) ContainerSupportDTO {
	pkgs, err := client.GetPackages(ctx)
	if err != nil {
		return ContainerSupportDTO{
			Status:  "unreachable",
			Message: "Could not query the router for installed packages: " + err.Error(),
		}
	}

	var foundPkg *routeros.Package
	for _, p := range pkgs {
		if strings.EqualFold(p.Name, "container") {
			pkgCopy := p
			foundPkg = &pkgCopy
			break
		}
	}

	if foundPkg == nil {
		return ContainerSupportDTO{
			Installed: false,
			Enabled:   false,
			Status:    "not_installed",
			Message:   "The 'container' package is not installed. Download the extra-packages bundle for this RouterOS version and architecture, upload container.npk, and reboot.",
		}
	}

	disabled := routeros.AsBool(foundPkg.Disabled)
	if disabled {
		ver := foundPkg.Version
		return ContainerSupportDTO{
			Installed: true,
			Enabled:   false,
			Version:   &ver,
			Status:    "disabled",
			Message:   "The container package is installed but disabled. Enable it and reboot.",
		}
	}

	ver := foundPkg.Version
	return ContainerSupportDTO{
		Installed: true,
		Enabled:   true,
		Version:   &ver,
		Status:    "ready",
	}
}

func (h *ContainerHandler) List(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	routerID, _ := strconv.Atoi(idStr)

	client, router, err := h.getRouterClient(r.Context(), routerID)
	if err != nil || router == nil {
		WriteError(w, http.StatusNotFound, "Router not found")
		return
	}

	support := h.probeSupport(r.Context(), client)
	overview := ContainerOverviewDTO{
		Support:    support,
		Containers: []ContainerDTO{},
		Mounts:     []ContainerMountDTO{},
		Envs:       []ContainerEnvDTO{},
		Storage: ContainerStorageDTO{
			Problems: []string{},
			Warnings: []string{},
			Disks:    []ContainerDiskDTO{},
		},
	}

	if support.Status != "ready" {
		WriteJSON(w, http.StatusOK, APIResponse{Success: true, Data: overview})
		return
	}

	// Fetch host resource
	if res, err := client.GetSystemResource(r.Context()); err == nil && res != nil {
		cpuLoad, _ := strconv.Atoi(res.CPULoad)
		freeMem, _ := strconv.ParseInt(res.FreeMemory, 10, 64)
		totalMem, _ := strconv.ParseInt(res.TotalMemory, 10, 64)
		overview.Host = ContainerHostDTO{
			CPULoadPct:       &cpuLoad,
			TotalMemoryBytes: &totalMem,
			FreeMemoryBytes:  &freeMem,
			Uptime:           &res.Uptime,
		}
	}

	// Fetch containers
	if ctrs, err := client.GetContainers(r.Context()); err == nil {
		for _, c := range ctrs {
			var running *bool
			if c.Running != "" {
				rBool := routeros.AsBool(c.Running)
				running = &rBool
			}
			var logging *bool
			if c.Logging != "" {
				lBool := routeros.AsBool(c.Logging)
				logging = &lBool
			}
			var startBoot *bool
			if c.StartOnBoot != "" {
				sBool := routeros.AsBool(c.StartOnBoot)
				startBoot = &sBool
			}

			mountsVal := c.Mountlists
			if mountsVal == "" {
				mountsVal = c.Mounts
			}
			envsVal := c.Envlists
			if envsVal == "" {
				envsVal = c.Envlist
			}

			overview.Containers = append(overview.Containers, ContainerDTO{
				ID:                 c.ID,
				Name:               strPtr(c.Name),
				Tag:                strPtr(c.Tag),
				Status:             strPtr(c.Status),
				Running:            running,
				OS:                 strPtr(c.OS),
				Arch:               strPtr(c.Arch),
				Interface:          strPtr(c.Interface),
				RootDir:            strPtr(c.RootDir),
				Mounts:             strPtr(mountsVal),
				Envlist:            strPtr(envsVal),
				Cmd:                strPtr(c.Cmd),
				Entrypoint:         strPtr(c.Entrypoint),
				Hostname:           strPtr(c.Hostname),
				Logging:            logging,
				StartOnBoot:        startBoot,
				Comment:            strPtr(c.Comment),
				CPUUsagePct:        routeros.AsFloat(c.CPUUsage),
				MemoryCurrentBytes: routeros.AsCount(c.MemoryCurrent),
				MemoryHighBytes:    routeros.AsCount(c.MemoryHigh),
				MemoryMaxBytes:     routeros.AsCount(c.MemoryMax),
				DiskSizeBytes:      routeros.AsCount(c.ContainerSize),
				RestartCount:       routeros.AsCount(c.RestartCount),
				StopTimeSeconds:    routeros.AsCount(c.StopTime),
			})
		}
	}

	// Fetch mounts
	if mounts, err := client.GetContainerMounts(r.Context()); err == nil {
		for _, m := range mounts {
			name := m.List
			if name == "" {
				name = m.Name
			}
			overview.Mounts = append(overview.Mounts, ContainerMountDTO{
				ID:   m.ID,
				Name: strPtr(name),
				Src:  strPtr(m.Src),
				Dst:  strPtr(m.Dst),
			})
		}
	}

	// Fetch envs
	if envs, err := client.GetContainerEnvs(r.Context()); err == nil {
		for _, e := range envs {
			name := e.List
			if name == "" {
				name = e.Name
			}
			overview.Envs = append(overview.Envs, ContainerEnvDTO{
				ID:    e.ID,
				Name:  strPtr(name),
				Key:   strPtr(e.Key),
				Value: strPtr(e.Value),
			})
		}
	}

	// Fetch config
	if cfg, err := client.GetContainerConfig(r.Context()); err == nil && cfg != nil {
		overview.Config = ContainerConfigDTO{
			TmpDir:      strPtr(cfg.TmpDir),
			RegistryURL: strPtr(cfg.RegistryURL),
			RAMHigh:     strPtr(cfg.RAMHigh),
		}
	}

	WriteJSON(w, http.StatusOK, APIResponse{Success: true, Data: overview})
}

func (h *ContainerHandler) Action(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	routerID, _ := strconv.Atoi(idStr)
	containerID := chi.URLParam(r, "containerId")
	action := chi.URLParam(r, "action")

	client, router, err := h.getRouterClient(r.Context(), routerID)
	if err != nil || router == nil {
		WriteError(w, http.StatusNotFound, "Router not found")
		return
	}

	support := h.probeSupport(r.Context(), client)
	if support.Status != "ready" {
		msg := support.Message
		if msg == "" {
			msg = "Containers are not available on this router"
		}
		WriteError(w, http.StatusConflict, msg)
		return
	}

	if err := client.RunContainerAction(r.Context(), containerID, action); err != nil {
		WriteError(w, http.StatusBadRequest, fmt.Sprintf("Failed to run %s: %v", action, err))
		return
	}

	msg := fmt.Sprintf("Container %s dispatched", action)
	WriteJSON(w, http.StatusOK, APIResponse{Success: true, Data: true, Message: &msg})
}

type ContainerCreateRequest struct {
	RemoteImage string  `json:"remote_image"`
	Interface   string  `json:"interface"`
	RootDir     *string `json:"root_dir,omitempty"`
	Hostname    *string `json:"hostname,omitempty"`
	Cmd         *string `json:"cmd,omitempty"`
	Entrypoint  *string `json:"entrypoint,omitempty"`
	Mounts      *string `json:"mounts,omitempty"`
	Envlist     *string `json:"envlist,omitempty"`
	StartOnBoot bool    `json:"start_on_boot"`
	Logging     bool    `json:"logging"`
	Comment     *string `json:"comment,omitempty"`
}

func (h *ContainerHandler) Create(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	routerID, _ := strconv.Atoi(idStr)

	client, router, err := h.getRouterClient(r.Context(), routerID)
	if err != nil || router == nil {
		WriteError(w, http.StatusNotFound, "Router not found")
		return
	}

	support := h.probeSupport(r.Context(), client)
	if support.Status != "ready" {
		WriteError(w, http.StatusConflict, "Containers are not available on this router")
		return
	}

	var req ContainerCreateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, "Invalid request body")
		return
	}

	payload := map[string]interface{}{
		"remote-image":  req.RemoteImage,
		"interface":     req.Interface,
		"logging":       fmt.Sprintf("%t", req.Logging),
		"start-on-boot": fmt.Sprintf("%t", req.StartOnBoot),
	}
	if req.RootDir != nil && *req.RootDir != "" {
		payload["root-dir"] = *req.RootDir
	}
	if req.Hostname != nil && *req.Hostname != "" {
		payload["hostname"] = *req.Hostname
	}
	if req.Cmd != nil && *req.Cmd != "" {
		payload["cmd"] = *req.Cmd
	}
	if req.Entrypoint != nil && *req.Entrypoint != "" {
		payload["entrypoint"] = *req.Entrypoint
	}
	if req.Mounts != nil && *req.Mounts != "" {
		payload["mountlists"] = *req.Mounts
	}
	if req.Envlist != nil && *req.Envlist != "" {
		payload["envlists"] = *req.Envlist
	}
	if req.Comment != nil && *req.Comment != "" {
		payload["comment"] = *req.Comment
	}

	if err := client.CreateContainer(r.Context(), payload); err != nil {
		WriteError(w, http.StatusBadRequest, "Failed to create container: "+err.Error())
		return
	}

	msg := "Container created"
	WriteJSON(w, http.StatusCreated, APIResponse{Success: true, Data: true, Message: &msg})
}

func (h *ContainerHandler) Storage(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	routerID, _ := strconv.Atoi(idStr)
	storageDir := r.URL.Query().Get("storage_dir")

	client, router, err := h.getRouterClient(r.Context(), routerID)
	if err != nil || router == nil {
		WriteError(w, http.StatusNotFound, "Router not found")
		return
	}

	disks, _ := client.GetDisks(r.Context())
	var diskDTOs []ContainerDiskDTO

	ready := false
	var matchedSlot *string
	var freeBytes, sizeBytes *int64
	var fs *string
	var problems, warnings []string
	if problems == nil {
		problems = []string{}
	}
	if warnings == nil {
		warnings = []string{}
	}

	for _, d := range disks {
		free := routeros.AsCount(d.Free)
		sz := routeros.AsCount(d.Size)
		isPart := strings.Contains(d.Name, "part")
		readOnly := routeros.AsBool(d.ReadOnly)
		formatting := strings.EqualFold(d.Status, "formatting")
		fsStr := d.FS
		usable := (fsStr == "ext4" || fsStr == "ext3") && !readOnly && !formatting
		formatable := !readOnly && !formatting

		note := ""
		if usable {
			note = fmt.Sprintf("usable for containers (%s)", d.Name)
		} else if readOnly {
			note = "read only filesystem"
		} else if fsStr == "ntfs" || fsStr == "fat32" {
			note = fmt.Sprintf("%s not suitable for containers (ext4 required)", fsStr)
		} else if fsStr == "" || fsStr == "-" {
			note = "no filesystem (format required)"
		}

		dto := ContainerDiskDTO{
			Slot:                d.Name,
			Parent:              nil,
			IsPartition:         isPart,
			Model:               strPtr(d.Type),
			Serial:              nil,
			FS:                  strPtr(fsStr),
			MountPoint:          strPtr("/" + d.Name),
			Mounted:             true,
			ReadOnly:            readOnly,
			Formatting:          formatting,
			Disabled:            false,
			SizeBytes:           sz,
			FreeBytes:           free,
			UsableForContainers: usable,
			Formatable:          formatable,
			Note:                note,
		}
		diskDTOs = append(diskDTOs, dto)

		if storageDir != "" && strings.Trim(storageDir, "/") == d.Name {
			slotCopy := d.Name
			matchedSlot = &slotCopy
			freeBytes = free
			sizeBytes = sz
			fs = strPtr(fsStr)
			if usable {
				ready = true
			} else {
				problems = append(problems, note)
			}
		}
	}

	if diskDTOs == nil {
		diskDTOs = []ContainerDiskDTO{}
	}

	result := ContainerStorageDTO{
		Ready:         ready,
		StorageDir:    storageDir,
		MatchedSlot:   matchedSlot,
		FreeBytes:     freeBytes,
		SizeBytes:     sizeBytes,
		FS:            fs,
		Problems:      problems,
		Warnings:      warnings,
		Disks:         diskDTOs,
		RequiredBytes: 400 * 1024 * 1024,
	}

	WriteJSON(w, http.StatusOK, APIResponse{Success: true, Data: result})
}

type ContainerFormatRequest struct {
	Slot string `json:"slot"`
}

func (h *ContainerHandler) Format(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	routerID, _ := strconv.Atoi(idStr)

	client, router, err := h.getRouterClient(r.Context(), routerID)
	if err != nil || router == nil {
		WriteError(w, http.StatusNotFound, "Router not found")
		return
	}

	var req ContainerFormatRequest
	_ = json.NewDecoder(r.Body).Decode(&req)
	slot := strings.TrimSpace(req.Slot)
	if slot == "" {
		WriteError(w, http.StatusBadRequest, "Missing slot to format")
		return
	}

	if err := client.FormatDisk(r.Context(), slot); err != nil {
		WriteError(w, http.StatusBadRequest, "Format failed: "+err.Error())
		return
	}

	msg := "Format started on " + slot
	WriteJSON(w, http.StatusOK, APIResponse{Success: true, Data: true, Message: &msg})
}

type ContainerSetupRequest struct {
	StorageDir string `json:"storage_dir"`
	Image      string `json:"image"`
}

func (h *ContainerHandler) SetupPlan(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	routerID, _ := strconv.Atoi(idStr)

	client, router, err := h.getRouterClient(r.Context(), routerID)
	if err != nil || router == nil {
		WriteError(w, http.StatusNotFound, "Router not found")
		return
	}

	support := h.probeSupport(r.Context(), client)
	if support.Status != "ready" {
		WriteError(w, http.StatusConflict, support.Message)
		return
	}

	var req ContainerSetupRequest
	_ = json.NewDecoder(r.Body).Decode(&req)

	plan := ContainerSetupPlanDTO{
		OK:          true,
		StorageDir:  req.StorageDir,
		GatewayIP:   "172.17.0.1/24",
		ContainerIP: "172.17.0.2/24",
		Storage: ContainerStorageDTO{
			Ready:      true,
			StorageDir: req.StorageDir,
			Problems:   []string{},
			Warnings:   []string{},
			Disks:      []ContainerDiskDTO{},
		},
		Steps: []ContainerSetupStepDTO{
			{Key: "bridge", Action: "create", Detail: "Create bridge-containers for container isolated network"},
			{Key: "veth", Action: "create", Detail: "Create veth-mikroman (172.17.0.2/24)"},
			{Key: "nat", Action: "create", Detail: "Masquerade outbound container traffic"},
			{Key: "mount", Action: "create", Detail: fmt.Sprintf("Mount /%s/mikroman-data to /app/data", req.StorageDir)},
		},
		Blockers: []string{},
	}

	WriteJSON(w, http.StatusOK, APIResponse{Success: true, Data: plan})
}

func (h *ContainerHandler) SetupApply(w http.ResponseWriter, r *http.Request) {
	h.SetupPlan(w, r)
}

func strPtr(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}
