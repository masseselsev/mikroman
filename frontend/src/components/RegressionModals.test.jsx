import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen, waitFor } from '../test/render';
import { SettingsModal } from './SettingsModal';
import { TrafficHistoryModal } from './TrafficHistoryModal';
import { api } from '../api/client';
import { SECRET_PLACEHOLDER } from '../api/secrets';

/**
 * Three modals went to a blank screen after the multi-router refactor. The
 * existing suites rendered each one already open, which is the one path that
 * did not crash - the faults only showed on a state transition or on a hook
 * that ran past an early return. These tests reproduce those transitions.
 */

vi.mock('../api/client', () => ({
  api: {
    // SettingsModal fans out to these on open.
    getSettings: vi.fn().mockResolvedValue({ data: {} }),
    getRouters: vi.fn().mockResolvedValue({ data: [] }),
    // Part of the same Promise.all: without it the call throws before
    // `setSettings`, and a test that seeds settings silently keeps defaults.
    getArchivedRouters: vi.fn().mockResolvedValue({ data: [] }),
    getQuota: vi.fn().mockResolvedValue({ data: { enabled: false } }),
    getBillingCycleConfig: vi.fn().mockResolvedValue({ data: { anchor_day: 1, anchor_hour: 0, anchor_minute: 0 } }),
    saveBillingCycleConfig: vi.fn().mockResolvedValue({ data: { anchor_day: 1, anchor_hour: 0, anchor_minute: 0 } }),
    getIpLookup: vi.fn().mockResolvedValue({ data: { services: [], enabled_ids: [], default_id: null } }),
    // TrafficHistoryModal fetches history for its target.
    getUserTrafficHistory: vi.fn().mockResolvedValue({
      data: {
        entity_name: 'Alice', total_bytes: 0, total_bytes_in: 0, total_bytes_out: 0,
        daily_average_bytes: 0, peak_bytes: 0, peak_date: null, timeline: [], devices: [],
      },
    }),
    getDeviceTrafficHistory: vi.fn().mockResolvedValue({
      data: {
        entity_name: 'Laptop', total_bytes: 0, total_bytes_in: 0, total_bytes_out: 0,
        daily_average_bytes: 0, peak_bytes: 0, peak_date: null, timeline: [], devices: [],
      },
    }),
  },
}));

describe('SettingsModal', () => {
  beforeEach(() => {
    api.getSettings.mockResolvedValue({ data: {} });
    api.getRouters.mockResolvedValue({ data: [] });
    api.getArchivedRouters.mockResolvedValue({ data: [] });
    api.getQuota.mockResolvedValue({ data: { enabled: false } });
    api.getBillingCycleConfig.mockResolvedValue({ data: { anchor_day: 1, anchor_hour: 0, anchor_minute: 0 } });
    api.saveBillingCycleConfig.mockResolvedValue({ data: { anchor_day: 1, anchor_hour: 0, anchor_minute: 0 } });
    api.getIpLookup.mockResolvedValue({ data: { services: [], enabled_ids: [], default_id: null } });
  });

  it('opens without throwing (add-router state must be declared)', async () => {
    // Before the fix the open effect called setShowAddRouter, which did not
    // exist, and the modal unmounted to a blank screen.
    renderWithProviders(
      <SettingsModal isOpen onClose={() => {}} onReboot={() => {}} onRoutersChanged={() => {}} />
    );
    expect(await screen.findByText('General & Bot')).toBeInTheDocument();
  });

  it('autoOpenAddRouter expands the add-router form on the routers tab', async () => {
    renderWithProviders(
      <SettingsModal
        isOpen
        initialTab="routers"
        autoOpenAddRouter
        onClose={() => {}}
        onReboot={() => {}}
        onRoutersChanged={() => {}}
      />
    );
    // The RouterConnectionForm host-address field only mounts when
    // showAddRouter is true, so finding it proves the setter worked.
    expect(await screen.findByPlaceholderText('192.168.88.1')).toBeInTheDocument();
  });

  it('renders billing cycle reset day and time inputs in SettingsModal', async () => {
    renderWithProviders(
      <SettingsModal isOpen onClose={() => {}} onReboot={() => {}} onRoutersChanged={() => {}} />
    );
    expect(await screen.findByLabelText(/Reset time/i)).toBeInTheDocument();
    expect(screen.getByText(/Billing Reset Day/i)).toBeInTheDocument();
  });

  it('renders traffic accounting scope radio toggle in SettingsModal', async () => {
    renderWithProviders(
      <SettingsModal isOpen onClose={() => {}} onReboot={() => {}} onRoutersChanged={() => {}} />
    );
    expect(await screen.findByText(/Traffic Accounting Scope/i)).toBeInTheDocument();
    expect(screen.getByText(/Monitored WAN \/ Internet only/i)).toBeInTheDocument();
    expect(screen.getByText(/All routed traffic/i)).toBeInTheDocument();
  });
});

describe('TrafficHistoryModal', () => {
  it('survives a closed -> open transition (hooks run in a stable order)', async () => {
    const target = { type: 'user', id: 7, name: 'Alice' };
    const { rerender } = renderWithProviders(
      <TrafficHistoryModal isOpen={false} target={null} onClose={() => {}} />
    );
    // The first render returned null before any of the memo hooks; opening it
    // then ran an extra hook and React tore the tree down.
    rerender(
      <TrafficHistoryModal isOpen target={target} onClose={() => {}} />
    );
    await waitFor(() =>
      expect(screen.getByText('Alice')).toBeInTheDocument()
    );
  });
});

describe('SettingsModal secret field', () => {
  // The shared setup calls `vi.restoreAllMocks()` after every test, which strips
  // the implementations handed to these by the `vi.mock` factory above. So a
  // test here has to re-establish the *whole* fan-out the modal performs on
  // open: setting only the settings response leaves `getRouters()` returning
  // undefined, `Promise.all` throws inside the effect, and the modal silently
  // keeps its defaults — which looks like a broken component, not a broken mock.
  const withSettings = (data) => {
    api.getSettings.mockResolvedValue({ data });
    api.getRouters.mockResolvedValue({ data: [] });
    api.getArchivedRouters.mockResolvedValue({ data: [] });
    api.getQuota.mockResolvedValue({ data: { enabled: false } });
    api.getIpLookup.mockResolvedValue({
      data: { services: [], enabled_ids: [], default_id: null },
    });
  };

  it('labels a masked token as hidden instead of as filled in', async () => {
    // The API never returns the stored token; the field receives the placeholder
    // the server sends. Rendered bare, eight bullets in a password field reads
    // exactly like a real credential, so the hint is the only signal.
    withSettings({ telegram_bot_token: SECRET_PLACEHOLDER });
    renderWithProviders(
      <SettingsModal isOpen onClose={() => {}} onReboot={() => {}} onRoutersChanged={() => {}} />
    );
    expect(await screen.findByText('The stored token is hidden. Type a new one to replace it.'))
      .toBeInTheDocument();
  });

  it('stays quiet when no token is configured', async () => {
    withSettings({});
    renderWithProviders(
      <SettingsModal isOpen onClose={() => {}} onReboot={() => {}} onRoutersChanged={() => {}} />
    );
    await screen.findByText('General & Bot');
    expect(screen.queryByText('The stored token is hidden. Type a new one to replace it.'))
      .not.toBeInTheDocument();
  });
});
