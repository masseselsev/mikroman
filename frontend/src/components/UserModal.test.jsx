import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithProviders, screen, waitFor, within } from '../test/render';
import { UserModal } from './UserModal';
import { api } from '../api/client';

/**
 * The profile editor's per-device maintenance panel: clear a stale IP, split a
 * wrongly-merged MAC, delete a device. These act immediately against the API,
 * so the tests pin which call each control makes.
 */

vi.mock('../api/client', () => ({
  api: {
    updateDevice: vi.fn().mockResolvedValue({ data: {} }),
    deleteDevice: vi.fn().mockResolvedValue({ data: true }),
    splitDevice: vi.fn().mockResolvedValue({ data: { id: 99 } }),
  },
}));

const baseUser = (devices) => ({
  id: 1,
  name: 'Mark',
  speed_limit: 'unlimited',
  devices,
});

const device = (over = {}) => ({
  id: 10,
  mac_address: 'AA:BB:CC:DD:EE:01',
  ip_address: '192.168.88.50',
  custom_name: 'Pixel-9-Pro-XL',
  hostname: 'Pixel-9-Pro-XL',
  history: [],
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
});

function expandFirstDevice() {
  const row = screen.getByText('Pixel-9-Pro-XL').closest('.list-row-wrap');
  fireEvent.click(within(row).getByRole('button'));
  return row;
}

describe('UserModal device maintenance', () => {
  it('clears a stale IP through updateDevice with an explicit null', async () => {
    renderWithProviders(
      <UserModal user={baseUser([device()])} isOpen onClose={() => {}} onSave={vi.fn()} onDeviceChanged={vi.fn()} />
    );
    const row = expandFirstDevice();
    fireEvent.click(within(row).getByText(/Clear IP/i));
    await waitFor(() => expect(api.updateDevice).toHaveBeenCalledWith(10, { ip_address: null }));
  });

  it('offers Split off only for a MAC in the device history, and calls splitDevice', async () => {
    const dev = device({
      history: [
        { id: 1, mac_address: 'AA:BB:CC:DD:EE:99', event_type: 'mac_rotated', details: 'merged' },
        { id: 2, mac_address: 'AA:BB:CC:DD:EE:01', event_type: 'ip_changed', details: 'ip' },
      ],
    });
    renderWithProviders(
      <UserModal user={baseUser([dev])} isOpen onClose={() => {}} onSave={vi.fn()} onDeviceChanged={vi.fn()} />
    );
    const row = expandFirstDevice();
    expect(within(row).getByText('AA:BB:CC:DD:EE:99')).toBeInTheDocument();
    // the ip_changed row's address equals the current MAC -> not a split candidate
    fireEvent.click(within(row).getByText(/Split off/i));
    await waitFor(() => expect(api.splitDevice).toHaveBeenCalledWith(10, 'AA:BB:CC:DD:EE:99'));
  });

  it('has no Split section when history holds no prior address', () => {
    renderWithProviders(
      <UserModal user={baseUser([device()])} isOpen onClose={() => {}} onSave={vi.fn()} onDeviceChanged={vi.fn()} />
    );
    expandFirstDevice();
    expect(screen.queryByText(/Split off/i)).toBeNull();
  });

  it('deletes a device after confirmation and drops it from the list', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const onChanged = vi.fn();
    renderWithProviders(
      <UserModal user={baseUser([device()])} isOpen onClose={() => {}} onSave={vi.fn()} onDeviceChanged={onChanged} />
    );
    const row = expandFirstDevice();
    fireEvent.click(within(row).getByText(/^Delete$/));
    await waitFor(() => expect(api.deleteDevice).toHaveBeenCalledWith(10));
    await waitFor(() => expect(screen.queryByText('Pixel-9-Pro-XL')).toBeNull());
    expect(onChanged).toHaveBeenCalled();
  });

  it('does not delete when the confirm is dismissed', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderWithProviders(
      <UserModal user={baseUser([device()])} isOpen onClose={() => {}} onSave={vi.fn()} onDeviceChanged={vi.fn()} />
    );
    const row = expandFirstDevice();
    fireEvent.click(within(row).getByText(/^Delete$/));
    expect(api.deleteDevice).not.toHaveBeenCalled();
  });
});
