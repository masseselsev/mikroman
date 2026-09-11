package routeros

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"testing"
	"time"
)

func TestUnmarshalFlexible(t *testing.T) {
	// Test single object unmarshaling into a slice
	singleJSON := []byte(`{"name": "ether1", "type": "ether"}`)
	var ifaces []Interface
	if err := unmarshalFlexible(singleJSON, &ifaces); err != nil {
		t.Fatalf("failed to unmarshal single object to slice: %v", err)
	}
	if len(ifaces) != 1 || ifaces[0].Name != "ether1" {
		t.Fatalf("expected 1 interface named ether1, got %+v", ifaces)
	}

	// Test array unmarshaling into a slice
	arrayJSON := []byte(`[{"name": "ether1"}, {"name": "ether2"}]`)
	var ifaces2 []Interface
	if err := unmarshalFlexible(arrayJSON, &ifaces2); err != nil {
		t.Fatalf("failed to unmarshal array: %v", err)
	}
	if len(ifaces2) != 2 {
		t.Fatalf("expected 2 interfaces, got %d", len(ifaces2))
	}
}

func TestRouterOSClientEndpoints(t *testing.T) {
	mux := http.NewServeMux()

	// Mock /system/resource
	mux.HandleFunc("/rest/system/resource", func(w http.ResponseWriter, r *http.Request) {
		u, p, ok := r.BasicAuth()
		if !ok || u != "admin" || p != "secret" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(Resource{
			Uptime:   "1d2h3m",
			Version:  "7.15",
			CPULoad:  "15",
			Platform: "MikroTik",
		})
	})

	// Mock /interface
	mux.HandleFunc("/rest/interface", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]Interface{
			{Name: "ether1", Type: "ether", RxByte: "1000", TxByte: "2000"},
			{Name: "bridge", Type: "bridge", RxByte: "5000", TxByte: "6000"},
		})
	})

	// Mock /ip/dhcp-server/lease
	mux.HandleFunc("/rest/ip/dhcp-server/lease", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode([]DHCPLease{
			{Address: "192.168.1.50", MacAddress: "AA:BB:CC:DD:EE:01", HostName: "iPhone"},
		})
	})

	// Mock /queue/simple
	mux.HandleFunc("/rest/queue/simple", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			_ = json.NewEncoder(w).Encode([]SimpleQueue{
				{ID: "*1", Name: "user_queue", Target: "192.168.1.50/32", MaxLimit: "10M/50M"},
			})
		} else if r.Method == http.MethodPut {
			var q SimpleQueue
			_ = json.NewDecoder(r.Body).Decode(&q)
			q.ID = "*new1"
			_ = json.NewEncoder(w).Encode(q)
		}
	})

	// Mock /queue/simple/*
	mux.HandleFunc("/rest/queue/simple/*1", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodDelete {
			w.WriteHeader(http.StatusOK)
		}
	})

	server := httptest.NewServer(mux)
	defer server.Close()

	u, err := url.Parse(server.URL)
	if err != nil {
		t.Fatalf("failed to parse server url: %v", err)
	}

	port, _ := strconv.Atoi(u.Port())
	client, err := NewClient(Config{
		Host:      u.Hostname(),
		Port:      port,
		Username:  "admin",
		Password:  "secret",
		UseSSL:    false,
		SSLVerify: false,
		Timeout:   2 * time.Second,
	})
	if err != nil {
		t.Fatalf("failed to create client: %v", err)
	}

	ctx := context.Background()

	// 1. Test Resource
	res, err := client.GetSystemResource(ctx)
	if err != nil {
		t.Fatalf("failed GetSystemResource: %v", err)
	}
	if res.CPULoad != "15" || res.Version != "7.15" {
		t.Fatalf("unexpected resource: %+v", res)
	}

	// 2. Test Interfaces
	ifaces, err := client.GetInterfaces(ctx)
	if err != nil || len(ifaces) != 2 {
		t.Fatalf("failed GetInterfaces: %v (len: %d)", err, len(ifaces))
	}

	// 3. Test DHCP Leases
	leases, err := client.GetDHCPLeases(ctx)
	if err != nil || len(leases) != 1 {
		t.Fatalf("failed GetDHCPLeases: %v (len: %d)", err, len(leases))
	}
	if leases[0].HostName != "iPhone" {
		t.Fatalf("unexpected lease: %+v", leases[0])
	}

	// 4. Test Queues
	queues, err := client.GetSimpleQueues(ctx)
	if err != nil || len(queues) != 1 {
		t.Fatalf("failed GetSimpleQueues: %v", err)
	}

	newQ := &SimpleQueue{Name: "test_q", Target: "192.168.1.60/32", MaxLimit: "20M/20M"}
	if err := client.CreateSimpleQueue(ctx, newQ); err != nil {
		t.Fatalf("failed CreateSimpleQueue: %v", err)
	}
	if newQ.ID != "*new1" {
		t.Fatalf("expected new ID *new1, got %q", newQ.ID)
	}

	if err := client.DeleteSimpleQueue(ctx, "*1"); err != nil {
		t.Fatalf("failed DeleteSimpleQueue: %v", err)
	}

	// 5. Test Immune IPs
	immunes := client.GetImmuneIPs()
	if !immunes[u.Hostname()] {
		t.Fatalf("expected host %s in immune IPs: %+v", u.Hostname(), immunes)
	}
}
