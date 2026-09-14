import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, waitFor } from '../test/render';
import { AppFooter } from './AppFooter';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    checkAppVersion: vi.fn(),
  },
}));

describe('AppFooter component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders footer copyright and current version tag', async () => {
    api.checkAppVersion.mockResolvedValue({
      data: {
        current_version: '0.3.26',
        latest_version: '0.3.26',
        has_update: false,
      },
    });

    const { container } = renderWithProviders(<AppFooter />);

    expect(container.querySelector('.app-footer')).toBeTruthy();
    expect(container.querySelector('.version-tag')).toBeTruthy();
    expect(container.querySelector('.footer-update-badge')).toBeNull();
  });

  it('renders glowing update badge when a newer release is detected', async () => {
    api.checkAppVersion.mockResolvedValue({
      data: {
        current_version: '0.3.25',
        latest_version: '0.3.26',
        has_update: true,
        release_url: 'https://github.com/masseselsev/mikroman/releases/tag/v0.3.26',
      },
    });

    const { container } = renderWithProviders(<AppFooter />);

    await waitFor(() => {
      expect(container.querySelector('.footer-update-badge')).toBeTruthy();
    });

    const badge = container.querySelector('.footer-update-badge');
    expect(badge.getAttribute('href')).toBe('https://github.com/masseselsev/mikroman/releases/tag/v0.3.26');
    expect(badge.textContent).toContain('0.3.26');
  });

  it('silently ignores network errors during version check', async () => {
    api.checkAppVersion.mockRejectedValue(new Error('Network offline'));

    const { container } = renderWithProviders(<AppFooter />);

    expect(container.querySelector('.version-tag')).toBeTruthy();
    expect(container.querySelector('.footer-update-badge')).toBeNull();
  });
});
