import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { formatSpeed, formatSpeedShort, formatBytes, formatBytesCompact, formatGbWhole, formatRelativeTime, formatLastActive, formatDateTime, parseUtcDate } from '../utils/formatters';
import { displayVendor } from '../utils/deviceLabels';
import { DeviceModal } from './DeviceModal';
import {
  User as UserIcon,
  Laptop,
  Smartphone,
  Tv,
  Gamepad2,
  Wifi,
  Cable,
  Pause,
  Play,
  Settings,
  Trash2,
  ArrowDown,
  ArrowUp,
  Check,
  Sliders,
  GripVertical,
  BarChart2,
  Activity
} from 'lucide-react';

// Apply and Pause share this exactly, so the footer's second row reads as two
// mirrored halves - only colour and icon set them apart.
const FOOTER_BTN_STYLE = {
  flex: 1,
  minWidth: 0,
  height: 30,
  padding: '2px 8px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 4,
};

function getDeviceIcon(vendor, hostname, size = 14) {
  const text = `${vendor || ''} ${hostname || ''}`.toLowerCase();
  if (text.includes('phone') || text.includes('iphone') || text.includes('pixel') || text.includes('galaxy')) {
    return <Smartphone size={size} />;
  }
  if (text.includes('tv') || text.includes('cast') || text.includes('samsung')) {
    return <Tv size={size} />;
  }
  if (text.includes('playstation') || text.includes('xbox') || text.includes('nintendo') || text.includes('game')) {
    return <Gamepad2 size={size} />;
  }
  return <Laptop size={size} />;
}

export function formatLimitSummary(limitStr) {
  if (!limitStr || limitStr === 'unlimited' || limitStr === '0/0') return '⚡ Unlimited';
  if (limitStr.includes('/')) {
    const [up, down] = limitStr.split('/');
    return `↓ ${down} / ↑ ${up}`;
  }
  return `↓↑ ${limitStr}`;
}

/** Signal strength colour: usable above -65 dBm, weak below -80 dBm. */
export function signalColor(dbm) {
  if (dbm > -65) return 'var(--color-success)';
  if (dbm > -80) return 'var(--color-warning)';
  return 'var(--color-danger)';
}

/**
 * Collapse a device list into logical machines.
 *
 * A machine with more than one network adapter (a laptop docked over Ethernet
 * and roaming over Wi-Fi) has a MAC per adapter and is therefore discovered as
 * several devices. Linked adapters are grouped behind their primary so the
 * machine appears once, with each connection shown separately.
 */
export function groupDevices(devices) {
  const byId = new Map(devices.map(d => [d.id, d]));
  const groups = new Map();

  for (const device of devices) {
    // An adapter whose primary is filtered out stands on its own rather than
    // vanishing from the list.
    const headId = byId.has(device.linked_to_device_id) ? device.linked_to_device_id : device.id;
    if (!groups.has(headId)) groups.set(headId, []);
    groups.get(headId).push(device);
  }

  return [...groups.entries()].map(([headId, adapters]) => {
    adapters.sort((a, b) => (a.id === headId ? -1 : b.id === headId ? 1 : a.id - b.id));
    const primary = byId.get(headId) || adapters[0];
    const sum = (field) => adapters.reduce((acc, a) => acc + (a[field] || 0), 0);
    return {
      key: headId,
      primary,
      adapters,
      // The machine is online while any of its adapters is, and its traffic is
      // the total across them - otherwise a dual-homed machine reads as two
      // half-idle devices.
      isActive: adapters.some(a => a.is_active),
      isPaused: adapters.every(a => a.is_paused),
      rateIn: sum('current_rate_in'),
      rateOut: sum('current_rate_out'),
      bytesIn: sum('bytes_today_in'),
      bytesOut: sum('bytes_today_out'),
      bytesTotalIn: sum('bytes_total_in'),
      bytesTotalOut: sum('bytes_total_out'),
      bytesCycleIn: sum('bytes_cycle_in'),
      bytesCycleOut: sum('bytes_cycle_out'),
      lastSeen: adapters.reduce((latest, a) => {
        if (!a.last_seen) return latest;
        if (!latest) return a.last_seen;
        const dA = parseUtcDate(a.last_seen);
        const dL = parseUtcDate(latest);
        if (!dA) return latest;
        if (!dL) return a.last_seen;
        return dA > dL ? a.last_seen : latest;
      }, null),
    };
  });
}

/**
 * The radio links of a wireless adapter, or a single wired entry.
 *
 * A WiFi 7 multi-link client associates over several radios at once and
 * RouterOS names the bundle 'mld1', which identifies no actual radio. The
 * member links carry the interface and signal that are actually useful.
 */
export function connectionLinks(device) {
  if (device.wifi_links && device.wifi_links.length > 0) {
    return device.wifi_links.map(link => ({
      wireless: true,
      interface: link.interface,
      signal: link.signal,
      band: link.band,
    }));
  }
  // connection_kind is authoritative when known: a machine that moved onto
  // cable must not be drawn as wireless because of a stale signal reading.
  const wireless = device.connection_kind
    ? device.connection_kind === 'wireless'
    : device.last_wifi_signal != null;
  return [{
    wireless,
    interface: device.last_interface,
    signal: wireless ? device.last_wifi_signal : null,
    band: null,
  }];
}

/** Compact label for a radio band, e.g. '5ghz-be' -> '5G·BE'. */
export function bandLabel(band) {
  if (!band) return null;
  const [freq, mode] = band.split('-');
  const shortFreq = freq.replace('ghz', 'G').replace('2', '2.4');
  return mode ? `${shortFreq}·${mode.toUpperCase()}` : shortFreq;
}

/**
 * A single device.
 *
 * Rebuilt after the three-column layout kept overflowing. The old version
 * carried thirteen pieces of information across three lines and a fixed 104px
 * figures column; the column's content was wider than 104px, it had no overflow
 * rule, and it printed straight over the "seen 10h ago" text beside it.
 *
 * The redesign follows two principles:
 *
 *  - Only one element per line may be greedy; everything else is `flex-shrink:0`
 *    and genuinely small. The name line has the name (which truncates) plus at
 *    most two tiny chips. The detail line has one truncating run of text plus
 *    the two action buttons.
 *  - The row shows what changes and what is actionable. The live rate (only
 *    when non-zero - an idle row would otherwise repeat "0 bps" twice) and the
 *    per-device speed limit stay on the row; today's byte totals moved to the
 *    row tooltip and the device modal, since the per-user panel above already
 *    carries the number the reader came for.
 */
function DeviceRow({ group, t, lang, grandTotal = 0, onOpen, onUpdate, onViewTrafficHistory, onViewConnections }) {
  const [busy, setBusy] = useState(false);
  // The adapter list under the "N×" chip, for pulling an adapter back out of a
  // bundle it was wrongly grouped into.
  const [showAdapters, setShowAdapters] = useState(false);
  const [unlinkingId, setUnlinkingId] = useState(null);

  const d = group.primary;
  const rateIn = group.rateIn;
  const rateOut = group.rateOut;
  const isMoving = rateIn > 0 || rateOut > 0;
  const offline = !group.isActive;
  const multiHomed = group.adapters.length > 1;
  const hasCustomLimit = d.speed_limit && d.speed_limit !== 'default';

  // The randomization marker is not shown as a chip: nearly every phone uses a
  // private MAC, so it annotates the norm and only costs width. It stays in the
  // row tooltip and the device modal.
  const vendorLabel = displayVendor(d.vendor);
  const deviceName = d.custom_name || d.hostname || d.vendor || 'Device';

  // Compact volume readout shown on the row: Today / All-Time / Share% in compact units.
  const volToday = group.bytesIn + group.bytesOut;
  const volTotal = group.bytesTotalIn + group.bytesTotalOut;
  const showVolume = volTotal > 0 || volToday > 0;
  const sharePct = grandTotal > 0 ? ((volTotal / grandTotal) * 100).toFixed(1) : '0';
  const volTitle =
    `${t('device_volume_legend')}\n` +
    `${t('today_scope')}: ↓ ${formatBytes(group.bytesIn)} · ↑ ${formatBytes(group.bytesOut)} (${formatBytes(volToday)})\n` +
    `${t('all_time_label')}: ↓ ${formatBytes(group.bytesTotalIn)} · ↑ ${formatBytes(group.bytesTotalOut)} (${formatBytes(volTotal)})\n` +
    `${t('share_of_traffic')}: ${sharePct}% (${formatBytes(volTotal)} ${t('of_total')} ${formatBytes(grandTotal)})`;
  const volumeNote = `${t('today_scope')}: ↓ ${formatBytes(group.bytesIn)} · ↑ ${formatBytes(group.bytesOut)}`;
  const rowTitle = [
    t('device_row_hint'),
    d.is_randomized_mac ? t('private_mac_hint') : null,
    volumeNote,
  ].filter(Boolean).join(' · ');

  // One entry per live radio link, across every active adapter. A WiFi 7
  // multi-link client contributes one per bonded radio.
  const links = group.adapters
    .filter(a => a.is_active)
    .flatMap(adapter =>
      connectionLinks(adapter).map((link, i) => ({ ...link, key: `${adapter.id}-${link.interface}-${i}` }))
    );

  const unlinkAdapter = async (e, adapterId) => {
    e.stopPropagation();
    setUnlinkingId(adapterId);
    try {
      await api.unlinkDevice(adapterId);
      if (onUpdate) onUpdate();
    } catch (err) {
      console.error('Failed to unlink adapter:', err);
    } finally {
      setUnlinkingId(null);
    }
  };

  const togglePause = async (e) => {
    e.stopPropagation();
    setBusy(true);
    try {
      // Pausing a machine must cut every adapter, or it simply hops media.
      await Promise.all(
        group.adapters.map(a => api.toggleDevicePause(a.id, !group.isPaused))
      );
      if (onUpdate) onUpdate();
    } catch (err) {
      console.error('Failed to toggle device pause:', err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="device-row"
      onClick={onOpen}
      title={rowTitle}
      style={{ opacity: group.isPaused ? 0.55 : (offline ? 0.72 : 1) }}
    >
      {/* Line 1 — identity, live rate on the left, and volume stats on the right */}
      <div className="drow-main">
        <span
          className="status-dot"
          style={{
            width: 7,
            height: 7,
            background: group.isPaused ? 'var(--color-danger)' : (group.isActive ? 'var(--color-success)' : 'var(--text-muted)'),
            boxShadow: group.isActive && !group.isPaused ? '0 0 6px rgba(16, 185, 129, 0.5)' : 'none'
          }}
          title={group.isPaused ? t('paused') : (group.isActive ? t('online') : t('offline'))}
        />
        <span style={{ flexShrink: 0, display: 'flex', color: 'var(--text-secondary)' }}>
          {getDeviceIcon(d.vendor, d.hostname)}
        </span>
        <span className="drow-name" title={deviceName}>{deviceName}</span>

        {multiHomed && (
          <button
            type="button"
            className="badge badge-chip drow-adapters-toggle"
            title={t('multi_adapter_manage_hint')}
            onClick={(e) => { e.stopPropagation(); setShowAdapters(v => !v); }}
          >
            {group.adapters.length}×
          </button>
        )}
        {hasCustomLimit && (
          <span className="badge badge-chip badge-chip-warn" title={`${t('table_speed_limit')}: ${d.speed_limit}`}>
            ⚡ {d.speed_limit}
          </span>
        )}

        {showVolume && (
          <span className="drow-vol font-mono" title={volTitle} style={{ marginLeft: 'auto' }}>
            {formatBytesCompact(volToday)}<span className="drow-vol-sep">/</span>
            {formatBytesCompact(volTotal)}<span className="drow-vol-sep">/</span>
            {sharePct}%
          </span>
        )}
      </div>

      {/* Adapter bundle manager — opened from the "N×" chip. Lists every
          adapter in the machine; a secondary one wrongly folded in can be
          detached back to its own device. */}
      {multiHomed && showAdapters && (
        <div className="drow-adapters" onClick={(e) => e.stopPropagation()}>
          <div className="drow-adapters-head">{t('linked_adapters_title')}</div>
          {group.adapters.map(a => {
            const isPrimary = a.id === group.key;
            const wireless = a.connection_kind
              ? a.connection_kind === 'wireless'
              : a.last_wifi_signal != null;
            return (
              <div key={a.id} className="drow-adapter">
                <span className="drow-adapter-ident">
                  {wireless ? <Wifi size={11} /> : <Cable size={11} />}
                  {/* The MAC is the only thing that tells two same-named
                      adapters apart, so it leads. */}
                  <span className="font-mono drow-adapter-mac">{a.mac_address}</span>
                  {isPrimary && (
                    <span className="badge badge-chip drow-adapter-primary">{t('primary_adapter')}</span>
                  )}
                  <span className="drow-adapter-meta font-mono">
                    {a.ip_address || '—'}{a.last_interface ? ` · ${a.last_interface}` : ''}
                  </span>
                </span>
                {isPrimary ? (
                  <span className="drow-adapter-primary-note">{t('primary_adapter_note')}</span>
                ) : (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm drow-adapter-unlink"
                    disabled={unlinkingId === a.id}
                    title={t('unlink_adapter_hint')}
                    onClick={(e) => unlinkAdapter(e, a.id)}
                  >
                    {t('unlink_adapter')}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Line 2 — address, vendor, and last active with tooltip datetime */}
      <div className="drow-sub">
        {/* One greedy run: IP, vendor, hidden flag. It truncates. */}
        <span className="drow-facts">
          <span className="font-mono">{d.ip_address || d.mac_address}</span>
          {vendorLabel && <> · {vendorLabel}</>}
          {d.is_hidden && <> · {t('hidden_badge')}</>}
        </span>

        {/* Last-active is pulled out of the truncating run - "5h" or "2d" is
            meaningless clipped, and the label lives in the tooltip. */}
        {(group.lastSeen || d.last_seen) && (
          <span
            className="drow-lastseen font-mono"
            title={`${t('col_last_active')}: ${formatDateTime(group.lastSeen || d.last_seen, lang)}`}
          >
            {(group.isActive || d.is_active)
              ? (lang === 'ru' ? 'сейчас' : 'now')
              : formatLastActive(group.lastSeen || d.last_seen, lang)}
          </span>
        )}

        <span className="drow-actions">
          {onViewConnections && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onViewConnections(d.id);
              }}
              className="btn-icon"
              style={{ width: 24, height: 24, background: 'var(--bg-card)' }}
              title={t('live_connections_title')}
            >
              <Activity size={12} style={{ color: 'var(--color-primary)' }} />
            </button>
          )}
          {onViewTrafficHistory && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onViewTrafficHistory({
                  type: 'device',
                  id: d.id,
                  name: deviceName,
                  mac: d.mac_address,
                  ip: d.ip_address
                });
              }}
              className="btn-icon"
              style={{ width: 24, height: 24, background: 'var(--bg-card)' }}
              title={t('view_device_history')}
            >
              <BarChart2 size={12} />
            </button>
          )}
          <button
            type="button"
            onClick={togglePause}
            disabled={busy}
            className="btn-icon"
            style={{
              width: 24,
              height: 24,
              background: group.isPaused ? 'var(--color-danger)' : 'var(--bg-card)',
              color: group.isPaused ? '#fff' : 'var(--text-secondary)'
            }}
            title={group.isPaused ? t('resume_device') : t('pause_device')}
          >
            {group.isPaused ? <Play size={12} /> : <Pause size={12} />}
          </button>
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onOpen(); }}
            className="btn-icon"
            style={{ width: 24, height: 24, background: 'var(--bg-card)' }}
            title={t('edit_device')}
          >
            <Sliders size={12} />
          </button>
        </span>
      </div>

      {/* Line 3 — how it is connected, plus the live rate pinned right so it
          sits directly under the action buttons. Radio-link tokens wrap
          rather than truncate; the line also shows for a wired device that is
          currently moving traffic, just for the rate. */}
      {(links.length > 0 || isMoving) && (
        <div className="drow-conn">
          {links.map(link => (
            <span
              key={link.key}
              className="drow-conn-link"
              title={link.band ? `${link.interface} — ${link.band}` : link.interface}
            >
              {link.wireless ? <Wifi size={10} /> : <Cable size={10} />}
              {link.interface}
              {link.band && <span className="band-tag">{bandLabel(link.band)}</span>}
              {link.signal != null && (
                <span className="font-mono signal-reading" style={{ color: signalColor(link.signal) }}>
                  [{link.signal} {t('dbm_unit')}]
                </span>
              )}
            </span>
          ))}
          {isMoving && (
            <span className="drow-rate" style={{ marginLeft: 'auto' }}>
              <span style={{ color: rateIn ? 'var(--color-success)' : 'var(--text-muted)' }}>↓ {formatSpeedShort(rateIn)}</span>
              <span style={{ color: rateOut ? 'var(--color-primary)' : 'var(--text-muted)' }}>↑ {formatSpeedShort(rateOut)}</span>
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export function UserCard({ user, users = [], onEdit, onDelete, onLimitChange, onPauseToggle, onUpdate, onViewTrafficHistory, onViewConnections, showHidden = false, autoSortActivity = false, gatewayTotal = 0, deviceGrandTotal = 0, dragIndex = null }) {
  const { t, lang } = useI18n();
  const [isUpdating, setIsUpdating] = useState(false);
  const [customDown, setCustomDown] = useState('');
  const [customUp, setCustomUp] = useState('');
  const [selectedDevice, setSelectedDevice] = useState(null);

  // Keep the limit fields in step with the stored value. RouterOS Simple Queue
  // format is "upload/download"; "unlimited" / "default" / "0/0" read as blank,
  // which is also how the operator clears a limit.
  useEffect(() => {
    const raw = user.speed_limit;
    if (!raw || raw === 'unlimited' || raw === 'default' || raw === '0/0') {
      setCustomUp('');
      setCustomDown('');
    } else if (raw.includes('/')) {
      const [up, down] = raw.split('/');
      setCustomUp(up || '');
      setCustomDown(down || '');
    } else {
      setCustomUp(raw);
      setCustomDown(raw);
    }
  }, [user.speed_limit]);

  // The current limit per direction, used as the field placeholder so an empty
  // box still says what is in force ("unlimited" when nothing is set) rather
  // than a made-up example.
  const _lim = user.speed_limit;
  const _limParts = _lim && _lim.includes('/') ? _lim.split('/') : null;
  const _limPlain = _lim && !['unlimited', 'default', '0/0', ''].includes(_lim) && !_limParts ? _lim : '';
  const upPlaceholder = (_limParts ? _limParts[0] : _limPlain) || t('unlimited');
  const downPlaceholder = (_limParts ? _limParts[1] : _limPlain) || t('unlimited');

  const visibleDevices = (user.devices || []).filter(d => showHidden || !d.is_hidden);
  // Adapters of one machine collapse into a single row.
  const rawDeviceGroups = groupDevices(visibleDevices);
  const deviceGroups = autoSortActivity
    ? [...rawDeviceGroups].sort((a, b) => {
        // 1. Current live moving rate (highest first)
        const rateA = (a.rateIn || 0) + (a.rateOut || 0);
        const rateB = (b.rateIn || 0) + (b.rateOut || 0);
        if (rateB !== rateA) {
          return rateB - rateA;
        }
        // 2. Active status (active first)
        if (a.isActive !== b.isActive) {
          return a.isActive ? -1 : 1;
        }
        // 3. Total downloaded volume (all-time highest first)
        const volA = (a.bytesTotalIn || 0) + (a.bytesTotalOut || 0);
        const volB = (b.bytesTotalIn || 0) + (b.bytesTotalOut || 0);
        if (volB !== volA) {
          return volB - volA;
        }
        // 4. Today volume fallback
        const todayA = (a.bytesIn || 0) + (a.bytesOut || 0);
        const todayB = (b.bytesIn || 0) + (b.bytesOut || 0);
        return todayB - todayA;
      })
    : rawDeviceGroups;
  const activeDevices = deviceGroups.filter(g => g.isActive);
  const isPaused = user.is_paused;
  const isOnline = activeDevices.length > 0 && !isPaused;

  const todayTotal = (user.bytes_today_in || 0) + (user.bytes_today_out || 0);
  const sharePct = gatewayTotal > 0 ? (todayTotal / gatewayTotal) * 100 : 0;
  const cycleTotal = (user.bytes_cycle_in || 0) + (user.bytes_cycle_out || 0);
  const allTimeTotal = (user.bytes_total_in || 0) + (user.bytes_total_out || 0);

  const handleApplyCustom = async () => {
    let down = customDown.trim();
    let up = customUp.trim();

    // Both fields empty is an explicit "remove the limit".
    if (!down && !up) {
      setIsUpdating(true);
      try {
        await onLimitChange(user.id, 'unlimited');
      } finally {
        setIsUpdating(false);
      }
      return;
    }

    // One side left blank mirrors the other, so a single figure caps both ways.
    if (!down) down = up;
    if (!up) up = down;

    // A bare number is taken as megabits; an explicit K / M / G suffix is kept
    // as typed so 512K or 1G work.
    if (/^\d+$/.test(down)) down += 'M';
    if (/^\d+$/.test(up)) up += 'M';

    const formatted = `${up}/${down}`; // RouterOS Simple Queue format: upload/download
    setIsUpdating(true);
    try {
      await onLimitChange(user.id, formatted);
    } finally {
      setIsUpdating(false);
    }
  };

  const handlePauseClick = async () => {
    setIsUpdating(true);
    try {
      await onPauseToggle(user.id, !isPaused);
    } finally {
      setIsUpdating(false);
    }
  };

  return (
    <div className="card" style={{
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'space-between',
      gap: 12,
      padding: '14px 16px',
      borderLeft: `4px solid ${isPaused ? 'var(--color-danger)' : (isOnline ? 'var(--color-primary)' : 'var(--border-color)')}`
    }}>
      <div>
        {/* Header — identity, status and today's consumption in one band */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }}>
            {dragIndex !== null && (
              <GripVertical
                size={14}
                style={{ color: 'var(--text-muted)', cursor: 'grab', flexShrink: 0 }}
                title={t('drag_to_reorder')}
              />
            )}
            <div style={{
              background: isPaused ? 'var(--color-danger-bg)' : 'var(--bg-secondary)',
              color: isPaused ? 'var(--color-danger)' : 'var(--color-primary)',
              width: 30,
              height: 30,
              borderRadius: 'var(--radius-full)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0
            }}>
              <UserIcon size={16} />
            </div>
            <div style={{ minWidth: 0 }}>
              <div className="truncate" style={{
                fontWeight: 700,
                fontSize: 'var(--fs-lg)',
                color: 'var(--text-primary)'
              }}>
                {user.name}
              </div>
              <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
                {activeDevices.length}/{deviceGroups.length} {t('online_of_devices')}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
            <span className={`badge ${isPaused ? 'badge-danger' : (isOnline ? 'badge-success' : 'badge-neutral')}`}>
              {isPaused ? t('paused') : (isOnline ? t('active_now') : t('idle'))}
            </span>
            {onViewTrafficHistory && (
              <button
                className="btn-icon"
                onClick={() => onViewTrafficHistory({ type: 'user', id: user.id, name: user.name })}
                title={t('view_traffic_history')}
                style={{ width: 26, height: 26 }}
              >
                <BarChart2 size={13} />
              </button>
            )}
            <button className="btn-icon" onClick={() => onEdit(user)} title={t('edit_user')} style={{ width: 26, height: 26 }}>
              <Settings size={13} />
            </button>
            <button className="btn-icon" onClick={() => onDelete(user.id)} title={t('delete_user')} style={{ width: 26, height: 26, color: 'var(--color-danger)' }}>
              <Trash2 size={13} />
            </button>
          </div>
        </div>

        {/* Live throughput + today's volume — the headline figures of the card.
            Previously styled like any other muted panel, which left the numbers
            people actually open this card to read looking like secondary detail.
            It now carries its own surface, a live accent while traffic is
            moving, and figures a full step larger than the device rows beneath
            it, so the eye lands on the user's totals before the per-device
            breakdown. */}
        <div className={`usage-panel${isOnline ? ' is-live' : ''}`}>
          <div className="usage-metric">
            <ArrowDown size={16} style={{ color: isOnline ? 'var(--color-success)' : 'var(--text-muted)', flexShrink: 0 }} />
            <div style={{ minWidth: 0 }}>
              <div className="usage-figure font-mono" style={{ color: isOnline ? 'var(--color-success)' : 'var(--text-muted)' }}>
                {formatSpeed(user.current_rate_in || 0)}
              </div>
              <div className="usage-caption font-mono">
                {formatBytes(user.bytes_today_in || 0)}
              </div>
            </div>
          </div>

          <div className="usage-divider" />

          <div className="usage-metric">
            <ArrowUp size={16} style={{ color: isOnline ? 'var(--color-primary)' : 'var(--text-muted)', flexShrink: 0 }} />
            <div style={{ minWidth: 0 }}>
              <div className="usage-figure font-mono" style={{ color: isOnline ? 'var(--color-primary)' : 'var(--text-muted)' }}>
                {formatSpeed(user.current_rate_out || 0)}
              </div>
              <div className="usage-caption font-mono">
                {formatBytes(user.bytes_today_out || 0)}
              </div>
            </div>
          </div>

          <div style={{ flex: 1 }} />

          {/* Share of today's gateway traffic */}
          <div className="usage-total">
            <div className="usage-figure font-mono" style={{ color: 'var(--text-primary)' }}>
              {formatBytes(todayTotal)}
            </div>
            <div className="usage-caption font-mono">
              {gatewayTotal > 0 ? `${t('today_scope')} · ${sharePct.toFixed(1)}%` : t('today_scope')}
            </div>
          </div>
        </div>

        {/* Reference strip: this user's volume over the billing cycle and over
            all recorded history, plus how long since any of their devices was
            last seen. The panel above is "right now / today"; this is the
            longer view, kept deliberately small. */}
        <div style={{
          marginTop: 8,
          display: 'flex',
          flexWrap: 'wrap',
          gap: '4px 14px',
          fontSize: 'var(--fs-2xs)',
          color: 'var(--text-muted)'
        }}>
          <span>
            {t('col_cycle')}: <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>{formatBytes(cycleTotal)}</span>
          </span>
          <span>
            {t('all_time_label')}: <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>{formatBytes(allTimeTotal)}</span>
          </span>
          <span title={user.last_seen ? formatDateTime(user.last_seen, lang) : t('last_active_never')}>
            {t('col_last_active')}: <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>
              {user.last_seen ? (isOnline ? (lang === 'ru' ? 'сейчас' : 'now') : formatLastActive(user.last_seen, lang)) : t('last_active_never')}
            </span>
          </span>
        </div>

        {/* Associated devices — two lines each, see DeviceRow */}
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 5 }}>
          {deviceGroups.map(g => (
            <DeviceRow
              key={g.key}
              group={g}
              t={t}
              lang={lang}
              grandTotal={deviceGrandTotal}
              onOpen={() => setSelectedDevice(g.primary)}
              onUpdate={onUpdate}
              onViewTrafficHistory={onViewTrafficHistory}
              onViewConnections={onViewConnections}
            />
          ))}
          {deviceGroups.length === 0 && (
            <div style={{
              fontSize: 'var(--fs-xs)',
              color: 'var(--text-muted)',
              textAlign: 'center',
              padding: '10px 0',
              border: '1px dashed var(--border-color)',
              borderRadius: 'var(--radius-sm)'
            }}>
              {t('no_devices_assigned')}
            </div>
          )}
        </div>
      </div>

      {/* Footer: per-user bandwidth cap and the pause toggle.
          Down / Up / Apply are always on screen - no preset list, no expand
          step. Empty both fields and Apply to lift the cap. The K / M / G rule
          is a tooltip on the dotted-underlined Down / Up labels, not a
          permanent line of small print under a cramped row. */}
      <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: 10 }}>
        {/* Two deterministic rows - inputs, then buttons - so a longer set of
            translated labels ("Применить" vs "Apply") cannot push a control
            onto its own line and stretch the card. */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <label
              title={t('limit_units_hint')}
              style={{ fontSize: 'var(--fs-2xs)', color: 'var(--color-success)', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 3, marginBottom: 3, cursor: 'help' }}
            >
              <ArrowDown size={11} />
              <span style={{ borderBottom: '1px dotted currentColor', lineHeight: 1.1 }}>{t('download_limit')}</span>
            </label>
            <input
              type="text"
              className="form-input font-mono"
              style={{ padding: '4px 6px', fontSize: 'var(--fs-sm)', height: 30, width: '100%' }}
              placeholder={downPlaceholder}
              value={customDown}
              onChange={e => setCustomDown(e.target.value)}
            />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <label
              title={t('limit_units_hint')}
              style={{ fontSize: 'var(--fs-2xs)', color: 'var(--color-primary)', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 3, marginBottom: 3, cursor: 'help' }}
            >
              <ArrowUp size={11} />
              <span style={{ borderBottom: '1px dotted currentColor', lineHeight: 1.1 }}>{t('upload_limit')}</span>
            </label>
            <input
              type="text"
              className="form-input font-mono"
              style={{ padding: '4px 6px', fontSize: 'var(--fs-sm)', height: 30, width: '100%' }}
              placeholder={upPlaceholder}
              value={customUp}
              onChange={e => setCustomUp(e.target.value)}
            />
          </div>
        </div>
        {/* Two mirrored halves: identical geometry, only the colour and the
            icon differ so Apply / Pause read as a matched pair. */}
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={handleApplyCustom}
            disabled={isUpdating}
            style={FOOTER_BTN_STYLE}
          >
            <Check size={13} style={{ flexShrink: 0 }} />
            <span className="truncate">{t('apply_limit')}</span>
          </button>
          <button
            type="button"
            className={`btn ${isPaused ? 'btn-primary' : 'btn-danger'} btn-sm`}
            onClick={handlePauseClick}
            disabled={isUpdating}
            style={FOOTER_BTN_STYLE}
          >
            {isPaused ? <Play size={13} style={{ flexShrink: 0 }} /> : <Pause size={13} style={{ flexShrink: 0 }} />}
            <span className="truncate">{isPaused ? t('resume_btn') : t('pause_btn')}</span>
          </button>
        </div>
      </div>

      {selectedDevice && (
        <DeviceModal
          device={selectedDevice}
          user={user}
          users={users}
          onClose={() => setSelectedDevice(null)}
          onUpdated={onUpdate}
          onViewConnections={onViewConnections}
        />
      )}
    </div>
  );
}
