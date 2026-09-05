import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, renderWithProviders as render, screen, waitFor } from '../test/render';
import React from 'react';
import RouterBackupsModal from './RouterBackupsModal';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    getRouterBackups: vi.fn(),
    triggerRouterBackup: vi.fn(),
    updateRouterBackup: vi.fn(),
    deleteRouterBackup: vi.fn(),
    getBackupDiff: vi.fn(),
    getBackupRscDownloadUrl: vi.fn((rId, bId) => `/api/v1/routers/${rId}/backups/${bId}/download/rsc`),
    getBackupBinaryDownloadUrl: vi.fn((rId, bId) => `/api/v1/routers/${rId}/backups/${bId}/download/backup`),
  },
}));

const mockBackups = {
  items: [
    {
      id: 1,
      router_id: 1,
      created_at: new Date().toISOString(),
      outcome: 'changed',
      source: 'manual',
      fingerprint: 'a1b2c3d4e5f6',
      rsc_bytes: 4096,
      backup_bytes: 65536,
      is_pinned: false,
      note: 'Initial backup',
      model: 'RB5009',
      os_version: '7.15.2',
      has_rsc: true,
      has_binary: true,
    },
    {
      id: 2,
      router_id: 1,
      created_at: new Date().toISOString(),
      outcome: 'unchanged',
      source: 'scheduled',
      fingerprint: 'a1b2c3d4e5f6',
      rsc_bytes: 4096,
      backup_bytes: 0,
      is_pinned: true,
      note: 'Daily check',
      model: 'RB5009',
      os_version: '7.15.2',
      has_rsc: false,
      has_binary: false,
    },
  ],
  total: 2,
  page: 1,
  page_size: 50,
};

const mockDiff = {
  base_id: 1,
  target_id: 2,
  is_target_live: false,
  lines_added: 3,
  lines_removed: 1,
  total_changes: 4,
  hunks: [
    {
      old_start: 1,
      old_count: 5,
      new_start: 1,
      new_count: 7,
      header: '@@ -1,5 +1,7 @@',
      lines: [
        { type: 'ctx', content: '/interface bridge', old_line_no: 1, new_line_no: 1 },
        { type: 'del', content: 'add name=br-old', old_line_no: 2 },
        { type: 'add', content: 'add name=br-new', new_line_no: 2 },
        { type: 'add', content: 'add name=br-extra', new_line_no: 3 },
      ],
    },
  ],
  raw_unified: '--- v1\n+++ v2\n@@ -1,5 +1,7 @@\n /interface bridge\n-add name=br-old\n+add name=br-new\n+add name=br-extra\n',
};

describe('RouterBackupsModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getRouterBackups.mockResolvedValue(mockBackups);
    api.getBackupDiff.mockResolvedValue(mockDiff);
    api.triggerRouterBackup.mockResolvedValue({ id: 3, outcome: 'changed' });
    api.updateRouterBackup.mockResolvedValue({ id: 1, is_pinned: true });
  });

  it('renders backup entries and outcome badges', async () => {
    render(
      <RouterBackupsModal
        isOpen={true}
        onClose={vi.fn()}
        routerId={1}
        routerName="Core-Gateway"
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Initial backup/i)).toBeInTheDocument();
      expect(screen.getByText('Changed', { selector: 'span' })).toBeInTheDocument();
      expect(screen.getByText('Unchanged', { selector: 'span' })).toBeInTheDocument();
    });
  });

  it('triggers on-demand backup on button click', async () => {
    render(
      <RouterBackupsModal
        isOpen={true}
        onClose={vi.fn()}
        routerId={1}
        routerName="Core-Gateway"
      />
    );

    const backupBtn = await screen.findByRole('button', { name: /backup now/i });
    fireEvent.click(backupBtn);

    await waitFor(() => {
      expect(api.triggerRouterBackup).toHaveBeenCalledWith(1);
    });
  });

  it('opens visual diff viewer when clicking Diff button', async () => {
    render(
      <RouterBackupsModal
        isOpen={true}
        onClose={vi.fn()}
        routerId={1}
        routerName="Core-Gateway"
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Initial backup/i)).toBeInTheDocument();
    });

    const diffBtn = screen.getAllByTitle(/compare with the previous snapshot/i)[0];
    fireEvent.click(diffBtn);

    await waitFor(() => {
      expect(api.getBackupDiff).toHaveBeenCalled();
      expect(screen.getByText(/add name=br-new/i)).toBeInTheDocument();
      expect(screen.getByText(/add name=br-old/i)).toBeInTheDocument();
      expect(screen.getByText('+3')).toBeInTheDocument();
      expect(screen.getByText('-1')).toBeInTheDocument();
    });
  });

  it('toggles pin status on click', async () => {
    render(
      <RouterBackupsModal
        isOpen={true}
        onClose={vi.fn()}
        routerId={1}
        routerName="Core-Gateway"
      />
    );

    await waitFor(() => {
      expect(screen.getByText(/Initial backup/i)).toBeInTheDocument();
    });

    const pinButtons = screen.getAllByTitle(/pin milestone/i);
    fireEvent.click(pinButtons[0]);

    await waitFor(() => {
      expect(api.updateRouterBackup).toHaveBeenCalledWith(1, 1, { is_pinned: true });
    });
  });
});
