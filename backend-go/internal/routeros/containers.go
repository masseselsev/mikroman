package routeros

import (
	"context"
	"fmt"
	"strconv"
	"strings"
)

// Package represents /system/package
type Package struct {
	ID       string       `json:".id"`
	Name     string       `json:"name"`
	Version  string       `json:"version"`
	Disabled FlexibleBool `json:"disabled"`
}

// Container represents /container
type Container struct {
	ID            string       `json:".id"`
	Name          string       `json:"name,omitempty"`
	Tag           string       `json:"tag,omitempty"`
	Status        string       `json:"status,omitempty"`
	Running       FlexibleBool `json:"running,omitempty"`
	OS            string       `json:"os,omitempty"`
	Arch          string       `json:"arch,omitempty"`
	Interface     string       `json:"interface,omitempty"`
	RootDir       string       `json:"root-dir,omitempty"`
	Mounts        string       `json:"mounts,omitempty"`
	Mountlists    string       `json:"mountlists,omitempty"`
	Envlist       string       `json:"envlist,omitempty"`
	Envlists      string       `json:"envlists,omitempty"`
	Cmd           string       `json:"cmd,omitempty"`
	Entrypoint    string       `json:"entrypoint,omitempty"`
	Hostname      string       `json:"hostname,omitempty"`
	Logging       FlexibleBool `json:"logging,omitempty"`
	StartOnBoot   FlexibleBool `json:"start-on-boot,omitempty"`
	Comment       string       `json:"comment,omitempty"`
	CPUUsage      string       `json:"cpu-usage,omitempty"`
	MemoryCurrent string       `json:"memory-current,omitempty"`
	MemoryHigh    string       `json:"memory-high,omitempty"`
	MemoryMax     string       `json:"memory-max,omitempty"`
	ContainerSize string       `json:"container-size,omitempty"`
	RestartCount  string       `json:"restart-count,omitempty"`
	StopTime      string       `json:"stop-time,omitempty"`
}

// ContainerMount represents /container/mounts
type ContainerMount struct {
	ID   string `json:".id"`
	Name string `json:"name,omitempty"`
	List string `json:"list,omitempty"`
	Src  string `json:"src,omitempty"`
	Dst  string `json:"dst,omitempty"`
}

// ContainerEnv represents /container/envs
type ContainerEnv struct {
	ID    string `json:".id"`
	Name  string `json:"name,omitempty"`
	List  string `json:"list,omitempty"`
	Key   string `json:"key,omitempty"`
	Value string `json:"value,omitempty"`
}

// ContainerConfig represents /container/config
type ContainerConfig struct {
	RegistryURL string `json:"registry-url,omitempty"`
	TmpDir      string `json:"tmpdir,omitempty"`
	RAMHigh     string `json:"ram-high,omitempty"`
}

// DiskItem represents /disk
type DiskItem struct {
	ID       string       `json:".id"`
	Name     string       `json:"name,omitempty"`
	Type     string       `json:"type,omitempty"`
	FS       string       `json:"fs,omitempty"`
	Free     string       `json:"free,omitempty"`
	Size     string       `json:"size,omitempty"`
	Status   string       `json:"status,omitempty"`
	ReadOnly FlexibleBool `json:"read-only,omitempty"`
}

// GetPackages queries /system/package
func (c *Client) GetPackages(ctx context.Context) ([]Package, error) {
	var pkgs []Package
	if err := c.Get(ctx, "/system/package", &pkgs); err != nil {
		return nil, err
	}
	return pkgs, nil
}

// GetContainers queries /container
func (c *Client) GetContainers(ctx context.Context) ([]Container, error) {
	var list []Container
	if err := c.Get(ctx, "/container", &list); err != nil {
		if strings.Contains(err.Error(), "404") || strings.Contains(err.Error(), "no such command") {
			return nil, nil
		}
		return nil, err
	}
	return list, nil
}

// GetContainerMounts queries /container/mounts
func (c *Client) GetContainerMounts(ctx context.Context) ([]ContainerMount, error) {
	var list []ContainerMount
	if err := c.Get(ctx, "/container/mounts", &list); err != nil {
		if strings.Contains(err.Error(), "404") || strings.Contains(err.Error(), "no such command") {
			return nil, nil
		}
		return nil, err
	}
	return list, nil
}

// GetContainerEnvs queries /container/envs
func (c *Client) GetContainerEnvs(ctx context.Context) ([]ContainerEnv, error) {
	var list []ContainerEnv
	if err := c.Get(ctx, "/container/envs", &list); err != nil {
		if strings.Contains(err.Error(), "404") || strings.Contains(err.Error(), "no such command") {
			return nil, nil
		}
		return nil, err
	}
	return list, nil
}

// GetContainerConfig queries /container/config
func (c *Client) GetContainerConfig(ctx context.Context) (*ContainerConfig, error) {
	var cfg ContainerConfig
	if err := c.Get(ctx, "/container/config", &cfg); err != nil {
		if strings.Contains(err.Error(), "404") || strings.Contains(err.Error(), "no such command") {
			return &ContainerConfig{}, nil
		}
		return nil, err
	}
	return &cfg, nil
}

// GetDisks queries /disk
func (c *Client) GetDisks(ctx context.Context) ([]DiskItem, error) {
	var list []DiskItem
	if err := c.Get(ctx, "/disk", &list); err != nil {
		if strings.Contains(err.Error(), "404") || strings.Contains(err.Error(), "no such command") {
			return nil, nil
		}
		return nil, err
	}
	return list, nil
}

// RunContainerAction runs start, stop, or remove on a container
func (c *Client) RunContainerAction(ctx context.Context, id string, action string) error {
	switch strings.ToLower(action) {
	case "start":
		return c.Post(ctx, "/container/start", map[string]string{".id": id}, nil)
	case "stop":
		return c.Post(ctx, "/container/stop", map[string]string{".id": id}, nil)
	case "remove":
		return c.Delete(ctx, "/container/"+id)
	default:
		return fmt.Errorf("unsupported container action: %s", action)
	}
}

// CreateContainer creates a container via PUT /container
func (c *Client) CreateContainer(ctx context.Context, payload map[string]interface{}) error {
	return c.Put(ctx, "/container", payload, nil)
}

// FormatDisk formats a disk partition via POST /disk/format-drive
func (c *Client) FormatDisk(ctx context.Context, id string) error {
	return c.Post(ctx, "/disk/format-drive", map[string]string{".id": id}, nil)
}

// AsBool parses RouterOS boolean string
func AsBool(val string) bool {
	v := strings.ToLower(strings.TrimSpace(val))
	return v == "true" || v == "yes" || v == "1"
}

// AsCount parses RouterOS count string into *int64
func AsCount(val string) *int64 {
	v := strings.TrimSpace(val)
	if v == "" || v == "none" || v == "-" || v == "unlimited" || v == "auto" {
		return nil
	}
	if n, err := strconv.ParseInt(v, 10, 64); err == nil {
		return &n
	}
	if f, err := strconv.ParseFloat(v, 64); err == nil {
		n := int64(f)
		return &n
	}
	return nil
}

// AsFloat parses RouterOS float string into *float64
func AsFloat(val string) *float64 {
	v := strings.TrimSpace(val)
	if v == "" || v == "none" || v == "-" || v == "unlimited" {
		return nil
	}
	if f, err := strconv.ParseFloat(v, 64); err == nil {
		return &f
	}
	return nil
}

