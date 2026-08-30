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
