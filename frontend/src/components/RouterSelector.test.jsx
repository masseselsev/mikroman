import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithProviders, screen } from '../test/render';
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

/**
 * Board model and firmware version used to sit in the header beside the app
 * name, where they crowded the brand and described only one of several
 * routers. They belong to the expanded list instead: that is where you compare
 * routers, and it is the moment the version is actually worth reading.
 */
describe('RouterSelector firmware version', () => {
  const base = { onSelectRouter: vi.fn(), onAddRouter: vi.fn() };
  const routers = [
    { id: 1, name: 'Main', is_default: true, is_online: true, board_name: 'hAP be3 Media', ros_version: '7.24.2 (stable)' },
  ];

  it('keeps the version out of the collapsed trigger', () => {
    const { container } = renderWithProviders(
      <RouterSelector {...base} routers={routers} activeRouter={routers[0]} />
    );
    const trigger = container.querySelector('.router-selector > button');
    expect(trigger).toHaveTextContent('Main');
    expect(trigger.textContent).not.toMatch(/7\.24\.2/);
    expect(trigger.textContent).not.toMatch(/hAP be3/);
  });

  it('shows the board and version once the list is expanded', () => {
    const { container } = renderWithProviders(
      <RouterSelector {...base} routers={routers} activeRouter={routers[0]} />
    );
    fireEvent.click(container.querySelector('.router-selector > button'));
    expect(screen.getByText(/hAP be3 Media · v7\.24\.2 \(stable\)/)).toBeInTheDocument();
  });

  it('prefers the live telemetry version for the selected router over the stored one', () => {
    // The stored `ros_version` comes from the periodic /routers probe, which
    // lags a firmware upgrade until the next sweep.
    const { container } = renderWithProviders(
      <RouterSelector
        {...base}
        routers={routers}
        activeRouter={routers[0]}
        currentVersion="7.25.1 (stable)"
      />
    );
    fireEvent.click(container.querySelector('.router-selector > button'));
    expect(screen.getByText(/v7\.25\.1/)).toBeInTheDocument();
    expect(screen.queryByText(/v7\.24\.2/)).toBeNull();
  });

  it('uses each other router\'s own stored version, not the live one', () => {
    const { container } = renderWithProviders(
      <RouterSelector
        {...base}
        routers={[...routers, { id: 2, name: 'Edge', board_name: 'hEX', ros_version: '7.19.4' }]}
        activeRouter={routers[0]}
        currentVersion="7.25.1 (stable)"
      />
    );
    fireEvent.click(container.querySelector('.router-selector > button'));
    expect(screen.getByText(/hEX · v7\.19\.4/)).toBeInTheDocument();
  });
});
