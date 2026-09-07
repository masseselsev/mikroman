import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MetricCharts } from './MetricCharts';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    getSettings: vi.fn(),
    getAvailableInterfaces: vi.fn(),
    getMonitoredInterfacesConfig: vi.fn(),
    getSystemMetrics: vi.fn(),
    getInterfaceMetrics: vi.fn(),
    saveMonitoredInterfacesConfig: vi.fn(),
  },
}));

vi.mock('../context/I18nContext', () => ({
  useI18n: () => ({
    lang: 'en',
    t: (k) => ({
      metrics_title: 'Hardware & Bandwidth Graphs',
      interface_bandwidth: 'Interface Bandwidth',
      cpu_history: 'CPU Load (%)',
      ram_history: 'RAM Usage (%)',
      temp_history: 'Board Temperature (°C)',
      voltage_history: 'Board Voltage (V)',
      select_interfaces: 'Interfaces',
      save_default_ifaces: 'Save Default',
      saved_ifaces_success: 'Saved',
      no_metrics_yet: 'No metrics yet',
      range_1h: '1 Hour',
      range_6h: '6 Hours',
      range_24h: '24 Hours',
      range_7d: '7 Days',
      range_30d: '30 Days',
    }[k] || k),
  }),
}));

// The 30-day shape: a four-hour bucket that carried a 343.6 Mbps burst but only
// averaged 1.2 Mbps of it. The mean is what the line must show; the burst is
// what the chart used to lose completely.
const IFACE_RESPONSE = {
  data: {
    range: '30d',
    interfaces: ['ether1'],
    is_summed: true,
    current_rx_bps: 1_500_000,
    current_tx_bps: 320_000,
    points: [
      {
        timestamp: '2026-09-05T04:00:00',
        rx_rate_bps: 400_000, rx_peak_bps: 900_000,
        tx_rate_bps: 60_000, tx_peak_bps: 120_000,
      },
      {
        timestamp: '2026-09-06T04:00:00',
        rx_rate_bps: 1_200_000, rx_peak_bps: 343_600_000,
        tx_rate_bps: 90_000, tx_peak_bps: 49_600_000,
      },
      {
        timestamp: '2026-09-07T04:00:00',
        rx_rate_bps: 250_000, rx_peak_bps: 250_000,
        tx_rate_bps: 40_000, tx_peak_bps: 40_000,
      },
    ],
  },
};

const SYSTEM_RESPONSE = {
  data: {
    range: '30d',
    current_cpu: 2,
    current_ram_pct: 30.2,
    current_temp: 46,
    current_voltage: 24.1,
    points: [
      {
        timestamp: '2026-09-05T04:00:00',
        cpu_load: 2, cpu_peak: 4,
        memory_usage_pct: 30, memory_used_mb: 60, memory_total_mb: 200,
        temperature: 46, temperature_peak: 48,
        voltage: 24.1, voltage_min: 24.0, voltage_max: 24.2,
      },
      {
        timestamp: '2026-09-06T04:00:00',
        cpu_load: 3, cpu_peak: 99,
        memory_usage_pct: 31, memory_used_mb: 62, memory_total_mb: 200,
        temperature: 47, temperature_peak: 71,
        voltage: 24.0, voltage_min: 22.4, voltage_max: 24.3,
      },
    ],
  },
};

/** Highest point a path reaches on screen - smaller y means a taller drawing. */
function topOfPath(d) {
  return Math.min(...(d.match(/-?\d+(\.\d+)?/g) || []).slice(1).map(Number));
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getSettings.mockResolvedValue({ data: {} });
  api.getAvailableInterfaces.mockResolvedValue({ data: [{ name: 'ether1', running: true, disabled: false }] });
  api.getMonitoredInterfacesConfig.mockResolvedValue({ data: { selected_interfaces: ['ether1'] } });
  api.getSystemMetrics.mockResolvedValue(SYSTEM_RESPONSE);
  api.getInterfaceMetrics.mockResolvedValue(IFACE_RESPONSE);
});

describe('MetricCharts peak banding', () => {
  it('scales the bandwidth axis to the burst, not to the bucket average', async () => {
    render(<MetricCharts activeRouterId={1} />);

    // 343.6 Mbps peak with the chart's headroom, not the 1.2 Mbps mean that used
    // to be the tallest thing on the axis.
    await waitFor(() => expect(screen.getByText('395.1 Mbps')).toBeTruthy());
    expect(screen.queryByText('1.4 Mbps')).toBeNull();
  });

  it('draws a peak band that climbs well above the average line', async () => {
    const { container } = render(<MetricCharts activeRouterId={1} />);
    await waitFor(() => expect(container.querySelectorAll('svg').length).toBeGreaterThan(0));

    const paths = [...container.querySelectorAll('path')];
    const band = paths.find(p => p.getAttribute('fill') === '#10b981' && p.getAttribute('opacity'));
    const line = paths.find(p => p.getAttribute('stroke') === '#10b981' && p.getAttribute('fill') === 'none');

    expect(band, 'the RX peak envelope should be rendered').toBeTruthy();
    expect(line, 'the RX average line should still be rendered').toBeTruthy();

    // The band must reach up to the burst while the line stays down at the mean:
    // both halves of the story in one card.
    expect(topOfPath(band.getAttribute('d'))).toBeLessThan(topOfPath(line.getAttribute('d')) - 40);
  });

  it('says what the two shapes mean', async () => {
    render(<MetricCharts activeRouterId={1} />);
    await waitFor(() => expect(screen.getAllByText('peak').length).toBeGreaterThan(0));
    expect(screen.getAllByText('average').length).toBeGreaterThan(0);
  });

  it('bands the CPU and temperature worst cases too', async () => {
    const { container } = render(<MetricCharts activeRouterId={1} />);
    await waitFor(() => expect(container.querySelectorAll('svg').length).toBeGreaterThan(0));

    const paths = [...container.querySelectorAll('path')];
    const cpuBand = paths.find(p => p.getAttribute('fill') === '#0b72c9' && p.getAttribute('opacity'));
    const tempBand = paths.find(p => p.getAttribute('fill') === '#f59e0b' && p.getAttribute('opacity'));

    expect(cpuBand).toBeTruthy();
    expect(tempBand).toBeTruthy();
    // The 99% stall is drawn even though the bucket averaged 3%.
    expect(topOfPath(cpuBand.getAttribute('d'))).toBeLessThan(30);
  });

  it('shows the min–max envelope on the voltage view', async () => {
    const { container } = render(<MetricCharts activeRouterId={1} />);
    await waitFor(() => expect(container.querySelectorAll('svg').length).toBeGreaterThan(0));

    // The health card opens on temperature; the voltage series is one click away.
    fireEvent.click(screen.getByText('24.1V'));

    await waitFor(() => expect(screen.getByText('min – max')).toBeTruthy());
    const voltBand = [...container.querySelectorAll('path')]
      .find(p => p.getAttribute('fill') === '#06b6d4' && p.getAttribute('opacity'));
    // A sag below the average is the reading that matters on this card, so its
    // band is bounded by the bucket's low as well as its high.
    expect(voltBand).toBeTruthy();
  });

  it('leaves a freeze blank instead of ramping the line across it', async () => {
    const HOUR = 3600 * 1000;
    const at = (h) => new Date(Date.UTC(2026, 8, 1) + h * HOUR).toISOString().slice(0, 19);
    // Hourly buckets with a seventeen-hour hole between the third and fourth.
    api.getInterfaceMetrics.mockResolvedValue({
      data: {
        range: '7d',
        interfaces: ['ether1'],
        is_summed: true,
        bucket_seconds: 3600,
        current_rx_bps: 1_000_000,
        current_tx_bps: 100_000,
        points: [0, 1, 2, 19, 20, 21].map(h => ({
          timestamp: at(h),
          rx_rate_bps: 1_000_000, rx_peak_bps: 3_000_000,
          tx_rate_bps: 100_000, tx_peak_bps: 200_000,
        })),
      },
    });

    const { container } = render(<MetricCharts activeRouterId={1} />);
    await waitFor(() => expect(container.querySelectorAll('svg[viewBox="0 0 500 180"]').length).toBe(4));

    const rxRuns = [...container.querySelectorAll('path')]
      .filter(p => p.getAttribute('stroke') === '#10b981' && p.getAttribute('fill') === 'none');
    // Two separate runs. One path would have drawn traffic across the hours
    // nobody measured.
    expect(rxRuns).toHaveLength(2);
    // And the card says how old the newest reading is, rather than implying live.
    expect(screen.getByText(/last sample/)).toBeTruthy();
  });
});
