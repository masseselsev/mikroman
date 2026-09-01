import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithProviders, screen, waitFor, within } from '../test/render';
import { UserCard } from './UserCard';
import { api } from '../api/client';

/**
 * Rendering tests for the device row.
 *
 * The layout was reworked several times because it kept overflowing - names
 * truncated to "Pixe...", rate figures printing over the "seen 10h ago" text.
 * The redesign stacks up to three lines and lets exactly one element per line
 * be greedy. These tests pin that: the name is the only element that gives way
 * on line 1, the address run is the only one on line 2, and the two actions are
 * always reachable.
 */

vi.mock('../api/client', () => ({
  api: {
    toggleDevicePause: vi.fn().mockResolvedValue({}),
    updateDevice: vi.fn().mockResolvedValue({}),
    unlinkDevice: vi.fn().mockResolvedValue({}),
  },
}));

const device = (over = {}) => ({
  id: 10,
  mac_address: 'AA:BB:CC:DD:EE:FF',
  ip_address: '192.168.88.241',
  custom_name: 'Pixel-9-Pro-XL',
  vendor: 'Google Pixel',
  is_active: true,
  is_paused: false,
  is_hidden: false,
  is_randomized_mac: false,
  speed_limit: 'default',
  current_rate_in: 12_400_000,
  current_rate_out: 39_500,
  bytes_today_in: 63_100_000,
  bytes_today_out: 2_200_000,
  connection_kind: 'wireless',
  last_interface: 'wifi2',
  last_wifi_signal: -68,
  wifi_links: null,
  linked_to_device_id: null,
  ...over,
});

const user = (devices) => ({
  id: 1,
  name: 'Kristina',
  speed_limit: 'unlimited',
  is_paused: false,
  current_rate_in: 12_400_000,
  current_rate_out: 39_500,
  bytes_today_in: 63_100_000,
  bytes_today_out: 2_200_000,
  devices,
});

const noop = () => {};

function renderCard(devices, props = {}) {
  return renderWithProviders(
    <UserCard
      user={user(devices)}
      onEdit={noop}
      onDelete={noop}
      onLimitChange={noop}
      onPauseToggle={noop}
      onUpdate={noop}
      gatewayTotal={100_000_000}
      {...props}
    />
  );
}

describe('device row layout', () => {
  beforeEach(() => renderCard([device()]));

  it('shows the full device name rather than truncating it away', () => {
    // The bug this replaced rendered "Pixe...".
    expect(screen.getByText('Pixel-9-Pro-XL')).toBeInTheDocument();
  });

  it('makes the name the only element that gives way on the first line', () => {
    const name = screen.getByText('Pixel-9-Pro-XL');
    expect(name).toHaveClass('drow-name');
    expect(name.closest('.drow-main')).not.toBeNull();
    // The full name is always available on hover even when it truncates.
    expect(name).toHaveAttribute('title', 'Pixel-9-Pro-XL');
  });

  it('shows the live rate compactly, on the name line, only while it is moving', () => {
    const rate = document.querySelector('.drow-rate');
    expect(rate).not.toBeNull();
    expect(rate.closest('.drow-main')).not.toBeNull();
    // Compact form: "12.4M" not "12.4 Mbps".
    expect(rate.textContent).toMatch(/↓\s*12\.4M/);
    expect(rate.textContent).toMatch(/↑\s*39\.5K/);
  });

  it('drops the rate entirely when the device is idle, rather than printing "0 bps" twice', () => {
    renderCard([device({ current_rate_in: 0, current_rate_out: 0 })]);
    // Two rows rendered (beforeEach + this one); the idle one has no rate block.
    const rows = document.querySelectorAll('.device-row');
    const idleRow = rows[rows.length - 1];
    expect(idleRow.querySelector('.drow-rate')).toBeNull();
  });

  it('keeps the action buttons pinned right on the detail line, where they cannot be clipped', () => {
    const actions = document.querySelector('.drow-actions');
    expect(within(actions).getAllByRole('button')).toHaveLength(2);
    expect(actions.closest('.drow-sub')).not.toBeNull();
  });

  it('puts the address and vendor in one truncating run on the second line', () => {
    const facts = document.querySelector('.drow-facts');
    expect(facts.textContent).toContain('192.168.88.241');
    expect(facts.textContent).toContain('Google Pixel');
  });

  it('moves the per-device byte totals off the row and into its tooltip', () => {
    // The per-user panel above already carries the number the reader wants; the
    // row does not have the width to repeat it per device.
    const row = document.querySelector('.device-row');
    expect(row.getAttribute('title')).toMatch(/60\.2 MB/);
    expect(row.getAttribute('title')).toMatch(/2\.1 MB/);
    expect(row.textContent).not.toMatch(/60\.2 MB/);
  });
});

describe('radio links', () => {
  it('tags the band so it can be read at a glance', () => {
    renderCard([device({ wifi_links: [{ interface: 'wifi2', signal: -68, band: '5ghz-be' }] })]);
    const tag = screen.getByText('5G·BE');
    expect(tag).toHaveClass('band-tag');
  });

  it('shows every radio of a Wi-Fi 7 multi-link client', () => {
    // 'mld1' names no actual radio, so the member links are what is shown.
    renderCard([device({
      wifi_links: [
        { interface: 'wifi1', signal: -55, band: '2ghz-ax' },
        { interface: 'wifi2', signal: -68, band: '5ghz-be' },
      ],
    })]);
    expect(screen.getByText('2.4G·AX')).toBeInTheDocument();
    expect(screen.getByText('5G·BE')).toBeInTheDocument();
    expect(screen.getByText('[-55 dBm]')).toBeInTheDocument();
    expect(screen.getByText('[-68 dBm]')).toBeInTheDocument();
  });

  it('writes the signal as a bracketed measurement with its unit', () => {
    // A bare "-68" between an interface name and a band tag reads as part of
    // the interface's name rather than as a reading.
    renderCard([device({ wifi_links: [{ interface: 'wifi2', signal: -71, band: '5ghz-be' }] })]);

    const reading = screen.getByText('[-71 dBm]');
    expect(reading).toHaveClass('signal-reading');
    expect(screen.queryByText('-71')).toBeNull();
  });

  it('does not show a band tag for a wired device', () => {
    renderCard([device({ connection_kind: 'wired', last_interface: 'bridge', wifi_links: null })]);
    expect(document.querySelector('.band-tag')).toBeNull();
  });
});

describe('the vendor', () => {
  it('strips the randomization marker from the vendor', () => {
    // The stored vendor is "Google Pixel (Private MAC)". The "(Private MAC)"
    // suffix duplicates a fact the row deliberately does not spend width on.
    renderCard([device({ vendor: 'Google Pixel (Private MAC)', is_randomized_mac: true })]);

    const facts = document.querySelector('.drow-facts');
    expect(facts.textContent).toContain('Google Pixel');
    expect(facts.textContent).not.toContain('(Private MAC)');
  });

  it('omits the vendor entirely when the marker is all it holds', () => {
    renderCard([device({ vendor: 'Private MAC (Randomized)', is_randomized_mac: true })]);
    const facts = document.querySelector('.drow-facts');
    expect(facts.textContent).toContain('192.168.88.241');
    expect(facts.textContent).not.toContain('Randomized');
  });

  it('keeps a long real vendor', () => {
    renderCard([device({ vendor: 'Quanta Computer' })]);
    expect(document.querySelector('.drow-facts').textContent).toContain('Quanta Computer');
  });
});

describe('badges', () => {
  it('does not spend row width on a PRIVATE badge', () => {
    // Every phone made in the last five years uses a private MAC, so the badge
    // annotated the norm - and it did so on the one line the device name has to
    // share, which is why the name kept truncating. The fact is still reachable
    // through the row's tooltip and the device modal.
    renderCard([device({ is_randomized_mac: true })]);
    expect(screen.queryByText('Private')).toBeNull();
  });

  it('still says so in the row tooltip, where it costs no width', () => {
    renderCard([device({ is_randomized_mac: true })]);
    const row = document.querySelector('.device-row');
    expect(row.getAttribute('title')).toMatch(/Private/i);
  });

  it('keeps the actionable speed-limit chip on the name line', () => {
    // Unlike PRIVATE, a per-device limit is rare (it means someone chose it)
    // and doing something about it starts here, so it earns its width.
    renderCard([device({ speed_limit: '5M/15M' })]);
    const chip = screen.getByText(/5M\/15M/);
    expect(chip.closest('.drow-main')).not.toBeNull();
  });

  it('keeps the width-hungry facts off the name line', () => {
    // IP, vendor and staleness are what used to crowd the name to "Pixel-9-P...".
    renderCard([device({
      custom_name: 'Pixel-9-Pro-XL',
      is_hidden: true,
      is_randomized_mac: true,
    })], { showHidden: true });

    const main = screen.getByText('Pixel-9-Pro-XL').closest('.drow-main');
    expect(main.textContent).not.toContain('192.168.88.241');
    expect(main.textContent).not.toContain('Google Pixel');
  });

  it('counts the adapters of a multi-homed machine', () => {
    renderCard([
      device({ id: 10, custom_name: 'mpcX' }),
      device({ id: 11, linked_to_device_id: 10 }),
    ]);
    expect(screen.getByText('2×')).toBeInTheDocument();
    // One machine, one row - not two half-idle devices.
    expect(document.querySelectorAll('.device-row')).toHaveLength(1);
  });

  it('the N× chip opens the bundle and detaches a wrongly-grouped adapter', async () => {
    renderCard([
      device({ id: 10, custom_name: 'mpcX' }),
      device({ id: 11, custom_name: 'mpcX-usb', linked_to_device_id: 10 }),
    ]);

    fireEvent.click(screen.getByText('2×'));
    // The primary offers no detach; the secondary does.
    const detach = screen.getByRole('button', { name: 'Detach' });
    fireEvent.click(detach);

    await waitFor(() => expect(api.unlinkDevice).toHaveBeenCalledWith(11));
  });
});

describe('pausing a machine', () => {
  it('cuts every adapter, so it cannot simply hop media', async () => {
    renderCard([
      device({ id: 10, custom_name: 'mpcX' }),
      device({ id: 11, linked_to_device_id: 10 }),
    ]);
    // The row's own pause control, not the profile-wide one in the footer.
    const row = document.querySelector('.drow-actions');
    fireEvent.click(within(row).getByTitle('Pause'));

    // waitFor rather than a bare microtask: the handler awaits both requests
    // and then sets state, and that settling has to happen inside act().
    await waitFor(() => expect(api.toggleDevicePause).toHaveBeenCalledTimes(2));
    expect(api.toggleDevicePause).toHaveBeenCalledWith(10, true);
    expect(api.toggleDevicePause).toHaveBeenCalledWith(11, true);
  });
});

describe('the card header', () => {
  it('counts online devices against the total', () => {
    renderCard([device({ id: 10 }), device({ id: 11, is_active: false })]);
    expect(screen.getByText(/1\/2/)).toBeInTheDocument();
  });

  it('labels the volume figure as today, not as the analytics range', () => {
    renderCard([device()]);
    expect(screen.getByText(/Today/)).toBeInTheDocument();
  });
});
