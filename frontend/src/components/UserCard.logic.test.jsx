import { describe, expect, it } from 'vitest';
import {
  bandLabel,
  connectionLinks,
  formatLimitSummary,
  groupDevices,
  signalColor,
} from './UserCard';

/**
 * The pure logic behind a device row. None of this was covered before, and it
 * encodes two decisions that are easy to get wrong and hard to spot by eye: how
 * a multi-adapter machine collapses into one row, and how a Wi-Fi 7 multi-link
 * client is expanded back into its member radios.
 */

const device = (over = {}) => ({
  id: 1,
  is_active: true,
  is_paused: false,
  current_rate_in: 0,
  current_rate_out: 0,
  bytes_today_in: 0,
  bytes_today_out: 0,
  linked_to_device_id: null,
  ...over,
});

describe('groupDevices - multi-adapter machines', () => {
  it('leaves unlinked devices as one group each', () => {
    const groups = groupDevices([device({ id: 1 }), device({ id: 2 })]);
    expect(groups).toHaveLength(2);
    expect(groups.every(g => g.adapters.length === 1)).toBe(true);
  });

  it('collapses a linked adapter behind its primary', () => {
    // The same laptop on cable and on Wi-Fi: two MACs, one machine.
    const groups = groupDevices([
      device({ id: 1, custom_name: 'mpcX' }),
      device({ id: 2, linked_to_device_id: 1 }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].primary.id).toBe(1);
    expect(groups[0].adapters.map(a => a.id)).toEqual([1, 2]);
  });

  it('puts the primary first regardless of input order', () => {
    const groups = groupDevices([
      device({ id: 2, linked_to_device_id: 1 }),
      device({ id: 1 }),
    ]);
    expect(groups[0].adapters[0].id).toBe(1);
  });

  it('sums traffic across adapters', () => {
    // Otherwise a dual-homed machine reads as two half-idle devices.
    const groups = groupDevices([
      device({ id: 1, current_rate_in: 100, bytes_today_in: 10 }),
      device({ id: 2, linked_to_device_id: 1, current_rate_in: 250, bytes_today_in: 40 }),
    ]);
    expect(groups[0].rateIn).toBe(350);
    expect(groups[0].bytesIn).toBe(50);
  });

  it('is online while any adapter is online', () => {
    const groups = groupDevices([
      device({ id: 1, is_active: false }),
      device({ id: 2, linked_to_device_id: 1, is_active: true }),
    ]);
    expect(groups[0].isActive).toBe(true);
  });

  it('is paused only when every adapter is paused', () => {
    // A machine with one live adapter is not paused - it would simply hop media.
    const partly = groupDevices([
      device({ id: 1, is_paused: true }),
      device({ id: 2, linked_to_device_id: 1, is_paused: false }),
    ]);
    expect(partly[0].isPaused).toBe(false);

    const fully = groupDevices([
      device({ id: 1, is_paused: true }),
      device({ id: 2, linked_to_device_id: 1, is_paused: true }),
    ]);
    expect(fully[0].isPaused).toBe(true);
  });

  it('does not lose an adapter whose primary was filtered out', () => {
    // Hidden devices are filtered before grouping; the orphan must still show
    // rather than vanish from the list.
    const groups = groupDevices([device({ id: 2, linked_to_device_id: 99 })]);
    expect(groups).toHaveLength(1);
    expect(groups[0].primary.id).toBe(2);
  });

  it('returns nothing for an empty list', () => {
    expect(groupDevices([])).toEqual([]);
  });
});

describe('connectionLinks - Wi-Fi 7 multi-link', () => {
  it('expands an MLO client into one entry per radio', () => {
    // RouterOS reports the bundle as 'mld1', which names no actual radio.
    const links = connectionLinks(device({
      wifi_links: [
        { interface: 'wifi1', signal: -55, band: '2ghz-ax' },
        { interface: 'wifi2', signal: -68, band: '5ghz-be' },
      ],
    }));
    expect(links).toHaveLength(2);
    expect(links.map(l => l.interface)).toEqual(['wifi1', 'wifi2']);
    expect(links.every(l => l.wireless)).toBe(true);
    expect(links[1].signal).toBe(-68);
  });
});

describe('connectionLinks - single link', () => {
  it('trusts connection_kind over a stale signal reading', () => {
    // A machine that moved onto cable must not be drawn as wireless just
    // because the last signal value is still on record.
    const links = connectionLinks(device({
      connection_kind: 'wired',
      last_interface: 'bridge',
      last_wifi_signal: -60,
    }));
    expect(links[0].wireless).toBe(false);
    expect(links[0].signal).toBeNull();
  });

  it('falls back to the signal when connection_kind is unknown', () => {
    const links = connectionLinks(device({ last_interface: 'wifi2', last_wifi_signal: -71 }));
    expect(links[0].wireless).toBe(true);
    expect(links[0].signal).toBe(-71);
  });

  it('treats a device with neither as wired', () => {
    const links = connectionLinks(device({ last_interface: 'ether3' }));
    expect(links[0].wireless).toBe(false);
  });
});

describe('bandLabel', () => {
  it.each([
    ['5ghz-be', '5G·BE'],
    ['5ghz-ax', '5G·AX'],
    ['2ghz-ax', '2.4G·AX'],
    ['5ghz', '5G'],
  ])('renders %s as %s', (input, expected) => {
    expect(bandLabel(input)).toBe(expected);
  });

  it('returns nothing for a missing band', () => {
    expect(bandLabel(null)).toBeNull();
    expect(bandLabel('')).toBeNull();
  });
});

describe('signalColor', () => {
  it('grades by usability, not by raw magnitude', () => {
    expect(signalColor(-50)).toBe('var(--color-success)');
    expect(signalColor(-72)).toBe('var(--color-warning)');
    expect(signalColor(-85)).toBe('var(--color-danger)');
  });

  it('puts the boundaries where the comments say', () => {
    expect(signalColor(-65)).toBe('var(--color-warning)');
    expect(signalColor(-64)).toBe('var(--color-success)');
    expect(signalColor(-80)).toBe('var(--color-danger)');
    expect(signalColor(-79)).toBe('var(--color-warning)');
  });
});

describe('formatLimitSummary', () => {
  it('renders an unlimited profile', () => {
    expect(formatLimitSummary('unlimited')).toBe('⚡ Unlimited');
    expect(formatLimitSummary('0/0')).toBe('⚡ Unlimited');
    expect(formatLimitSummary(null)).toBe('⚡ Unlimited');
  });

  it('shows download first, since RouterOS stores upload first', () => {
    // "20M/50M" is upload/download in RouterOS; the UI leads with download.
    expect(formatLimitSummary('20M/50M')).toBe('↓ 50M / ↑ 20M');
  });

  it('renders a symmetric single value', () => {
    expect(formatLimitSummary('30M')).toBe('↓↑ 30M');
  });
});
