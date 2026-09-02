import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders, screen } from '../test/render';
import { RouterSelector } from './RouterSelector';

/**
 * The header dot must reflect the router's *live* status. `activeRouter` is a
 * snapshot captured when the router was picked and frequently still says
 * `is_online: false` (it was taken before the first poll came back). The
 * `routers` list is refreshed on every poll, so the dot is read from the
 * matching entry there.
 */
describe('RouterSelector status dot', () => {
  const base = {
    onSelectRouter: vi.fn(),
    onAddRouter: vi.fn(),
  };

  it('shows online from the fresh routers list even when the activeRouter snapshot is stale', () => {
    const { container } = renderWithProviders(
      <RouterSelector
        {...base}
        routers={[{ id: 1, name: 'Main', is_default: true, is_online: true }]}
        activeRouter={{ id: 1, name: 'Main', is_default: true, is_online: false }}
      />
    );
    // The trigger button carries exactly one status dot.
    const dot = container.querySelector('.router-selector > button .status-dot');
    expect(dot).not.toBeNull();
    expect(dot.className).toContain('is-online');
  });

  it('stays grey when the live entry is offline', () => {
    const { container } = renderWithProviders(
      <RouterSelector
        {...base}
        routers={[{ id: 1, name: 'Main', is_default: true, is_online: false }]}
        activeRouter={{ id: 1, name: 'Main', is_default: true, is_online: true }}
      />
    );
    const dot = container.querySelector('.router-selector > button .status-dot');
    expect(dot.className).not.toContain('is-online');
  });

  it('shows online while telemetry is streaming even if the /routers probe says offline', () => {
    // A slow remote router fails the 30s probe (is_online:false) but its
    // telemetry WS is connected - it is plainly reachable, so the dot is green.
    const { container } = renderWithProviders(
      <RouterSelector
        {...base}
        telemetryLive
        routers={[{ id: 1, name: 'Polet-Grad', is_default: true, is_online: false }]}
        activeRouter={{ id: 1, name: 'Polet-Grad', is_default: true, is_online: false }}
      />
    );
    const dot = container.querySelector('.router-selector > button .status-dot');
    expect(dot.className).toContain('is-online');
  });

  it('falls back to the default router when nothing is active yet', () => {
    renderWithProviders(
      <RouterSelector
        {...base}
        routers={[{ id: 2, name: 'Edge', is_default: true, is_online: true }]}
        activeRouter={null}
      />
    );
    expect(screen.getByText('Edge')).toBeInTheDocument();
  });
});
