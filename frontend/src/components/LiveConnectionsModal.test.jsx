import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { LiveConnectionsModal } from './LiveConnectionsModal';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    getLiveConnections: vi.fn(),
    killConnection: vi.fn(),
  },
}));

vi.mock('../context/I18nContext', () => ({
  useI18n: () => ({
    t: (k) => {
      const trans = {
        live_connections_title: 'Live Connections',
        connections_count: 'connections',
        search_connections_placeholder: 'Search IP, domain, device...',
        protocol_all: 'All',
        kill_connection: 'Kill',
        kill_connection_confirm: 'Terminate connection?',
        kill_confirm_yes: 'Yes, Kill',
        cancel: 'Cancel',
      };
      return trans[k] || k;
    },
    lang: 'en',
  }),
}));

const mockConnections = [
  {
    id: '*1',
    protocol: 'tcp',
    src_ip: '192.168.88.50',
    src_port: 52140,
    dst_ip: '142.250.190.46',
    dst_port: 443,
    device_id: 10,
    device_name: 'Work Laptop',
    user_id: 1,
    user_name: 'Alice',
    domain: 'youtube.com',
    country_code: 'US',
    country_name: 'United States',
    flag_emoji: '🇺🇸',
    tcp_state: 'established',
    orig_rate: 250000,
    repl_rate: 1500000,
    orig_bytes: 50000,
    repl_bytes: 800000,
    timeout: '1h',
    is_immune: false,
  },
  {
    id: '*2',
    protocol: 'udp',
    src_ip: '192.168.88.50',
    src_port: 5353,
    dst_ip: '1.1.1.1',
    dst_port: 53,
    device_id: 10,
    device_name: 'Work Laptop',
    user_id: 1,
    user_name: 'Alice',
    domain: 'one.one',
    country_code: 'US',
    country_name: 'United States',
    flag_emoji: '🇺🇸',
    tcp_state: null,
    orig_rate: 0,
    repl_rate: 0,
    orig_bytes: 400,
    repl_bytes: 800,
    timeout: '10s',
    is_immune: false,
  },
];

describe('LiveConnectionsModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getLiveConnections.mockResolvedValue({ data: { total: mockConnections.length, items: mockConnections } });
    api.killConnection.mockResolvedValue({ data: true });
  });

  it('renders connections and handles search filtering', async () => {
    render(<LiveConnectionsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/youtube\.com/i)).toBeInTheDocument();
      expect(screen.getByText(/1\.1\.1\.1/i)).toBeInTheDocument();
    });

    // Filter by search
    const searchInput = screen.getByPlaceholderText(/Search IP, domain, device/i);
    fireEvent.change(searchInput, { target: { value: 'youtube' } });

    expect(screen.getByText(/youtube\.com/i)).toBeInTheDocument();
    expect(screen.queryByText(/1\.1\.1\.1/i)).not.toBeInTheDocument();
  });

  it('filters by protocol pills', async () => {
    render(<LiveConnectionsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/youtube\.com/i)).toBeInTheDocument();
    });

    // Click UDP pill
    const udpBtn = screen.getByRole('button', { name: /^UDP$/i });
    fireEvent.click(udpBtn);

    expect(screen.queryByText(/youtube\.com/i)).not.toBeInTheDocument();
    expect(screen.getByText(/1\.1\.1\.1/i)).toBeInTheDocument();
  });

  it('terminates a connection on kill confirm', async () => {
    render(<LiveConnectionsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/youtube\.com/i)).toBeInTheDocument();
    });

    const killButtons = screen.getAllByTitle(/Kill/i);
    fireEvent.click(killButtons[0]);

    // Confirmation popover appears
    const confirmBtn = screen.getByText(/Yes, Kill/i);
    fireEvent.click(confirmBtn);

    await waitFor(() => {
      expect(api.killConnection).toHaveBeenCalledWith('*1', expect.any(Object));
    });
  });
});

describe('LiveConnectionsModal truncation and error handling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows "shown / total" when the server truncated the list, with no client-side filter active', async () => {
    api.getLiveConnections.mockResolvedValue({ data: { total: 812, items: mockConnections } });
    render(<LiveConnectionsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('2 / 812 connections')).toBeInTheDocument();
    });
  });

  it('drops the "/ total" once a client-side filter narrows the visible rows', async () => {
    // total (812) is a router-side figure that ignores the search box, so
    // pairing it with a filtered count ("1 / 812") would misread as "812
    // connections match your search".
    api.getLiveConnections.mockResolvedValue({ data: { total: 812, items: mockConnections } });
    render(<LiveConnectionsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText(/youtube\.com/i)).toBeInTheDocument());

    fireEvent.change(screen.getByPlaceholderText(/Search IP, domain, device/i), {
      target: { value: 'youtube' },
    });

    await waitFor(() => {
      expect(screen.getByText('1 connections')).toBeInTheDocument();
      expect(screen.queryByText(/\/ 812/)).not.toBeInTheDocument();
    });
  });

  it('does not show "/ total" when nothing was actually truncated', async () => {
    api.getLiveConnections.mockResolvedValue({ data: { total: 2, items: mockConnections } });
    render(<LiveConnectionsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText('2 connections')).toBeInTheDocument();
    });
  });

  it('keeps the last known-good list on screen when a poll fails, instead of showing empty', async () => {
    // Regression: the backend used to swallow a fetch failure into a fake
    // "200 OK, zero connections" response, which this component could not
    // tell apart from a genuine empty router - so the whole table was wiped
    // on every transient failure. Now that failures are real rejections, the
    // existing catch block (which never touches `connections`) is what keeps
    // the table intact.
    api.getLiveConnections.mockResolvedValueOnce({ data: { total: 2, items: mockConnections } });
    render(<LiveConnectionsModal isOpen={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText(/youtube\.com/i)).toBeInTheDocument();
    });

    api.getLiveConnections.mockRejectedValueOnce(new Error('router unreachable'));
    fireEvent.click(screen.getByTitle('Refresh'));

    await waitFor(() => {
      expect(screen.getByText(/router unreachable/i)).toBeInTheDocument();
    });
    // The rows fetched on the earlier, successful poll are still visible.
    expect(screen.getByText(/youtube\.com/i)).toBeInTheDocument();
  });
});
