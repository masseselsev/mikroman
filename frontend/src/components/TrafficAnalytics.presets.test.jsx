import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TrafficAnalytics } from './TrafficAnalytics';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    getTrafficAnalytics: vi.fn(),
    getBillingCycleConfig: vi.fn(),
    saveBillingCycleConfig: vi.fn(),
  },
}));

vi.mock('../context/I18nContext', () => ({
  useI18n: () => ({ lang: 'en', t: (k) => String(k) }),
}));

// The tabs render tables of the payload; this file is about *which* payload
// wins, so the tab is stubbed to the names it was handed and each response is
// stamped with the range it came from.
// OverviewTab is the default view and does receive `users`, so it carries the marker.
vi.mock('./analytics/OverviewTab', () => ({
  OverviewTab: ({ users }) => <div data-testid="ov">{(users || []).map(u => u.user_name).join(',')}</div>,
}));
vi.mock('./analytics/UsersTab', () => ({
  UsersTab: ({ users }) => <div data-testid="ut">{(users || []).map(u => u.user_name).join(',')}</div>,
}));
vi.mock('./analytics/DevicesTab', () => ({ DevicesTab: () => <div data-testid="dt" /> }));
vi.mock('./analytics/InterfacesTab', () => ({ InterfacesTab: () => <div data-testid="it" /> }));

function payload(preset) {
  return {
    range_preset: preset,
    start_date: '2026-09-01', end_date: '2026-09-08', billing_anchor_day: 1,
    gateway: { total_bytes_in: 0, total_bytes_out: 0, total_bytes: 0, monitored_interfaces: [] },
    router_self: { bytes_in: 0, bytes_out: 0 },
    unassigned: { bytes_in: 0, bytes_out: 0 },
    users: [{ user_name: preset, bytes_in: 0, bytes_out: 0, total_bytes: 0, pct_of_total: 0 }],
    devices: [], interfaces: [], timeline: [],
    unaccounted_bytes: 0, over_accounted_bytes: 0,
    accounting_health: { status: 'ok' },
  };
}

/**
 * Record every request and hand the test its resolver, so responses can arrive
 * in whatever order the test asks for. Deliberately a growing log rather than a
 * fixed pair of promises: the view may ask more often than the clicks below, and
 * a mock that ran out of answers would fail the test instead of the code.
 */
function recordRequests() {
  const calls = [];
  api.getTrafficAnalytics.mockImplementation((opts) => {
    let resolve;
    const promise = new Promise(r => { resolve = r; });
    calls.push({ ...opts, resolve });
    return promise;
  });
  return calls;
}

const shownRange = () => screen.getByTestId('ov').textContent;

beforeEach(() => {
  vi.clearAllMocks();
  api.getBillingCycleConfig.mockResolvedValue({
    data: { anchor_day: 1, anchor_hour: 0, anchor_minute: 0 },
  });
});

describe('TrafficAnalytics preset switching', () => {
  it('carries an abort signal on every range request', async () => {
    const calls = recordRequests();
    render(<TrafficAnalytics activeRouter={{ id: 1, name: 'R' }} />);
    await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(1));
    expect(calls[0].preset).toBe('today');
    expect(calls[0].signal).toBeTruthy();
    calls[0].resolve({ data: payload('today') });
    await waitFor(() => expect(shownRange()).toBe('today'));
  });

  it('aborts the request a preset click superseded', async () => {
    const calls = recordRequests();
    render(<TrafficAnalytics activeRouter={{ id: 1, name: 'R' }} />);
    await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(1));
    const first = calls[0];

    fireEvent.click(screen.getByText('range_7d'));
    await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(2));

    // The abandoned read is still in flight on a router-hosted backend. Without
    // the abort it lands after the new response, and the view ends up showing
    // the range nobody asked for any more.
    expect(first.signal.aborted).toBe(true);
    calls[1].resolve({ data: payload('7d') });
    await waitFor(() => expect(shownRange()).toBe('7d'));
  });

  it('keeps the newest response when the older one arrives last', async () => {
    const calls = recordRequests();
    render(<TrafficAnalytics activeRouter={{ id: 1, name: 'R' }} />);
    await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(1));

    fireEvent.click(screen.getByText('range_7d'));
    await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(2));

    calls[1].resolve({ data: payload('7d') });
    await waitFor(() => expect(shownRange()).toBe('7d'));

    calls[0].resolve({ data: payload('today') });     // the stale request answers last
    await new Promise(r => setTimeout(r, 30));
    expect(shownRange()).toBe('7d');
  });

  it('says that a range is being recalculated', async () => {
    recordRequests();
    const { container } = render(<TrafficAnalytics activeRouter={{ id: 1, name: 'R' }} />);
    await waitFor(() => expect(api.getTrafficAnalytics).toHaveBeenCalled());
    // `loading` used to be tracked and never rendered, so a slow switch looked
    // like a frozen page still showing old numbers rather than work running.
    expect(container.textContent).toContain('analytics_loading');
  });
});
