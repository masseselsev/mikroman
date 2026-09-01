import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithProviders, screen, waitFor } from '../test/render';
import { TrafficAnalytics } from './TrafficAnalytics';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    getBillingCycleConfig: vi.fn(),
    saveBillingCycleConfig: vi.fn().mockResolvedValue({ data: {} }),
    getTrafficAnalytics: vi.fn().mockResolvedValue({ data: null }),
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  api.getBillingCycleConfig.mockResolvedValue({
    data: { anchor_day: 5, anchor_hour: 14, anchor_minute: 30 },
  });
  api.getTrafficAnalytics.mockResolvedValue({ data: null });
  api.saveBillingCycleConfig.mockResolvedValue({ data: {} });
});

describe('billing-cycle reset time', () => {
  it('loads the stored HH:MM into the modal and saves an edited value', async () => {
    renderWithProviders(<TrafficAnalytics activeRouter={{ id: 1 }} />);

    // open the billing-cycle modal (the summary line is a button)
    const opener = await screen.findByText(/Day 5/i);
    fireEvent.click(opener);

    const timeInput = await screen.findByLabelText(/reset time/i);
    expect(timeInput.value).toBe('14:30');

    fireEvent.change(timeInput, { target: { value: '09:15' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() =>
      expect(api.saveBillingCycleConfig).toHaveBeenCalledWith(5, 9, 15)
    );
  });

  it('shows Day-only in the summary when the reset is at midnight', async () => {
    api.getBillingCycleConfig.mockResolvedValue({
      data: { anchor_day: 5, anchor_hour: 0, anchor_minute: 0 },
    });
    renderWithProviders(<TrafficAnalytics activeRouter={{ id: 1 }} />);
    expect(await screen.findByText(/Day 5/i)).toBeInTheDocument();
    expect(screen.queryByText(/Day 5 ·/)).toBeNull();
  });
});
