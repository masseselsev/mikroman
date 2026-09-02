import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen } from '../test/render';
import { TelemetryBar } from './TelemetryBar';
import { api } from '../api/client';

// The bar fires a handful of best-effort config lookups from effects; stub them
// so mounting is cheap and offline.
vi.mock('../api/client', () => ({
  api: {
    getIpLookup: vi.fn(),
    getSettings: vi.fn(),
    getMonitoredInterfacesConfig: vi.fn(),
    getAvailableInterfaces: vi.fn(),
    getLatestSpeedTest: vi.fn(),
    getSpeedTestHistory: vi.fn(),
  },
}));

// afterEach in the shared setup restores mocks, so re-arm the resolved values
// before every test rather than only in the factory.
beforeEach(() => {
  api.getIpLookup.mockResolvedValue({ data: { services: [], default_id: null } });
  api.getSettings.mockResolvedValue({ data: {} });
  api.getMonitoredInterfacesConfig.mockResolvedValue({ data: { selected_interfaces: [] } });
  api.getAvailableInterfaces.mockResolvedValue({ data: [] });
  api.getLatestSpeedTest.mockResolvedValue({ data: null });
  api.getSpeedTestHistory.mockResolvedValue({ data: [] });
});

const router = {
  cpu_load: 5,
  free_memory_mb: 200,
  total_memory_mb: 512,
  temperature: 40,
  wan_rx_bps: 1000,
  wan_tx_bps: 500,
  wan_ip: '10.0.0.2',
  monitored_interfaces: ['ether1'],
  user_count: 3,
  client_device_count: 8,
  active_clients: 5,
};

describe('TelemetryBar client tile', () => {
  it('leads with the profile count and shows online / total devices underneath', () => {
    renderWithProviders(<TelemetryBar router={router} activeRouter={{ id: 1 }} />);

    // Label is now "Users", not "Clients".
    expect(screen.getByText('Users')).toBeInTheDocument();
    // Headline value is the number of profiles.
    expect(screen.getByText('3')).toBeInTheDocument();
    // Sub line carries their devices: online / total.
    expect(screen.getByText('5 / 8 devices')).toBeInTheDocument();
  });

  it('warns loudly on the speed tiles when no WAN interface is selected', () => {
    const noWan = { ...router, monitored_interfaces: [] };
    renderWithProviders(<TelemetryBar router={noWan} activeRouter={{ id: 1 }} />);
    // Both speed tiles carry the same warning sub-line.
    expect(screen.getAllByText(/No WAN selected/i).length).toBeGreaterThanOrEqual(1);
  });

  it('shows the interface list as the WAN sub-line when one is selected', () => {
    renderWithProviders(<TelemetryBar router={router} activeRouter={{ id: 1 }} />);
    expect(screen.getAllByText(/WAN · ether1/).length).toBe(2);
  });

  it('falls back to active_clients when an older frame lacks the new counts', () => {
    const legacy = { ...router };
    delete legacy.user_count;
    delete legacy.client_device_count;
    renderWithProviders(<TelemetryBar router={legacy} activeRouter={{ id: 1 }} />);

    // No user_count -> shows 0 profiles, and the device total falls back to the
    // online count so the tile never reads "5 / 0".
    expect(screen.getByText('5 / 5 devices')).toBeInTheDocument();
  });
});
