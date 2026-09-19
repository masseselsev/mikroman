import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen, waitFor, act } from '../test/render';
import { AppFooter, formatVersionTag, VERSION_RECHECK_INTERVAL_MS } from './AppFooter';
import { api } from '../api/client';

vi.mock('../api/client', () => ({
  api: {
    checkAppVersion: vi.fn(),
  },
}));

describe('formatVersionTag', () => {
  it('drops a trailing zero patch', () => {
    expect(formatVersionTag('0.2.0')).toBe('v0.2');
  });

  it('keeps a real patch level', () => {
    expect(formatVersionTag('0.2.1')).toBe('v0.2.1');
  });

  it('passes anything that is not three parts straight through', () => {
    expect(formatVersionTag('1.0')).toBe('v1.0');
  });
});

describe('AppFooter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.checkAppVersion.mockResolvedValue({
      data: {
        current_version: '0.3.39',
        latest_version: '0.3.39',
        has_update: false,
      },
    });
  });

  it('carries the build version, moved down out of the header', () => {
    const { container } = renderWithProviders(<AppFooter />);
    const tag = container.querySelector('.version-tag');
    expect(tag).not.toBeNull();
    expect(tag.textContent).toMatch(/^v\d/);
    expect(tag.getAttribute('title')).toMatch(/^MikroMan \d+\.\d+\.\d+/);
    expect(container.querySelector('.app-footer')).toBeTruthy();
    expect(container.querySelector('.footer-update-badge')).toBeNull();
  });

  it('still shows the copyright line and the source link', () => {
    renderWithProviders(<AppFooter />);
    expect(screen.getByText(/© \d{4} MikroMan/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Source on GitHub/i })).toHaveAttribute(
      'href',
      'https://github.com/masseselsev/mikroman'
    );
  });

  it('matches the header tagline instead of its own separate wording', () => {
    renderWithProviders(<AppFooter />);
    expect(screen.getByText(/RouterOS Companion/i)).toBeInTheDocument();
  });

  it('renders glowing update badge when a newer release is detected', async () => {
    api.checkAppVersion.mockResolvedValue({
      data: {
        current_version: '0.3.38',
        latest_version: '0.3.39',
        has_update: true,
        release_url: 'https://github.com/masseselsev/mikroman/releases/tag/v0.3.39',
      },
    });

    const { container } = renderWithProviders(<AppFooter />);

    await waitFor(() => {
      expect(container.querySelector('.footer-update-badge')).toBeTruthy();
    });

    const badge = container.querySelector('.footer-update-badge');
    expect(badge.getAttribute('href')).toBe('https://github.com/masseselsev/mikroman/releases/tag/v0.3.39');
    expect(badge.textContent).toContain('0.3.39');
  });

  it('silently ignores network errors during version check', async () => {
    api.checkAppVersion.mockRejectedValue(new Error('Network offline'));

    const { container } = renderWithProviders(<AppFooter />);

    expect(container.querySelector('.version-tag')).toBeTruthy();
    expect(container.querySelector('.footer-update-badge')).toBeNull();
  });

  it('re-asks on an interval, so a release published while the tab stays open is not missed', async () => {
    vi.useFakeTimers();
    try {
      const { container } = renderWithProviders(<AppFooter />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(api.checkAppVersion).toHaveBeenCalledTimes(1);

      // The release lands after the first answer was already served.
      api.checkAppVersion.mockResolvedValue({
        data: {
          current_version: '0.3.38',
          latest_version: '0.3.39',
          has_update: true,
          release_url: 'https://github.com/masseselsev/mikroman/releases/tag/v0.3.39',
        },
      });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(VERSION_RECHECK_INTERVAL_MS);
      });
      expect(api.checkAppVersion).toHaveBeenCalledTimes(2);
      expect(container.querySelector('.footer-update-badge')).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it('re-asks when the tab becomes visible again', async () => {
    const { container } = renderWithProviders(<AppFooter />);
    await waitFor(() => expect(api.checkAppVersion).toHaveBeenCalledTimes(1));

    api.checkAppVersion.mockResolvedValue({
      data: {
        current_version: '0.3.38',
        latest_version: '0.3.39',
        has_update: true,
        release_url: 'https://github.com/masseselsev/mikroman/releases/tag/v0.3.39',
      },
    });

    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));

    await waitFor(() => expect(api.checkAppVersion).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(container.querySelector('.footer-update-badge')).toBeTruthy());
  });

  it('drops the offer once the running build catches up with the latest release', async () => {
    api.checkAppVersion.mockResolvedValue({
      data: { current_version: '0.3.38', latest_version: '0.3.39', has_update: true },
    });

    const { container } = renderWithProviders(<AppFooter />);
    await waitFor(() => expect(container.querySelector('.footer-update-badge')).toBeTruthy());

    // Same tab, after the upgrade: the answer now says "current".
    api.checkAppVersion.mockResolvedValue({
      data: { current_version: '0.3.39', latest_version: '0.3.39', has_update: false },
    });

    Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));

    await waitFor(() => expect(container.querySelector('.footer-update-badge')).toBeNull());
  });
});
