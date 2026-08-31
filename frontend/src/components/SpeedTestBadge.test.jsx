import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithProviders, screen, waitFor } from '../test/render';
import { SpeedTestBadge } from './SpeedTestBadge';
import { ContainerWorkloads } from './ContainerWorkloads';
import { api } from '../api/client';

/**
 * The speed test badge on the WAN IP tile, and the container-workloads list.
 *
 * The badge's contract is that it never shows a button that cannot work: a
 * router without the container package, or without a speedtest container, gets
 * an explanation instead. Offering a button that always fails is worse than
 * offering nothing.
 */

vi.mock('../api/client', () => ({
  api: {
    getSpeedTestStatus: vi.fn(),
    runSpeedTest: vi.fn(),
    getContainerDevices: vi.fn(),
  },
}));

const ready = (last = null) => ({
  data: {
    can_run: true,
    reason: 'ready',
    container_id: '*2',
    container_status: 'stopped',
    logging_enabled: true,
    last_result: last,
  },
});

const result = {
  id: 1, created_at: '2026-09-01T10:00:00', status: 'ok',
  download_mbps: 482.17, upload_mbps: 93.66, ping_ms: 8.42, jitter_ms: 0.51,
  server_name: 'Some ISP - Riga', isp: 'Example Telecom',
};

beforeEach(() => {
  vi.clearAllMocks();
  api.getSpeedTestStatus.mockResolvedValue(ready());
  api.runSpeedTest.mockResolvedValue({ data: { result } });
  api.getContainerDevices.mockResolvedValue({ data: [] });
});

describe('SpeedTestBadge', () => {
  it('renders nothing without a router', () => {
    const { container } = renderWithProviders(<SpeedTestBadge routerId={null} />);
    expect(container.textContent).toBe('');
    expect(api.getSpeedTestStatus).not.toHaveBeenCalled();
  });

  it('offers the button when the router can run a test', async () => {
    renderWithProviders(<SpeedTestBadge routerId={1} />);
    expect(await screen.findByRole('button', { name: /Speed test/i })).toBeInTheDocument();
  });

  it('shows the last result rounded to whole Mbps', async () => {
    api.getSpeedTestStatus.mockResolvedValue(ready(result));
    renderWithProviders(<SpeedTestBadge routerId={1} />);
    expect(await screen.findByText(/482/)).toBeInTheDocument();
    expect(screen.getByText(/94/)).toBeInTheDocument();
  });

  it('runs a test and refreshes the status afterwards', async () => {
    renderWithProviders(<SpeedTestBadge routerId={1} />);
    const button = await screen.findByRole('button', { name: /Speed test/i });
    fireEvent.click(button);
    await waitFor(() => expect(api.runSpeedTest).toHaveBeenCalledWith(1));
    await waitFor(() => expect(api.getSpeedTestStatus).toHaveBeenCalledTimes(2));
  });

  it('explains instead of offering a button when there is no container', async () => {
    api.getSpeedTestStatus.mockResolvedValue({
      data: { can_run: false, reason: 'no_container', last_result: null },
    });
    renderWithProviders(<SpeedTestBadge routerId={1} />);
    expect(await screen.findByText(/No speed test/i)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('stays silent when the status call fails', async () => {
    api.getSpeedTestStatus.mockRejectedValue(new Error('unreachable'));
    const { container } = renderWithProviders(<SpeedTestBadge routerId={1} />);
    await waitFor(() => expect(api.getSpeedTestStatus).toHaveBeenCalled());
    expect(container.textContent).toBe('');
  });
});

describe('ContainerWorkloads', () => {
  it('says so plainly when the router runs none', async () => {
    renderWithProviders(<ContainerWorkloads />);
    expect(await screen.findByText(/No container workloads/i)).toBeInTheDocument();
  });

  it('lists container devices with their interface, marked as containers', async () => {
    api.getContainerDevices.mockResolvedValue({
      data: [{
        id: 5, mac_address: '02:00:00:AA:BB:CC', ip_address: '172.17.0.2',
        hostname: 'speedtest', vendor: null, last_interface: 'veth1',
        is_container: true, bytes_today_in: 1024, bytes_today_out: 512,
      }],
    });
    renderWithProviders(<ContainerWorkloads />);
    expect(await screen.findByText('speedtest')).toBeInTheDocument();
    expect(screen.getByText('veth1')).toBeInTheDocument();
    // The row's own badge, not the section heading which also says "Container".
    expect(document.querySelector('.container-badge').textContent).toMatch(/Container/);
  });

  it('asks only for container devices, never for clients', async () => {
    renderWithProviders(<ContainerWorkloads />);
    await waitFor(() => expect(api.getContainerDevices).toHaveBeenCalled());
  });
});
