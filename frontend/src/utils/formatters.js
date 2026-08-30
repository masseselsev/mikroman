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

export function formatSpeed(bps) {
  if (!bps || bps === 0) return '0 bps';
  if (bps < 1000) return `${bps} bps`;
  if (bps < 1000000) return `${(bps / 1000).toFixed(1)} Kbps`;
  if (bps < 1000000000) return `${(bps / 1000000).toFixed(1)} Mbps`;
  return `${(bps / 1000000000).toFixed(2)} Gbps`;
}

/**
 * Compact "time since" label for a timestamp, e.g. "2m", "3h", "5d".
 * Used on device rows where a full date would not fit and is rarely what the
 * reader wants - "how stale is this" is the actual question.
 */
export function formatRelativeTime(timestamp, lang = 'en') {
  if (!timestamp) return '';
  const then = new Date(timestamp);
  if (Number.isNaN(then.getTime())) return '';

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
