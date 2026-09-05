import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithProviders, screen } from '../test/render';
import { Navbar } from './Navbar';

// RouterCommentBar loads its own data; stub the client so mounting is cheap.
vi.mock('../api/client', () => ({
  api: { updateRouter: vi.fn().mockResolvedValue({}) },
}));

const base = {
  isConnected: true,
  routerInfo: null,
  routers: [{ id: 1, name: 'Main', is_default: true, comment: 'patch panel B' }],
  onSelectRouter: vi.fn(),
  onOpenSettings: vi.fn(),
  onAddRouter: vi.fn(),
  onRouterCommentSaved: vi.fn(),
};

describe('Navbar router comment bar', () => {
  it('shows the selected router note when activeRouter is passed', () => {
    // Regression: the multi-router refactor dropped activeRouter from the
    // Navbar props, so the note strip stopped rendering entirely.
    renderWithProviders(<Navbar {...base} activeRouter={base.routers[0]} />);
    expect(screen.getByText('patch panel B')).toBeInTheDocument();
  });

  it('renders nothing for the note when no router is active', () => {
    renderWithProviders(<Navbar {...base} activeRouter={null} />);
    expect(screen.queryByText('patch panel B')).toBeNull();
  });

  it('renders firmware update badge when hasFirmwareUpdate is true and triggers onOpenFirmware', () => {
    const onOpenFirmware = vi.fn();
    renderWithProviders(
      <Navbar
        {...base}
        hasFirmwareUpdate={true}
        firmwareVersion="7.16.1"
        onOpenFirmware={onOpenFirmware}
      />
    );

    const updateBtn = screen.getByTitle(/RouterOS Firmware & Updates/i);
    expect(updateBtn).toBeInTheDocument();
    expect(updateBtn).toHaveTextContent(/v7\.16\.1/i);

    updateBtn.click();
    expect(onOpenFirmware).toHaveBeenCalled();
  });
});

/**
 * The top bar had grown until the app-wide controls wrapped onto a second
 * line, below the note. They belong on the same row, to the right of it.
 *
 * Layout is not observable in jsdom, so these assert the structure the CSS
 * depends on: three direct children of `.navbar-row`, in order, with the note
 * between them as the only element allowed to shrink.
 */
describe('Navbar top-bar layout', () => {
  it('keeps the app controls on the same row, to the right of the note', () => {
    const { container } = renderWithProviders(
      <Navbar {...base} activeRouter={base.routers[0]} routerInfo={{ clock: { gmt_offset_minutes: 300, timezone: 'Asia/Tashkent' } }} />
    );

    const row = container.querySelector('.navbar-row');
    const kids = Array.from(row.children);
    expect(kids).toHaveLength(3);
    expect(kids[0].className).toContain('navbar-left');
    expect(kids[1].className).toContain('router-comment');
    expect(kids[2].className).toContain('navbar-actions');

    // Clock, language, theme and settings all live in that third group. The
    // clock shows the numeric offset inline and the zone name only on hover.
    const actions = kids[2];
    expect(actions.textContent).toMatch(/\+5(?!\d)/);
    expect(actions.textContent).not.toMatch(/Tashkent/);
    expect(actions.querySelector('[title*="Asia/Tashkent"]')).not.toBeNull();
    expect(actions.querySelector('.lang-switch-toggle')).not.toBeNull();
    expect(actions.querySelector('[title="Switch to Light Theme"], [title="Switch to Dark Theme"]')).not.toBeNull();
    expect(actions.textContent).toMatch(/Settings/);
  });

  it('still pins the controls right when no router is selected', () => {
    // With no note there is nothing to push against, which is why the group
    // uses `margin-left: auto` rather than `space-between`.
    const { container } = renderWithProviders(<Navbar {...base} activeRouter={null} />);
    const kids = Array.from(container.querySelector('.navbar-row').children);
    expect(kids).toHaveLength(2);
    expect(kids[1].className).toContain('navbar-actions');
  });
});

/**
 * The brand block used to carry the app version and the selected router's
 * board and firmware. Both were moved: the version to the footer, the router
 * facts into the selector's expanded list.
 */
describe('Navbar brand block', () => {
  it('carries only the app name and its tagline', () => {
    const { container } = renderWithProviders(
      <Navbar
        {...base}
        activeRouter={base.routers[0]}
        routerInfo={{ board_name: 'hAP be3 Media', version: '7.24.2 (stable)' }}
      />
    );

    const brand = container.querySelector('.navbar-left > div');
    expect(brand.textContent).toContain('MikroMan');
    expect(brand.textContent).toContain('RouterOS Companion');
    expect(brand.textContent).not.toMatch(/hAP be3/);
    expect(brand.textContent).not.toMatch(/7\.24\.2/);
  });

  it('does not show the app version anywhere in the header', () => {
    const { container } = renderWithProviders(<Navbar {...base} activeRouter={base.routers[0]} />);
    expect(container.querySelector('.version-tag')).toBeNull();
  });
});

/**
 * The router's own clock used to spell out the city name inline
 * ("Tashkent"), which was often wider than the clock face itself and told
 * you nothing you could act on faster than the offset. It now shows the
 * numeric GMT offset, with the full zone name available on hover.
 */
describe('Navbar router clock', () => {
  it('shows a positive offset with a leading plus and no minutes when exact', () => {
    const { container } = renderWithProviders(
      <Navbar
        {...base}
        activeRouter={base.routers[0]}
        routerInfo={{ clock: { gmt_offset_minutes: 300, timezone: 'Asia/Tashkent' } }}
      />
    );
    expect(container.textContent).toMatch(/\+5(?!:)/);
    expect(container.querySelector('[title*="Asia/Tashkent"]')).not.toBeNull();
  });

  it('shows a negative half-hour offset with minutes', () => {
    const { container } = renderWithProviders(
      <Navbar
        {...base}
        activeRouter={base.routers[0]}
        routerInfo={{ clock: { gmt_offset_minutes: -210, timezone: 'America/St_Johns' } }}
      />
    );
    expect(container.textContent).toMatch(/-3:30/);
  });

  it('appends "(DST)" to the tooltip while daylight saving is active', () => {
    const { container } = renderWithProviders(
      <Navbar
        {...base}
        activeRouter={base.routers[0]}
        routerInfo={{ clock: { gmt_offset_minutes: 60, timezone: 'Europe/Berlin', dst_active: true } }}
      />
    );
    expect(container.querySelector('[title="Europe/Berlin (DST)"]')).not.toBeNull();
  });

  it('renders nothing when no clock is on the telemetry payload', () => {
    const { container } = renderWithProviders(
      <Navbar {...base} activeRouter={base.routers[0]} routerInfo={null} />
    );
    expect(container.textContent).not.toMatch(/[+-]\d/);
  });
});

/**
 * The language switcher used to be a single toggle button (a globe icon plus
 * the current language code). It is now two flag buttons side by side, so the
 * language you are not currently on is still visible, and switching is a
 * direct pick rather than always the opposite of whatever is active.
 */
describe('Navbar language switcher', () => {
  it('shows a single toggle carrying the current language\'s flag and switches on click', () => {
    const { container } = renderWithProviders(
      <Navbar {...base} activeRouter={base.routers[0]} />
    );
    const toggle = container.querySelector('.lang-switch-toggle');
    expect(toggle).not.toBeNull();
    // Only one flag button - not two - and it names the *other* language as
    // the action, the same convention the theme toggle already uses.
    expect(container.querySelectorAll('.lang-switch-toggle')).toHaveLength(1);
    expect(toggle.getAttribute('title')).toMatch(/русский/i);

    fireEvent.click(toggle);
    expect(toggle.getAttribute('title')).toMatch(/english/i);

    fireEvent.click(toggle);
    expect(toggle.getAttribute('title')).toMatch(/русский/i);
  });

  it('renders the flag as SVG, not emoji - some platforms have no colour-emoji flag glyphs', () => {
    const { container } = renderWithProviders(
      <Navbar {...base} activeRouter={base.routers[0]} />
    );
    const toggle = container.querySelector('.lang-switch-toggle');
    expect(toggle.querySelector('svg')).not.toBeNull();
    expect(toggle.textContent).not.toMatch(/[\u{1F1E6}-\u{1F1FF}]/u);
  });
});
