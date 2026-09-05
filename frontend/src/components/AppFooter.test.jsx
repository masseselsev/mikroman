import React from 'react';
import { describe, expect, it } from 'vitest';
import { renderWithProviders, screen } from '../test/render';
import { AppFooter, formatVersionTag } from './AppFooter';

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
  it('carries the build version, moved down out of the header', () => {
    const { container } = renderWithProviders(<AppFooter />);
    const tag = container.querySelector('.version-tag');
    expect(tag).not.toBeNull();
    expect(tag.textContent).toMatch(/^v\d/);
    // The full semver stays reachable, so a bug report can quote an exact build.
    expect(tag.getAttribute('title')).toMatch(/^MikroMan \d+\.\d+\.\d+/);
  });

  it('still shows the copyright line and the source link', () => {
    renderWithProviders(<AppFooter />);
    expect(screen.getByText(/© \d{4} MikroMan/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Source on GitHub/i })).toHaveAttribute(
      'href', 'https://github.com/masseselsev/mikroman'
    );
  });
});

describe('AppFooter tagline', () => {
  it('matches the header tagline instead of its own separate wording', () => {
    // Previously "MikroTik companion" in the footer vs "RouterOS Companion"
    // in the header - two names for the same thing. Both now render the same
    // translation key.
    renderWithProviders(<AppFooter />);
    expect(screen.getByText(/RouterOS Companion/i)).toBeInTheDocument();
  });
});
