import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithProviders, screen, waitFor } from '../test/render';
import { TrafficHistoryModal } from './TrafficHistoryModal';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    getUserTrafficHistory: vi.fn(),
    getDeviceTrafficHistory: vi.fn(),
    getUserDestinations: vi.fn().mockResolvedValue({ data: [] }),
  },
}));

const mockUserData = {
  entity_type: 'user',
  entity_id: 1,
  entity_name: 'Alice',
  range_preset: '7d',
  start_date: '2026-08-26',
  end_date: '2026-09-01',
  total_bytes_in: 5000000,
  total_bytes_out: 2000000,
  total_bytes: 7000000,
  daily_average_bytes: 1000000,
  peak_date: '2026-09-01',
  peak_bytes: 3000000,
  timeline: [
    { record_date: '2026-08-26', bytes_in: 500000, bytes_out: 200000, total_bytes: 700000 },
    { record_date: '2026-08-27', bytes_in: 600000, bytes_out: 300000, total_bytes: 900000 },
    { record_date: '2026-08-28', bytes_in: 700000, bytes_out: 300000, total_bytes: 1000000 },
    { record_date: '2026-08-29', bytes_in: 800000, bytes_out: 300000, total_bytes: 1100000 },
    { record_date: '2026-08-30', bytes_in: 900000, bytes_out: 400000, total_bytes: 1300000 },
    { record_date: '2026-08-31', bytes_in: 500000, bytes_out: 200000, total_bytes: 700000 },
    { record_date: '2026-09-01', bytes_in: 1000000, bytes_out: 300000, total_bytes: 1300000 },
  ],
  devices: [
    {
      device_id: 10,
      hostname: 'alice-laptop',
      custom_name: 'Alice MacBook',
      ip_address: '192.168.88.100',
      mac_address: 'AA:BB:CC:DD:EE:01',
      vendor: 'Apple, Inc.',
      user_id: 1,
      user_name: 'Alice',
      bytes_in: 3500000,
      bytes_out: 1500000,
      total_bytes: 5000000,
      percentage_of_total: 71.4,
      is_active: true,
      last_active: '2026-09-01T12:00:00Z',
    },
    {
      device_id: 11,
      hostname: 'alice-iphone',
      custom_name: 'Alice iPhone',
      ip_address: '192.168.88.101',
      mac_address: 'AA:BB:CC:DD:EE:02',
      vendor: 'Apple, Inc.',
      user_id: 1,
      user_name: 'Alice',
      bytes_in: 1500000,
      bytes_out: 500000,
      total_bytes: 2000000,
      percentage_of_total: 28.6,
      is_active: false,
      last_active: '2026-09-01T10:00:00Z',
    },
  ],
};

const mockDeviceData = {
  entity_type: 'device',
  entity_id: 10,
  entity_name: 'Alice MacBook',
  mac_address: 'AA:BB:CC:DD:EE:01',
  ip_address: '192.168.88.100',
  user_name: 'Alice',
  user_id: 1,
  range_preset: '7d',
  start_date: '2026-08-26',
  end_date: '2026-09-01',
  total_bytes_in: 3500000,
  total_bytes_out: 1500000,
  total_bytes: 5000000,
  daily_average_bytes: 714285,
  peak_date: '2026-09-01',
  peak_bytes: 1500000,
  timeline: [
    { record_date: '2026-08-26', bytes_in: 400000, bytes_out: 150000, total_bytes: 550000 },
    { record_date: '2026-08-27', bytes_in: 500000, bytes_out: 200000, total_bytes: 700000 },
    { record_date: '2026-08-28', bytes_in: 500000, bytes_out: 200000, total_bytes: 700000 },
    { record_date: '2026-08-29', bytes_in: 600000, bytes_out: 250000, total_bytes: 850000 },
    { record_date: '2026-08-30', bytes_in: 700000, bytes_out: 300000, total_bytes: 1000000 },
    { record_date: '2026-08-31', bytes_in: 400000, bytes_out: 150000, total_bytes: 550000 },
    { record_date: '2026-09-01', bytes_in: 400000, bytes_out: 250000, total_bytes: 650000 },
  ],
};

beforeEach(() => vi.clearAllMocks());

describe('TrafficHistoryModal component', () => {
  it('fetches and renders user traffic history with presets, chart, stats, and device breakdown', async () => {
    api.getUserTrafficHistory.mockResolvedValueOnce({ data: mockUserData });

    const handleClose = vi.fn();
    const handleSelectTarget = vi.fn();

    renderWithProviders(
      <TrafficHistoryModal
        isOpen={true}
        target={{ type: 'user', id: 1, name: 'Alice' }}
        onClose={handleClose}
        onSelectTarget={handleSelectTarget}
      />
    );

    await waitFor(() => {
      expect(api.getUserTrafficHistory).toHaveBeenCalledWith(1, { preset: '7d' });
    });

    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('Alice MacBook')).toBeInTheDocument();
    expect(screen.getByText('Alice iPhone')).toBeInTheDocument();

    // Preset buttons exist
    const dayBtn = screen.getByRole('button', { name: '24H' });
    const weekBtn = screen.getByRole('button', { name: '7D' });
    const monthBtn = screen.getByRole('button', { name: '30D' });
    const yearBtn = screen.getByRole('button', { name: '1Y' });
    const customBtn = screen.getByRole('button', { name: 'Custom' });

    expect(dayBtn).toBeInTheDocument();
    expect(weekBtn).toBeInTheDocument();
    expect(monthBtn).toBeInTheDocument();
    expect(yearBtn).toBeInTheDocument();
    expect(customBtn).toBeInTheDocument();

    // Switching preset to 30d
    api.getUserTrafficHistory.mockResolvedValueOnce({ data: { ...mockUserData, range_preset: '30d' } });
    fireEvent.click(monthBtn);

    await waitFor(() => {
      expect(api.getUserTrafficHistory).toHaveBeenCalledWith(1, { preset: '30d' });
    });
  });

  it('renders the 24H view as a half-hour timeline with HH:MM labels', async () => {
    api.getUserTrafficHistory.mockResolvedValueOnce({ data: mockUserData });
    api.getUserTrafficHistory.mockResolvedValueOnce({
      data: {
        ...mockUserData,
        range_preset: 'today',
        resolution: 'half_hour',
        start_date: '2026-09-02',
        end_date: '2026-09-02',
        peak_label: '09:00',
        timeline: [
          { record_date: '2026-09-02', label: '00:00', bytes_in: 0, bytes_out: 0, total_bytes: 0 },
          { record_date: '2026-09-02', label: '08:30', bytes_in: 100000, bytes_out: 40000, total_bytes: 140000 },
          { record_date: '2026-09-02', label: '09:00', bytes_in: 300000, bytes_out: 90000, total_bytes: 390000 },
        ],
      },
    });

    renderWithProviders(
      <TrafficHistoryModal isOpen={true} target={{ type: 'user', id: 1, name: 'Alice' }} onClose={vi.fn()} />
    );

    await waitFor(() => expect(api.getUserTrafficHistory).toHaveBeenCalledWith(1, { preset: '7d' }));
    fireEvent.click(screen.getByRole('button', { name: '24H' }));

    await waitFor(() => expect(api.getUserTrafficHistory).toHaveBeenCalledWith(1, { preset: '24h' }));

    // The breakdown table now has a Time column and HH:MM rows, not dates.
    await waitFor(() => expect(screen.getByText('Half-hour breakdown')).toBeInTheDocument());
    expect(screen.getByRole('columnheader', { name: 'Time' })).toBeInTheDocument();
    expect(screen.getByText('3 intervals')).toBeInTheDocument();
    expect(screen.getAllByText('09:00').length).toBeGreaterThanOrEqual(1);
  });

  it('renders the 1Y view as a weekly breakdown', async () => {
    api.getUserTrafficHistory.mockResolvedValueOnce({ data: mockUserData });
    api.getUserTrafficHistory.mockResolvedValueOnce({
      data: {
        ...mockUserData,
        range_preset: '1y',
        resolution: 'week',
        peak_label: 'Aug 31',
        timeline: [
          { record_date: '2026-08-24', label: 'Aug 24', bytes_in: 1_000_000, bytes_out: 200_000, total_bytes: 1_200_000 },
          { record_date: '2026-08-31', label: 'Aug 31', bytes_in: 5_000_000, bytes_out: 900_000, total_bytes: 5_900_000 },
        ],
      },
    });

    renderWithProviders(
      <TrafficHistoryModal isOpen={true} target={{ type: 'user', id: 1, name: 'Alice' }} onClose={vi.fn()} />
    );

    await waitFor(() => expect(api.getUserTrafficHistory).toHaveBeenCalledWith(1, { preset: '7d' }));
    fireEvent.click(screen.getByRole('button', { name: '1Y' }));

    await waitFor(() => expect(api.getUserTrafficHistory).toHaveBeenCalledWith(1, { preset: '1y' }));
    await waitFor(() => expect(screen.getByText('Weekly breakdown')).toBeInTheDocument());
    expect(screen.getByRole('columnheader', { name: 'Week' })).toBeInTheDocument();
    expect(screen.getByText('2 weeks')).toBeInTheDocument();
    expect(screen.getAllByText('Aug 31').length).toBeGreaterThanOrEqual(1);
  });

  it('fetches and renders device traffic history', async () => {
    api.getDeviceTrafficHistory.mockResolvedValueOnce({ data: mockDeviceData });

    renderWithProviders(
      <TrafficHistoryModal
        isOpen={true}
        target={{ type: 'device', id: 10, name: 'Alice MacBook', mac: 'AA:BB:CC:DD:EE:01' }}
        onClose={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(api.getDeviceTrafficHistory).toHaveBeenCalledWith(10, { preset: '7d' });
    });

    expect(screen.getByText('Alice MacBook')).toBeInTheDocument();
  });
});
