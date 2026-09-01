import { afterEach, describe, expect, it, vi } from 'vitest';
import { formatBytes, formatDateTime, formatGbWhole, formatLastActive, formatRelativeTime, formatSpeed, formatSpeedShort, formatUptime } from './formatters';

/**
 * These decide what every figure on the dashboard actually reads as, so their
 * unit boundaries are worth pinning: an off-by-one there turns 999 bps into
 * "1.0 Kbps" or a gigabyte into a megabyte, and nothing else would catch it.
 */

describe('formatBytes', () => {
  it('renders zero and falsy input as 0 B', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(null)).toBe('0 B');
    expect(formatBytes(undefined)).toBe('0 B');
  });

  it('steps through binary units', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(1024 ** 2)).toBe('1 MB');
    expect(formatBytes(1024 ** 3)).toBe('1 GB');
    expect(formatBytes(1024 ** 4)).toBe('1 TB');
  });

  it('keeps one decimal and drops a trailing zero', () => {
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(2048)).toBe('2 KB');
  });

  it('handles a realistic monthly figure', () => {
    expect(formatBytes(41_588_759_711)).toBe('38.7 GB');
  });
});

describe('formatSpeed', () => {
  it('renders an idle link as 0 bps', () => {
    expect(formatSpeed(0)).toBe('0 bps');
    expect(formatSpeed(null)).toBe('0 bps');
  });

  it('uses decimal units, as network rates are quoted', () => {
    // Deliberately 1000, not 1024: link rates are decimal.
    expect(formatSpeed(999)).toBe('999 bps');
    expect(formatSpeed(1000)).toBe('1.0 Kbps');
    expect(formatSpeed(1_000_000)).toBe('1.0 Mbps');
    expect(formatSpeed(1_000_000_000)).toBe('1.00 Gbps');
  });

  it('renders the rates seen on a device row', () => {
    expect(formatSpeed(39_500)).toBe('39.5 Kbps');
    expect(formatSpeed(12_400_000)).toBe('12.4 Mbps');
  });
});

describe('formatSpeedShort', () => {
  it('is empty-ish for an idle link', () => {
    expect(formatSpeedShort(0)).toBe('0');
    expect(formatSpeedShort(null)).toBe('0');
  });

  it('drops the unit word and the "bps" suffix - the arrow beside it says rate', () => {
    expect(formatSpeedShort(39_500)).toBe('39.5K');
    expect(formatSpeedShort(12_400_000)).toBe('12.4M');
    expect(formatSpeedShort(1_200_000_000)).toBe('1.2G');
  });

  it('shows a decimal only while it carries information', () => {
    // Below 100 of the unit the fraction matters; above it, it is noise on a
    // row this narrow.
    expect(formatSpeedShort(2_500)).toBe('2.5K');
    expect(formatSpeedShort(250_000)).toBe('250K');
    expect(formatSpeedShort(340_000_000)).toBe('340M');
  });

  it('reports sub-kbit rates as a bare number', () => {
    expect(formatSpeedShort(512)).toBe('512');
  });
});

describe('formatGbWhole', () => {
  const GiB = 1024 ** 3;

  it('is 0 for nothing or a negative', () => {
    expect(formatGbWhole(0)).toBe(0);
    expect(formatGbWhole(null)).toBe(0);
    expect(formatGbWhole(-5)).toBe(0);
  });

  it('rounds to whole GiB, half rounding up', () => {
    expect(formatGbWhole(0.4 * GiB)).toBe(0);
    expect(formatGbWhole(0.5 * GiB)).toBe(1);
    expect(formatGbWhole(46.7 * GiB)).toBe(47);
  });

  it('returns a number, not a string with a unit', () => {
    expect(formatGbWhole(3 * GiB)).toBe(3);
  });
});

describe('formatRelativeTime', () => {
  afterEach(() => vi.useRealTimers());

  const at = (isoNow) => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(isoNow));
  };

  it('is empty for missing or unparseable input', () => {
    expect(formatRelativeTime(null)).toBe('');
    expect(formatRelativeTime('not a date')).toBe('');
  });

  it('steps through the units', () => {
    at('2026-08-31T12:00:00Z');
    expect(formatRelativeTime('2026-08-31T11:59:30Z')).toBe('now');
    expect(formatRelativeTime('2026-08-31T11:45:00Z')).toBe('15m');
    expect(formatRelativeTime('2026-08-31T09:00:00Z')).toBe('3h');
    expect(formatRelativeTime('2026-08-26T12:00:00Z')).toBe('5d');
    // A "month" here is 30 days, which is what a compact label can honestly
    // claim: 1 June to 31 August is 91 days, so three of them.
    expect(formatRelativeTime('2026-06-01T12:00:00Z')).toBe('3mo');
    expect(formatRelativeTime('2026-08-01T12:00:00Z')).toBe('1mo');
  });

  it('never renders a negative age for a clock skewed into the future', () => {
    at('2026-08-31T12:00:00Z');
    expect(formatRelativeTime('2026-08-31T12:05:00Z')).toBe('now');
  });

  it('localises the unit suffix', () => {
    at('2026-08-31T12:00:00Z');
    expect(formatRelativeTime('2026-08-31T09:00:00Z', 'ru')).toBe('3ч');
    expect(formatRelativeTime('2026-08-31T11:59:30Z', 'ru')).toBe('сейчас');
  });
});

describe('formatLastActive', () => {
  afterEach(() => vi.useRealTimers());
  const at = (isoNow) => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(isoNow));
  };

  it('is empty for missing or unparseable input', () => {
    expect(formatLastActive(null)).toBe('');
    expect(formatLastActive('not a date')).toBe('');
  });

  it('rounds UP to the coarsest unit, unlike formatRelativeTime', () => {
    at('2026-08-31T12:00:00Z');
    // 90 minutes -> "2h", not "1h"
    expect(formatLastActive('2026-08-31T10:30:00Z')).toBe('2h');
    // 25 hours -> "2d", not "1d"
    expect(formatLastActive('2026-08-30T11:00:00Z')).toBe('2d');
    // a bare minute over the hour still rounds to the next hour
    expect(formatLastActive('2026-08-31T10:59:00Z')).toBe('2h');
    // exactly on a boundary does not round past it
    expect(formatLastActive('2026-08-31T11:00:00Z')).toBe('1h');
  });

  it('treats the last ~minute as "now" and never goes negative', () => {
    at('2026-08-31T12:00:00Z');
    expect(formatLastActive('2026-08-31T11:59:40Z')).toBe('now');
    expect(formatLastActive('2026-08-31T12:05:00Z')).toBe('now');
  });

  it('localises the suffix', () => {
    at('2026-08-31T12:00:00Z');
    expect(formatLastActive('2026-08-31T10:30:00Z', 'ru')).toBe('2ч');
  });
});

describe('formatDateTime', () => {
  it('is empty for missing or unparseable input', () => {
    expect(formatDateTime(null)).toBe('');
    expect(formatDateTime('not a date')).toBe('');
  });

  it('renders a fixed-width local date and time', () => {
    // en-GB: dd/mm/yyyy, 24h
    expect(formatDateTime('2026-09-01T14:32:00Z', 'en')).toMatch(/01\/09\/2026/);
  });
});

describe('formatUptime', () => {
  it('parses the RouterOS string form', () => {
    expect(formatUptime('1w2d3h4m5s')).toBe('1w 2d 3h 4m');
    expect(formatUptime('2d3h55m')).toBe('2d 3h 55m');
    expect(formatUptime('4h30m')).toBe('4h 30m');
  });

  it('keeps seconds only when they are all there is', () => {
    expect(formatUptime('42s')).toBe('42s');
  });

  it('parses numeric seconds', () => {
    expect(formatUptime(90)).toBe('1m');
    expect(formatUptime(3661)).toBe('1h 1m');
    expect(formatUptime(180000)).toBe('2d 2h');
  });

  it('renders a freshly booted router as 0m rather than blank', () => {
    expect(formatUptime(0)).toBe('0m');
    expect(formatUptime('0')).toBe('0m');
    expect(formatUptime(null)).toBe('0m');
  });

  it('localises the units', () => {
    expect(formatUptime('2d3h', 'ru')).toBe('2д 3ч');
    expect(formatUptime(0, 'ru')).toBe('0м');
  });

  it('falls back to spacing an unrecognised string rather than dropping it', () => {
    expect(formatUptime('3h junk')).toBe('3h  junk');
  });
});
