import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithProviders, screen, waitFor } from '../test/render';
import { DeviceInbox } from './DeviceInbox';
import { api } from '../api/client';

/**
 * The manual merge control: fold an unassigned record into a specific existing
 * device rather than attaching it to a person as a record of its own. Needed
 * whenever the rotation heuristics decline but the operator knows it is the
 * same phone. It is destructive - traffic and history are combined and cannot
 * be separated again - so the confirmation is part of the contract.
 */

vi.mock('../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({ data: {} }),
    getMergeSuggestions: vi.fn().mockResolvedValue({ data: [] }),
    getLinkSuggestions: vi.fn().mockResolvedValue({ data: [] }),
    mergeDevice: vi.fn().mockResolvedValue({ data: {} }),
    saveSettings: vi.fn().mockResolvedValue({ data: {} }),
  },
}));

const unassigned = [{
  id: 7,
  mac_address: '1A:FB:3A:9D:D2:2C',
  ip_address: '192.168.88.55',
  hostname: 'Pixel-9-Pro-XL',
  is_active: true,
  is_hidden: false,
  history: [],
}];

const users = [{
  id: 1,
  name: 'Mark',
  devices: [{ id: 3, mac_address: 'C6:DA:93:39:1E:C5', custom_name: 'Pixel-9-Pro-XL' }],
}];

function renderInbox(props = {}) {
  return renderWithProviders(
    <DeviceInbox devices={unassigned} users={users} onAssign={vi.fn()} onScan={vi.fn()} {...props} />
  );
}

beforeEach(() => {
  // The suite runs with mock reset between tests, so the resolved values have
  // to be re-established rather than declared once at module scope.
  vi.clearAllMocks();
  api.getSettings.mockResolvedValue({ data: {} });
  api.getMergeSuggestions.mockResolvedValue({ data: [] });
  api.getLinkSuggestions.mockResolvedValue({ data: [] });
  api.mergeDevice.mockResolvedValue({ data: {} });
  api.saveSettings.mockResolvedValue({ data: {} });
});

describe('DeviceInbox manual merge', () => {
  it('lists every known device as a merge target, labelled with its owner', async () => {
    renderInbox();
    const select = await screen.findByTitle(/Fold this record/i);
    expect(select).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Pixel-9-Pro-XL — Mark' })).toBeInTheDocument();
  });

  it('merges into the chosen device once confirmed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const onScan = vi.fn();
    renderInbox({ onScan });

    const select = await screen.findByTitle(/Fold this record/i);
    fireEvent.change(select, { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: /^Merge$/ }));

    await waitFor(() => expect(api.mergeDevice).toHaveBeenCalledWith(7, 3));
    await waitFor(() => expect(onScan).toHaveBeenCalled());
  });

  it('does nothing when the confirmation is dismissed', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderInbox();

    const select = await screen.findByTitle(/Fold this record/i);
    fireEvent.change(select, { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: /^Merge$/ }));

    expect(api.mergeDevice).not.toHaveBeenCalled();
  });

  it('keeps the merge button inert until a target is picked', async () => {
    renderInbox();
    await screen.findByTitle(/Fold this record/i);
    expect(screen.getByRole('button', { name: /^Merge$/ })).toBeDisabled();
  });

  it('offers no merge control when there is nothing to merge into', async () => {
    renderInbox({ users: [] });
    await screen.findByText('Pixel-9-Pro-XL');
    expect(screen.queryByRole('button', { name: /^Merge$/ })).toBeNull();
  });
});
