import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ContainersPage } from './ContainersPage';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    getContainers: vi.fn(),
    containerAction: vi.fn(),
    createContainer: vi.fn(),
  },
}));

vi.mock('../context/I18nContext', () => ({
  useI18n: () => ({ lang: 'en', t: (k) => k }),
}));

vi.mock('./ContainerWorkloads', () => ({
  ContainerWorkloads: () => <div data-testid="workloads" />,
}));

const MIKROMAN = {
  id: '*1', name: 'mikroman:latest', tag: 'ghcr.io/masseselsev/mikroman:latest',
  status: null, running: true, arch: 'arm64', interface: 'veth-mikroman',
  root_dir: '/mikroman:latest', mounts: 'mikroman_data', hostname: 'mikroman',
  logging: true, start_on_boot: true, comment: 'mikroman:container',
  cpu_usage_pct: 17.6, memory_current_bytes: 519647232, memory_high_bytes: null,
  memory_max_bytes: null, disk_size_bytes: 262440508, restart_count: 0,
  stop_time_seconds: 10,
};

const OVERVIEW = {
  support: { installed: true, enabled: true, version: '7.24.2', status: 'ready', message: null },
  host: { cpu_load_pct: 19, total_memory_bytes: 2147483648, free_memory_bytes: 885293056, uptime: '1d6h15m8s' },
  containers: [MIKROMAN],
  mounts: [{ id: '*1', name: 'mikroman_data', src: '/usb1-part1/mikroman_data', dst: '/data' }],
  envs: [{ id: '*1', name: 'mikroman_envs', key: 'PULL_COUNT', value: '7' }],
  config: { tmpdir: '/usb1-part1/container-tmp', layer_dir: '/usb1-part1/container-layers', memory_current_bytes: 519647232 },
  storage: { ready: true, matched_slot: 'usb1-part1', disks: [] },
};

const router = { id: 1, name: 'hAP be3 Media' };

beforeEach(() => {
  vi.clearAllMocks();
  api.getContainers.mockResolvedValue({ data: OVERVIEW });
  api.containerAction.mockResolvedValue({ data: true });
});

describe('ContainersPage resources', () => {
  it("shows the container's own CPU and memory, read off the /container row", async () => {
    const { container } = render(<ContainersPage activeRouter={router} />);
    await waitFor(() => expect(api.getContainers).toHaveBeenCalledWith(1));
    // 17.6 % of the device and 496 MB of cgroup memory - the two figures that
    // existed on the router and were dropped on the way to the DTO.
    expect(container.textContent).toContain('17.6%');
    expect(container.textContent).toContain('496 MB');
    expect(container.textContent).toContain('250 MB');   // unpacked image size
  });

  it('puts the router totals next to them so the share is readable', async () => {
    const { container } = render(<ContainersPage activeRouter={router} />);
    await waitFor(() => expect(api.getContainers).toHaveBeenCalled());
    expect(container.textContent).toContain('ctr_host_cpu');
    expect(container.textContent).toContain('19%');
    // 2147483648 - 885293056 = 1262190592 bytes used of the 2 GB board
    expect(container.textContent).toContain('1.18 GB');
    expect(container.textContent).toContain('2.00 GB');
  });

  it('shows a running container as running, not as a dash', async () => {
    const { container } = render(<ContainersPage activeRouter={router} />);
    await waitFor(() => expect(api.getContainers).toHaveBeenCalled());
    // RouterOS 7.x has no `status` attribute on /container; the page used to
    // read only that one and printed '—' for a container that was up.
    expect(container.textContent).toContain('running');
    expect(container.querySelector('.ctr-status.is-ok')).toBeTruthy();
  });

  it('disables stop for a container that is not running', async () => {
    api.getContainers.mockResolvedValue({
      data: { ...OVERVIEW, containers: [{ ...MIKROMAN, running: false }] },
    });
    const { container } = render(<ContainersPage activeRouter={router} />);
    await waitFor(() => expect(api.getContainers).toHaveBeenCalled());
    const stop = container.querySelector('button[title="ctr_stop"]');
    const start = container.querySelector('button[title="ctr_start"]');
    expect(stop.disabled).toBe(true);
    expect(start.disabled).toBe(false);
    expect(container.textContent).toContain('stopped');
  });

  it('masks env values in the list', async () => {
    const { container } = render(<ContainersPage activeRouter={router} />);
    await waitFor(() => expect(api.getContainers).toHaveBeenCalled());
    // The API has no auth: whoever can reach the port can read this panel, and
    // an env row is a credential store as easily as the database is.
    expect(container.textContent).not.toContain('PULL_COUNT=7');
    expect(container.textContent).toContain('PULL_COUNT');
  });

  it('leaves a resource column blank rather than inventing zero', async () => {
    api.getContainers.mockResolvedValue({
      data: {
        ...OVERVIEW,
        containers: [{ ...MIKROMAN, cpu_usage_pct: null, memory_current_bytes: null, disk_size_bytes: null }],
      },
    });
    const { container } = render(<ContainersPage activeRouter={router} />);
    await waitFor(() => expect(api.getContainers).toHaveBeenCalled());
    expect(container.textContent).not.toContain('0.0%');
  });

  it('refreshes on click and not on a timer', async () => {
    const { container } = render(<ContainersPage activeRouter={router} />);
    await waitFor(() => expect(api.getContainers).toHaveBeenCalledTimes(1));
    // One poll of this page is six REST calls against a device that is also
    // routing, so the numbers are fetched on entry and on demand only.
    await new Promise(r => setTimeout(r, 1200));
    expect(api.getContainers).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText('ctr_refresh'));
    await waitFor(() => expect(api.getContainers).toHaveBeenCalledTimes(2));
  });
});
