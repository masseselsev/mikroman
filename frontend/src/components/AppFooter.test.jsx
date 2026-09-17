import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen, waitFor } from '../test/render';
import { AppFooter, formatVersionTag } from './AppFooter';
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
        current_version: '0.3.31',
        latest_version: '0.3.31',
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
        current_version: '0.3.30',
        latest_version: '0.3.31',
        has_update: true,
        release_url: 'https://github.com/masseselsev/mikroman/releases/tag/v0.3.31',
      },
    });

    const { container } = renderWithProviders(<AppFooter />);

    await waitFor(() => {
      expect(container.querySelector('.footer-update-badge')).toBeTruthy();
    });

    const badge = container.querySelector('.footer-update-badge');
    expect(badge.getAttribute('href')).toBe('https://github.com/masseselsev/mikroman/releases/tag/v0.3.31');
    expect(badge.textContent).toContain('0.3.31');
  });

  it('silently ignores network errors during version check', async () => {
    api.checkAppVersion.mockRejectedValue(new Error('Network offline'));

    const { container } = renderWithProviders(<AppFooter />);

    expect(container.querySelector('.version-tag')).toBeTruthy();
    expect(container.querySelector('.footer-update-badge')).toBeNull();
  });
});
