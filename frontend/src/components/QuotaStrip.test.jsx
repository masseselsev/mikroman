import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, waitFor } from '../test/render';
import { QuotaStrip } from './QuotaStrip';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: { getQuota: vi.fn() },
}));

const quota = {
  enabled: true,
  on_track: true,
  used_bytes: 269_300_000_000,
  limit_bytes: 2_000_000_000_000,
  used_pct: 13.5,
  projected_pct_linear: 40.8,
  projected_pct_at_pace: 44.4,
  pace_basis: 'recent',
  remaining_bytes: 1_730_700_000_000,
  projected_daily_budget: 78_000_000_000,
  days_left: 22,
  thresholds: [50, 80],
  status: 'on_track',
};

beforeEach(() => {
  vi.clearAllMocks();
  api.getQuota.mockResolvedValue({ data: quota });
});

/**
 * The strip already prints the projection as a number off to the right. On the
 * bar itself - where the eye actually compares "where I am" against "where this
 * is heading" - there was nothing, so the two figures had to be held in the
 * head at once. A marker puts them on the same axis.
 */
describe('QuotaStrip projection marker', () => {
  it('marks the projected end-of-cycle position on the bar', async () => {
    const { container } = renderWithProviders(<QuotaStrip activeRouterId={1} />);

    await waitFor(() => {
      expect(container.querySelector('.quota-strip-projection')).toBeTruthy();
    });

    const marker = container.querySelector('.quota-strip-projection');
    expect(marker.style.left).toBe('40.8%');
  });

  it('pins the marker to the end of the bar when the projection overruns the limit', async () => {
    api.getQuota.mockResolvedValue({
      data: { ...quota, projected_pct_linear: 271, status: 'over_limit' },
    });

    const { container } = renderWithProviders(<QuotaStrip activeRouterId={1} />);

    await waitFor(() => {
      expect(container.querySelector('.quota-strip-projection')).toBeTruthy();
    });
    expect(container.querySelector('.quota-strip-projection').style.left).toBe('100%');
  });

  it('draws no marker before a projection exists', async () => {
    api.getQuota.mockResolvedValue({ data: { ...quota, projected_pct_linear: 0 } });

    const { container } = renderWithProviders(<QuotaStrip activeRouterId={1} />);

    await waitFor(() => expect(container.querySelector('.quota-strip-bar')).toBeTruthy());
    expect(container.querySelector('.quota-strip-projection')).toBeNull();
  });
});
