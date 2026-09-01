import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithProviders, screen, waitFor } from '../test/render';
import { TrafficAnalytics } from './TrafficAnalytics';
import { QuotaStrip } from './QuotaStrip';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    getBillingCycleConfig: vi.fn(),
    saveBillingCycleConfig: vi.fn().mockResolvedValue({ data: {} }),
    getTrafficAnalytics: vi.fn().mockResolvedValue({ data: null }),
    getQuota: vi.fn().mockResolvedValue({ data: null }),
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

describe('QuotaStrip countdown', () => {
  const base = {
    enabled: true, used_bytes: 50 * 1024 ** 3, limit_bytes: 100 * 1024 ** 3,
    used_pct: 50, remaining_bytes: 50 * 1024 ** 3, projected_daily_budget: 1024 ** 3,
    on_track: true, projected_pct_linear: 70, pace_basis: 'recent',
    projected_pct_at_pace: 72, days_remaining: 3,
    cycle_start: '2026-09-05', cycle_end: '2026-10-04',
  };

  it('shows whole days for a midnight anchor (backend returns cycle_end_at null)', () => {
    // A midnight anchor makes build_quota_status return cycle_end_at: null, so
    // the strip falls through to the plain "N days left" label.
    vi.spyOn(api, 'getQuota').mockResolvedValue({ data: { ...base, cycle_end_at: null } });
    renderWithProviders(<QuotaStrip activeRouterId={1} onOpenSettings={() => {}} />);
    return screen.findByText(/3 days left/i);
  });

  it('shows Nd Nh when cycle_end_at is a non-midnight instant', async () => {
    const soon = new Date(Date.now() + (2 * 24 + 14) * 3600 * 1000).toISOString();
    vi.spyOn(api, 'getQuota').mockResolvedValue({ data: { ...base, cycle_end_at: soon } });
    renderWithProviders(<QuotaStrip activeRouterId={1} onOpenSettings={() => {}} />);
    expect(await screen.findByText(/2d 1[34]h left/)).toBeInTheDocument();
  });
});
