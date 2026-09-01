import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen, waitFor } from '../test/render';
import { SettingsModal } from './SettingsModal';
import { TrafficHistoryModal } from './TrafficHistoryModal';

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
    getQuota: vi.fn().mockResolvedValue({ data: { enabled: false } }),
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
