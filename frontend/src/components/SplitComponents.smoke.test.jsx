import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen } from '../test/render';
import { SetupWizard } from './SetupWizard';
import { RouterStep } from './wizard/RouterStep';
import { TelegramStep } from './wizard/TelegramStep';
import { PreferencesStep } from './wizard/PreferencesStep';
import { OverviewTab } from './analytics/OverviewTab';
import { UsersTab } from './analytics/UsersTab';
import { DevicesTab } from './analytics/DevicesTab';
import { InterfacesTab } from './analytics/InterfacesTab';
import { TrafficAnalytics } from './TrafficAnalytics';

/**
 * Render each component that was split out of SetupWizard and TrafficAnalytics.
 *
 * These exist because of a specific failure mode the build does not catch: JSX
 * referencing a component that was never imported produces a valid bundle and a
 * blank screen. Moving markup between files is exactly when that happens, and
 * every one of these files was moved. Rendering is the only thing that proves
 * the identifiers resolve.
 */

vi.mock('../api/client', () => ({
  api: {
    getBillingCycleConfig: vi.fn().mockResolvedValue({ data: { anchor_day: 1 } }),
    getTrafficAnalytics: vi.fn().mockResolvedValue({
      data: {
        gateway: { total_bytes_in: 90, total_bytes_out: 10, total_bytes: 100, monitored_interfaces: ['ether1'] },
        accounting_health: { status: 'ok', coverage_pct: 100 },
        users: [],
        devices: [],
        interfaces: [],
        timeline: [],
        router_self: { total_bytes: 0, pct_of_total: 0 },
      }
    }),
    testRouterConnection: vi.fn().mockResolvedValue({ data: { success: true } }),
    getSettings: vi.fn().mockResolvedValue({ data: {} }),
  },
}));

const routerForm = {
  name: 'Main Router', host: '192.168.88.1', port: 80,
  use_ssl: false, ssl_verify: false, ca_cert: '', username: '', password: '',
};

const gateway = {
  total_bytes_in: 90, total_bytes_out: 10, total_bytes: 100, monitored_interfaces: ['ether1'],
};
const analyticsUsers = [
  { user_id: 1, user_name: 'Mark', bytes_in: 60, bytes_out: 5, total_bytes: 65, pct_of_total: 65, device_count: 2 },
];
const analyticsDevices = [
  {
    device_id: 3, mac_address: 'AA:BB:CC:DD:EE:01', ip_address: '192.168.88.5',
    custom_name: 'Pixel', hostname: 'Pixel', vendor: 'Google', user_id: 1, user_name: 'Mark',
    bytes_in: 60, bytes_out: 5, total_bytes: 65, pct_of_total: 65,
    speed_limit: 'default', is_paused: false, is_hidden: false,
  },
];
const analyticsInterfaces = [
  {
    interface_name: 'wg0', is_tunnel: true, is_monitored: false,
    bytes_in: 20, bytes_out: 4, total_bytes: 24, pct_of_total: 24,
    cycle_bytes: 40, all_time_bytes: 120,
  },
  {
    interface_name: 'ether1', is_tunnel: false, is_monitored: true,
    bytes_in: 90, bytes_out: 10, total_bytes: 100, pct_of_total: 100,
    cycle_bytes: 200, all_time_bytes: 900,
  },
];
const timeline = [{ record_date: '2026-09-01', bytes_in: 90, bytes_out: 10, total_bytes: 100 }];
const sort = { field: 'total_bytes', dir: 'desc' };

describe('components split out of the wizard', () => {
  it('renders the wizard shell on its first step', () => {
    renderWithProviders(<SetupWizard onComplete={vi.fn()} />);
    expect(screen.getByDisplayValue('192.168.88.1')).toBeInTheDocument();
  });

  it('renders the router step on its own', () => {
    renderWithProviders(
      <RouterStep routerForm={routerForm} setRouterForm={vi.fn()} onNext={vi.fn()} />
    );
    expect(screen.getByDisplayValue('Main Router')).toBeInTheDocument();
  });

  it('renders the telegram step on its own', () => {
    renderWithProviders(
      <TelegramStep
        telegramForm={{ bot_token: 'abc', admin_ids: '1', mode: 'polling' }}
        setTelegramForm={vi.fn()}
        onNext={vi.fn()}
        onBack={vi.fn()}
      />
    );
    expect(screen.getByDisplayValue('abc')).toBeInTheDocument();
  });

  it('renders the preferences step on its own', () => {
    const { container } = renderWithProviders(
      <PreferencesStep saving={false} onFinish={vi.fn()} onBack={vi.fn()} />
    );
    expect(container.querySelectorAll('button').length).toBeGreaterThan(0);
  });
});

describe('components split out of traffic analytics', () => {
  it('renders the overview tab with donuts and a timeline', () => {
    const { container } = renderWithProviders(
      <OverviewTab
        gateway={gateway}
        timeline={timeline}
        users={analyticsUsers}
        devices={analyticsDevices}
      />
    );
    expect(container.querySelectorAll('svg').length).toBeGreaterThan(0);
  });

  it('renders the users tab', () => {
    renderWithProviders(
      <UsersTab users={analyticsUsers} userSort={sort} toggleUserSort={vi.fn()} />
    );
    expect(screen.getByText('Mark')).toBeInTheDocument();
  });

  it('renders the devices tab', () => {
    renderWithProviders(
      <DevicesTab
        users={analyticsUsers}
        filteredDevices={analyticsDevices}
        deviceSort={sort}
        toggleDeviceSort={vi.fn()}
        searchTerm=""
        setSearchTerm={vi.fn()}
        userFilter="all"
        setUserFilter={vi.fn()}
        showHidden={false}
        setShowHidden={vi.fn()}
      />
    );
    expect(screen.getByText('Pixel')).toBeInTheDocument();
  });

  it('renders the interfaces tab, tunnels first', () => {
    renderWithProviders(
      <InterfacesTab interfaces={analyticsInterfaces} sort={sort} toggleSort={vi.fn()} />
    );
    expect(screen.getByText('wg0')).toBeInTheDocument();
    expect(screen.getByText('ether1')).toBeInTheDocument();
    expect(screen.getByText('tunnel')).toBeInTheDocument();
  });

  it('shows an empty-state when no interface traffic is recorded', () => {
    renderWithProviders(
      <InterfacesTab interfaces={[]} sort={sort} toggleSort={vi.fn()} />
    );
    expect(screen.getByText(/No interface traffic/i)).toBeInTheDocument();
  });

  it('offers the hidden-device toggle the table now honours', () => {
    const setShowHidden = vi.fn();
    renderWithProviders(
      <DevicesTab
        users={analyticsUsers}
        filteredDevices={analyticsDevices}
        deviceSort={sort}
        toggleDeviceSort={vi.fn()}
        searchTerm=""
        setSearchTerm={vi.fn()}
        userFilter="all"
        setUserFilter={vi.fn()}
        showHidden={false}
        setShowHidden={setShowHidden}
      />
    );
    screen.getByRole('button', { name: /Show Hidden/i }).click();
    expect(setShowHidden).toHaveBeenCalledWith(true);
  });

  it('renders the complete TrafficAnalytics component without throwing', () => {
    const { container } = renderWithProviders(
      <TrafficAnalytics activeRouter={{ id: 1 }} />
    );
    expect(container.querySelectorAll('.nav-tab').length).toBeGreaterThan(0);
  });
});
