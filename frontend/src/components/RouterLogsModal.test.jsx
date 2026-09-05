import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RouterLogsModal } from './RouterLogsModal';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    getLogs: vi.fn(),
    getLogStats: vi.fn(),
    getLoggingRules: vi.fn(),
    createLoggingRule: vi.fn(),
    deleteLoggingRule: vi.fn(),
  },
}));

vi.mock('../context/I18nContext', () => ({
  useI18n: () => ({
    lang: 'en',
    t: (k, p = {}) => {
      const trans = {
        router_logs_title: 'Router Logs',
        source_live: 'Live Stream (2.5s)',
        source_db: 'Stored History (DB)',
        cat_all: 'All',
        cat_auth: '🚨 Security / Auth',
        cat_dhcp: '⚡ DHCP',
        cat_errors: '❌ Errors',
        copy_logs: 'Copy Logs',
        export_logs: 'Export .log',
        manage_logging_rules: 'Logging Rules',
        no_logs_found: 'No logs matching current filters',
        log_count: '{count} entries',
        log_rule_builtin: 'built-in',
        delete_rule_btn: 'Delete Rule',
        log_search_ph: 'Search message or topic...',
      };
      let text = trans[k] || k;
      Object.entries(p).forEach(([key, v]) => {
        text = text.replace(new RegExp(`\\{${key}\\}`, 'g'), v);
      });
      return text;
    },
  }),
}));

const ENTRIES = [
  {
    id: 1, router_id: 1, external_id: '*1',
    timestamp: '2026-09-04T10:00:00',
    topics: 'system,error,critical',
    message: 'login failure for user admin from 10.0.0.9 via ssh',
    severity: 'critical', category: 'auth',
  },
  {
    id: 2, router_id: 1, external_id: '*2',
    timestamp: '2026-09-04T10:01:00',
    topics: 'dhcp,info',
    message: 'dhcp1 assigned 192.168.88.41 to AA:BB:CC:DD:EE:01',
    severity: 'info', category: 'dhcp',
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  api.getLogs.mockResolvedValue({ data: ENTRIES });
  api.getLogStats.mockResolvedValue({
    data: {
      router_id: 1, total_logs: 2, critical_count: 1,
      error_count: 0, warning_count: 0, auth_failures_count: 1,
    },
  });
  api.getLoggingRules.mockResolvedValue({
    data: [
      { id: '*1', topics: 'info', action: 'memory', comment: null, is_managed: false },
      { id: '*7', topics: 'wireless', action: 'memory', comment: 'mikroman:log:wireless', is_managed: true },
    ],
  });
  api.createLoggingRule.mockResolvedValue({ data: { id: '*9' } });
  api.deleteLoggingRule.mockResolvedValue({ data: true });
});

function open(props = {}) {
  return render(
    <RouterLogsModal isOpen onClose={vi.fn()} routerId={1} routerName="Marusyan" {...props} />
  );
}

describe('RouterLogsModal', () => {
  it('renders nothing until it is opened', () => {
    render(<RouterLogsModal isOpen={false} onClose={vi.fn()} />);
    expect(screen.queryByText('Router Logs')).toBeNull();
  });

  it('streams live entries with their message and topics', async () => {
    open();
    await waitFor(() => {
      expect(screen.getByText(/login failure for user admin/)).toBeInTheDocument();
    });
    expect(screen.getByText('[system,error,critical]')).toBeInTheDocument();
    expect(screen.getByText(/2 entries/)).toBeInTheDocument();
    // Live is the default source, since that is what a router actually holds.
    expect(api.getLogs).toHaveBeenCalledWith(
      expect.objectContaining({ source: 'live', router_id: 1 })
    );
  });

  it('asks the server for the chosen category rather than filtering locally', async () => {
    // The router's ring buffer holds far more than the viewport; filtering in
    // the browser would only ever filter the slice already fetched.
    open();
    await waitFor(() => expect(api.getLogs).toHaveBeenCalled());

    fireEvent.click(screen.getByText('⚡ DHCP'));

    await waitFor(() => {
      expect(api.getLogs).toHaveBeenCalledWith(
        expect.objectContaining({ category: 'dhcp' })
      );
    });
  });

  it('switches to the stored history, which is the only source with a past', async () => {
    open();
    await waitFor(() => expect(api.getLogs).toHaveBeenCalled());

    fireEvent.click(screen.getByText('Stored History (DB)'));

    await waitFor(() => {
      expect(api.getLogs).toHaveBeenCalledWith(expect.objectContaining({ source: 'db' }));
    });
  });

  it('passes the search term to the server', async () => {
    open();
    await waitFor(() => expect(api.getLogs).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText('Search message or topic...'), {
      target: { value: 'login failure' },
    });

    await waitFor(() => {
      expect(api.getLogs).toHaveBeenCalledWith(
        expect.objectContaining({ search: 'login failure' })
      );
    });
  });

  it('offers a delete only for the rules MikroMan created', async () => {
    open();
    fireEvent.click(screen.getByText('Logging Rules'));

    await waitFor(() => expect(api.getLoggingRules).toHaveBeenCalledWith(1));

    // The router's own `info` rule is protected - deleting it would silence
    // the log the viewer exists to show.
    expect(screen.getByText('built-in')).toBeInTheDocument();
    const deletes = screen.getAllByTitle('Delete Rule');
    expect(deletes).toHaveLength(1);

    fireEvent.click(deletes[0]);
    await waitFor(() => expect(api.deleteLoggingRule).toHaveBeenCalledWith('*7', 1));
  });

  it('adds a preset topic in one click', async () => {
    open();
    fireEvent.click(screen.getByText('Logging Rules'));
    await waitFor(() => expect(api.getLoggingRules).toHaveBeenCalled());

    fireEvent.click(screen.getByText('firewall'));

    await waitFor(() => {
      expect(api.createLoggingRule).toHaveBeenCalledWith(
        { topics: 'firewall', action: 'memory' },
        1
      );
    });
  });

  it('says so plainly when nothing matches, rather than showing a blank terminal', async () => {
    api.getLogs.mockResolvedValue({ data: [] });
    open();
    await waitFor(() => {
      expect(screen.getByText('No logs matching current filters')).toBeInTheDocument();
    });
  });
});

describe('RouterLogsModal hide-own-logins toggle', () => {
  beforeEach(() => {
    try { localStorage.removeItem('mikroman:logs-hide-self-api'); } catch {}
  });

  it('is off by default and sends hide_self_api only once checked', async () => {
    open();
    await waitFor(() => expect(api.getLogs).toHaveBeenCalled());
    expect(api.getLogs.mock.calls[0][0].hide_self_api).toBeUndefined();

    fireEvent.click(screen.getByText('log_hide_self_api'));

    await waitFor(() => {
      const last = api.getLogs.mock.calls[api.getLogs.mock.calls.length - 1][0];
      expect(last.hide_self_api).toBe(true);
    });
  });

  it('remembers the choice across remounts, as a per-viewer preference', async () => {
    const { unmount } = open();
    fireEvent.click(screen.getByText('log_hide_self_api'));
    await waitFor(() => {
      const last = api.getLogs.mock.calls[api.getLogs.mock.calls.length - 1][0];
      expect(last.hide_self_api).toBe(true);
    });
    unmount();

    api.getLogs.mockClear();
    open();
    await waitFor(() => {
      expect(api.getLogs.mock.calls[0][0].hide_self_api).toBe(true);
    });
  });
});
