import React, { useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { formatSpeed, formatSpeedShort, formatBytes, formatGbWhole, formatRelativeTime } from '../utils/formatters';
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
  X,
  Sliders,
  GripVertical
} from 'lucide-react';

const SPEED_PRESETS = [
  { label: '⚡ Unlimited (Max speed)', value: 'unlimited' },
  { label: '↓ 15 Mbps (Down) / ↑ 5 Mbps (Up) — Light', value: '5M/15M' },
  { label: '↓ 30 Mbps (Down) / ↑ 10 Mbps (Up) — Standard', value: '10M/30M' },
  { label: '↓ 50 Mbps (Down) / ↑ 25 Mbps (Up) — Fast', value: '25M/50M' },
  { label: '↓ 50 Mbps / ↑ 50 Mbps — Symmetric', value: '50M/50M' },
  { label: '↓ 100 Mbps (Down) / ↑ 50 Mbps (Up) — Super', value: '50M/100M' },
  { label: '↓ 100 Mbps / ↑ 100 Mbps — Symmetric', value: '100M/100M' },
  { label: '↓ 200 Mbps (Down) / ↑ 100 Mbps (Up) — Ultra', value: '100M/200M' },
  { label: '✏️ Custom manual limit...', value: 'custom' },
];

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
function DeviceRow({ group, t, lang, grandTotal = 0, onOpen, onUpdate }) {
  const [busy, setBusy] = useState(false);

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

  // Compact volume readout shown beside the name: whole GB, "today / all-time /
  // share of every device's all-time traffic". The exact bytes and the legend
  // for the three fields are in the tooltip.
  const volToday = group.bytesIn + group.bytesOut;
  const volTotal = group.bytesTotalIn + group.bytesTotalOut;
  const volShare = grandTotal > 0 ? Math.round((volTotal / grandTotal) * 100) : 0;
  const showVolume = volTotal > 0 || volToday > 0;
  const volTitle =
    `${t('device_volume_legend')}\n` +
    `${t('today_scope')}: ↓ ${formatBytes(group.bytesIn)} · ↑ ${formatBytes(group.bytesOut)}\n` +
    `${t('all_time_label')}: ↓ ${formatBytes(group.bytesTotalIn)} · ↑ ${formatBytes(group.bytesTotalOut)}`;

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
      {/* Line 1 — identity and, when it is moving, the live rate. */}
      <div className="drow-main">
        <span
          className="status-dot"
          style={{
            width: 7,
            height: 7,
            flexShrink: 0,
            background: group.isPaused ? 'var(--color-danger)' : (group.isActive ? 'var(--color-success)' : 'var(--text-muted)'),
            boxShadow: group.isActive && !group.isPaused ? '0 0 6px rgba(16, 185, 129, 0.5)' : 'none'
          }}
          title={group.isPaused ? t('paused') : (group.isActive ? t('online') : t('offline'))}
        />
        <span style={{ flexShrink: 0, display: 'flex', color: 'var(--text-secondary)' }}>
          {getDeviceIcon(d.vendor, d.hostname)}
        </span>
        <span className="drow-name" title={deviceName}>{deviceName}</span>

        {showVolume && (
          <span className="drow-vol font-mono" title={volTitle}>
            {formatGbWhole(volToday)}<span className="drow-vol-sep">/</span>
            {formatGbWhole(volTotal)}<span className="drow-vol-sep">/</span>
            {volShare}%
          </span>
        )}

        {multiHomed && (
          <span className="badge badge-chip" title={t('multi_adapter_hint')}>
            {group.adapters.length}×
          </span>
        )}
        {hasCustomLimit && (
          <span className="badge badge-chip badge-chip-warn" title={`${t('table_speed_limit')}: ${d.speed_limit}`}>
            ⚡ {d.speed_limit}
          </span>
        )}

        {isMoving && (
          <span className="drow-rate">
            <span style={{ color: rateIn ? 'var(--color-success)' : 'var(--text-muted)' }}>↓ {formatSpeedShort(rateIn)}</span>
            <span style={{ color: rateOut ? 'var(--color-primary)' : 'var(--text-muted)' }}>↑ {formatSpeedShort(rateOut)}</span>
          </span>
        )}
      </div>

      {/* Line 2 — address, vendor and staleness in one truncating run, then the
          two actions pinned to the right where they cannot be clipped. */}
      <div className="drow-sub">
        <span className="drow-facts">
          <span className="font-mono">{d.ip_address || d.mac_address}</span>
          {vendorLabel && <> · {vendorLabel}</>}
          {d.is_hidden && <> · {t('hidden_badge')}</>}
          {offline && d.last_seen && (
            <> · {t('last_seen_ago', { time: formatRelativeTime(d.last_seen, lang) })}</>
          )}
        </span>

        <span className="drow-actions">
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

      {/* Line 3 — how it is connected. Present only for an active device with a
          radio link; it wraps rather than truncates, since each token
          (interface, band, signal) is meaningless cut in half. */}
      {links.length > 0 && (
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
        </div>
      )}
    </div>
  );
}

export function UserCard({ user, onEdit, onDelete, onLimitChange, onPauseToggle, onUpdate, showHidden = false, gatewayTotal = 0, deviceGrandTotal = 0, dragIndex = null }) {
  const { t, lang } = useI18n();
  const [isUpdating, setIsUpdating] = useState(false);
  const [showCustomInput, setShowCustomInput] = useState(false);
  const [customDown, setCustomDown] = useState('');
  const [customUp, setCustomUp] = useState('');
  const [selectedDevice, setSelectedDevice] = useState(null);

  const visibleDevices = (user.devices || []).filter(d => showHidden || !d.is_hidden);
  // Adapters of one machine collapse into a single row.
  const deviceGroups = groupDevices(visibleDevices);
  const activeDevices = deviceGroups.filter(g => g.isActive);
  const isPaused = user.is_paused;
  const isOnline = activeDevices.length > 0 && !isPaused;

  const todayTotal = (user.bytes_today_in || 0) + (user.bytes_today_out || 0);
  const sharePct = gatewayTotal > 0 ? (todayTotal / gatewayTotal) * 100 : 0;

  const currentLimit = user.speed_limit || 'unlimited';
  const isKnownPreset = SPEED_PRESETS.some(p => p.value === currentLimit);

  const handleLimitSelect = async (e) => {
    const val = e.target.value;
    if (val === 'custom') {
      if (currentLimit.includes('/')) {
        const [up, down] = currentLimit.split('/');
        setCustomUp(up);
        setCustomDown(down);
      } else if (currentLimit !== 'unlimited') {
        setCustomUp(currentLimit);
        setCustomDown(currentLimit);
      } else {
        setCustomDown('50M');
        setCustomUp('20M');
      }
      setShowCustomInput(true);
      return;
    }

    setIsUpdating(true);
    try {
      await onLimitChange(user.id, val);
    } finally {
      setIsUpdating(false);
    }
  };

  const handleApplyCustom = async () => {
    let down = customDown.trim();
    let up = customUp.trim();
    if (!down && !up) return;

    if (!down) down = up;
    if (!up) up = down;

    if (/^\d+$/.test(down)) down += 'M';
    if (/^\d+$/.test(up)) up += 'M';

    const formatted = `${up}/${down}`; // RouterOS Simple Queue format: upload/download
    setIsUpdating(true);
    try {
      await onLimitChange(user.id, formatted);
      setShowCustomInput(false);
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

      {/* Footer: speed limiter and pause toggle */}
      <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: 10 }}>
        {showCustomInput ? (
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            background: 'var(--bg-secondary)',
            padding: 10,
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--border-color)'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 4 }}>
                <Sliders size={13} style={{ color: 'var(--color-primary)' }} />
                {t('custom_limit_title')}
              </span>
              <button
                type="button"
                className="btn-icon"
                onClick={() => setShowCustomInput(false)}
                style={{ width: 22, height: 22 }}
                title={t('cancel')}
              >
                <X size={13} />
              </button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              <div>
                <label style={{ fontSize: 'var(--fs-2xs)', color: 'var(--color-success)', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 3, marginBottom: 3 }}>
                  <ArrowDown size={11} /> {t('download_limit')}
                </label>
                <input
                  type="text"
                  className="form-input font-mono"
                  style={{ padding: '4px 6px', fontSize: 'var(--fs-sm)', height: 30 }}
                  placeholder="50M or 100M"
                  value={customDown}
                  onChange={e => setCustomDown(e.target.value)}
                  autoFocus
                />
              </div>
              <div>
                <label style={{ fontSize: 'var(--fs-2xs)', color: 'var(--color-primary)', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 3, marginBottom: 3 }}>
                  <ArrowUp size={11} /> {t('upload_limit')}
                </label>
                <input
                  type="text"
                  className="form-input font-mono"
                  style={{ padding: '4px 6px', fontSize: 'var(--fs-sm)', height: 30 }}
                  placeholder="20M or 50M"
                  value={customUp}
                  onChange={e => setCustomUp(e.target.value)}
                />
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 6, marginTop: 2 }}>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setShowCustomInput(false)}
                style={{ fontSize: 'var(--fs-xs)', height: 26, padding: '2px 8px' }}
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleApplyCustom}
                disabled={isUpdating || (!customDown.trim() && !customUp.trim())}
                style={{ fontSize: 'var(--fs-xs)', height: 26, padding: '2px 10px', display: 'flex', alignItems: 'center', gap: 4 }}
              >
                <Check size={12} />
                {t('apply_limit')}
              </button>
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <select
                className="form-select"
                style={{ width: '100%', padding: '5px 8px', fontSize: 'var(--fs-sm)' }}
                value={isKnownPreset ? currentLimit : 'custom'}
                onChange={handleLimitSelect}
                disabled={isUpdating}
              >
                {!isKnownPreset && (
                  <option value="custom">
                    {formatLimitSummary(currentLimit)} (Custom)
                  </option>
                )}
                {SPEED_PRESETS.map(p => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>

            <button
              className={`btn ${isPaused ? 'btn-primary' : 'btn-danger'} btn-sm`}
              onClick={handlePauseClick}
              disabled={isUpdating}
              style={{ whiteSpace: 'nowrap' }}
            >
              {isPaused ? <Play size={14} /> : <Pause size={14} />}
              {isPaused ? t('resume_btn') : t('pause_btn')}
            </button>
          </div>
        )}
      </div>

      {selectedDevice && (
        <DeviceModal
          device={selectedDevice}
          user={user}
          onClose={() => setSelectedDevice(null)}
          onUpdated={onUpdate}
        />
      )}
    </div>
  );
}
