/**
 * Formatting utilities for network speeds, byte sizes, and timestamps.
 */

export function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

/**
 * Whole gigabytes, rounded the ordinary way (half rounds up): 0, 1, 2, 47.
 * For the dense per-device volume readout where a decimal and a unit would not
 * fit and are not the point - "roughly how much has this device pulled" is.
 * GiB (1024^3) to match `formatBytes`, so a device that reads "47" here also
 * reads "47.x GB" in its tooltip.
 */
export function formatGbWhole(bytes) {
  if (!bytes || bytes < 0) return 0;
  return Math.round(bytes / (1024 ** 3));
}

/**
 * Compact byte volume readout with short metric units (K, M, G, T), e.g. "0B",
 * "450K", "4.2M", "16M", "13G", "1.5T".
 * For dense device rows where full units ("13.4 GB") are too wide, but raw numbers
 * without units ("13") are ambiguous.
 */
export function formatBytesCompact(bytes) {
  if (!bytes || bytes <= 0) return '0B';
  const k = 1024;
  const sizes = ['B', 'K', 'M', 'G', 'T', 'P'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  if (i === 0) return `${Math.round(bytes)}B`;
  const val = bytes / Math.pow(k, i);
  // If val is less than 10, keep 1 decimal (e.g. 4.2M, 1.5T), otherwise round to integer (e.g. 16M, 250K)
  const formatted = val < 10 ? val.toFixed(1).replace(/\.0$/, '') : Math.round(val);
  return `${formatted}${sizes[i]}`;
}

export function formatSpeed(bps) {
  if (!bps || bps === 0) return '0 bps';
  if (bps < 1000) return `${bps} bps`;
  if (bps < 1000000) return `${(bps / 1000).toFixed(1)} Kbps`;
  if (bps < 1000000000) return `${(bps / 1000000).toFixed(1)} Mbps`;
  return `${(bps / 1000000000).toFixed(2)} Gbps`;
}

/**
 * Very compact rate, for the dense device rows where "94.0 Kbps" is too wide to
 * sit beside a name without pushing something off the card: "94K", "1.2M", "0".
 * The unit is a single letter and there is no "bps" suffix - the arrow next to
 * it already says it is a rate.
 */
export function formatSpeedShort(bps) {
  if (!bps || bps < 1) return '0';
  if (bps < 1000) return `${Math.round(bps)}`;
  // One decimal below 100 of the unit ("12.4M", "39.5K"), none above ("250K",
  // "340M") where the fraction is noise on a row this size.
  if (bps < 1e6) return `${(bps / 1e3).toFixed(bps < 1e5 ? 1 : 0)}K`;
  if (bps < 1e9) return `${(bps / 1e6).toFixed(bps < 1e8 ? 1 : 0)}M`;
  return `${(bps / 1e9).toFixed(1)}G`;
}

/**
 * Parse an ISO or SQLite timestamp from the backend as UTC.
 * Backend datetimes from SQLite are naive UTC strings (e.g. "2026-09-01T12:30:00").
 * Without an explicit timezone indicator ('Z' or offset), standard JS `new Date(str)`
 * incorrectly parses ISO strings as local browser time, introducing an offset equal
 * to the client's timezone (e.g. +5h / +6h).
 */
export function parseUtcDate(timestamp) {
  if (!timestamp) return null;
  if (timestamp instanceof Date) return isNaN(timestamp.getTime()) ? null : timestamp;
  let str = String(timestamp).trim();
  if (!str) return null;
  // If string has date & time without explicit timezone suffix (Z or +/-HH:MM), treat as UTC
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?$/.test(str)) {
    str = str.replace(' ', 'T') + 'Z';
  }
  const d = new Date(str);
  return isNaN(d.getTime()) ? null : d;
}

/**
 * Compact "time since" label for a timestamp, e.g. "2m", "3h", "5d".
 * Used on device rows where a full date would not fit and is rarely what the
 * reader wants - "how stale is this" is the actual question.
 */
export function formatRelativeTime(timestamp, lang = 'en') {
  const then = parseUtcDate(timestamp);
  if (!then) return '';

  const isRu = lang === 'ru';
  const seconds = Math.max(0, Math.floor((Date.now() - then.getTime()) / 1000));

  if (seconds < 60) return isRu ? 'сейчас' : 'now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}${isRu ? 'м' : 'm'}`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}${isRu ? 'ч' : 'h'}`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}${isRu ? 'д' : 'd'}`;
  return `${Math.floor(days / 30)}${isRu ? 'мес' : 'mo'}`;
}

/**
 * "Time since", rounded UP to the coarsest sensible unit - a "last active"
 * readout. 90 minutes reads "2h", 25 hours reads "2d". This is the deliberate
 * opposite of `formatRelativeTime`'s floor: "how long since we last saw this"
 * is a staleness question, and rounding it down understates the staleness.
 */
export function formatLastActive(timestamp, lang = 'en') {
  const then = parseUtcDate(timestamp);
  if (!then) return '';

  const isRu = lang === 'ru';
  const seconds = Math.max(0, Math.floor((Date.now() - then.getTime()) / 1000));
  if (seconds < 45) return isRu ? 'сейчас' : 'now';

  const ceilDiv = (a, b) => Math.max(1, Math.ceil(a / b));
  if (seconds < 3600) return `${ceilDiv(seconds, 60)}${isRu ? 'м' : 'm'}`;
  if (seconds <= 86400) return `${ceilDiv(seconds, 3600)}${isRu ? 'ч' : 'h'}`;
  if (seconds <= 2592000) return `${ceilDiv(seconds, 86400)}${isRu ? 'д' : 'd'}`;
  return `${ceilDiv(seconds, 2592000)}${isRu ? 'мес' : 'mo'}`;
}

/**
 * Absolute local date and time for a hover title, e.g. "01.09.2026, 14:32".
 * Paired with `formatLastActive`: the compact label answers "roughly how
 * stale", the title answers "exactly when".
 */
export function formatDateTime(timestamp, lang = 'en') {
  const d = parseUtcDate(timestamp);
  if (!d) return '';
  return d.toLocaleString(lang === 'ru' ? 'ru-RU' : 'en-GB', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  });
}

/**
 * Absolute date and time in the router's own timezone, e.g. "05.09.2026,
 * 09:50:36" - not the viewer's browser timezone. Every other router-anchored
 * reading (the header clock, the historical rollups) is shown in the
 * router's local wall-clock rather than wherever the dashboard happens to be
 * open; the System Events list previously used a plain `toLocaleString()`,
 * which reads out the *browser's* timezone and made repeated same-second
 * events across two open dashboards look like they landed at different times.
 *
 * Formatted manually (not via `toLocaleString`) so the result is the same
 * regardless of the viewer's own locale or timezone: `gmtOffsetMinutes` is
 * added to the UTC instant and the result read back with the UTC getters,
 * the same trick the header clock uses.
 */
export function formatRouterDateTime(timestamp, gmtOffsetMinutes, lang = 'en') {
  const d = parseUtcDate(timestamp);
  if (!d) return '';
  if (gmtOffsetMinutes == null) return formatDateTime(timestamp, lang);

  const shifted = new Date(d.getTime() + gmtOffsetMinutes * 60000);
  const dd = String(shifted.getUTCDate()).padStart(2, '0');
  const mm = String(shifted.getUTCMonth() + 1).padStart(2, '0');
  const yyyy = shifted.getUTCFullYear();
  const hh = String(shifted.getUTCHours()).padStart(2, '0');
  const min = String(shifted.getUTCMinutes()).padStart(2, '0');
  const ss = String(shifted.getUTCSeconds()).padStart(2, '0');
  return `${dd}.${mm}.${yyyy}, ${hh}:${min}:${ss}`;
}

export function formatUptime(uptime, lang = 'en') {
  const isRu = lang === 'ru';
  const dUnit = isRu ? 'д' : 'd';
  const hUnit = isRu ? 'ч' : 'h';
  const mUnit = isRu ? 'м' : 'm';
  const sUnit = isRu ? 'с' : 's';
  const wUnit = isRu ? 'нед' : 'w';

  if (!uptime || uptime === '0') return `0${mUnit}`;

  // If numeric seconds
  if (typeof uptime === 'number' || /^\d+$/.test(String(uptime).trim())) {
    const totalSec = Number(uptime);
    if (totalSec <= 0) return `0${mUnit}`;
    const days = Math.floor(totalSec / 86400);
    const hours = Math.floor((totalSec % 86400) / 3600);
    const minutes = Math.floor((totalSec % 3600) / 60);

    const parts = [];
    if (days > 0) parts.push(`${days}${dUnit}`);
    if (hours > 0) parts.push(`${hours}${hUnit}`);
    if (minutes > 0 || parts.length === 0) parts.push(`${minutes}${mUnit}`);
    return parts.join(' ');
  }

  // If RouterOS formatted string: e.g. "1d3h58m3s", "1w2d3h4m5s", "4h30m", "58m12s"
  const str = String(uptime).trim();
  const match = str.match(/^(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/i);
  if (match && (match[1] || match[2] || match[3] || match[4] || match[5])) {
    const parts = [];
    if (match[1]) parts.push(`${match[1]}${wUnit}`);
    if (match[2]) parts.push(`${match[2]}${dUnit}`);
    if (match[3]) parts.push(`${match[3]}${hUnit}`);
    if (match[4]) parts.push(`${match[4]}${mUnit}`);
    if (parts.length === 0 && match[5]) parts.push(`${match[5]}${sUnit}`);
    return parts.join(' ');
  }

  // Fallback: insert spaces before units
  return str.replace(/([0-9]+[wdhms])/gi, '$1 ').trim();
}
