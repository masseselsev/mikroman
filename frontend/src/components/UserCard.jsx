import React, { useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { formatSpeed, formatBytes, formatRelativeTime } from '../utils/formatters';
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
  Sliders
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
function signalColor(dbm) {
  if (dbm > -65) return 'var(--color-success)';
  if (dbm > -80) return 'var(--color-warning)';
  return 'var(--color-danger)';
}

const META_SEP = <span style={{ opacity: 0.4 }}>·</span>;

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
function connectionLinks(device) {
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
function bandLabel(band) {
  if (!band) return null;
  const [freq, mode] = band.split('-');
  const shortFreq = freq.replace('ghz', 'G').replace('2', '2.4');
  return mode ? `${shortFreq}·${mode.toUpperCase()}` : shortFreq;
}

/**
 * A single device, rendered on two lines.
 *
 * Line 1 answers "is this device using bandwidth right now"; line 2 answers
 * "what and where is it, and how much has it used today". Splitting them means
 * the name no longer has to compete with the metadata for width, so it stops
 * being truncated to "Nama...".
 */
function DeviceRow({ group, t, lang, onOpen, onUpdate }) {
  const [busy, setBusy] = useState(false);

  const d = group.primary;
  const rateIn = group.rateIn;
  const rateOut = group.rateOut;
  const isMoving = rateIn > 0 || rateOut > 0;
  const offline = !group.isActive;
  const multiHomed = group.adapters.length > 1;

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
      onClick={onOpen}
      title={t('device_row_hint')}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '6px 9px',
        background: 'var(--bg-secondary)',
        borderRadius: 'var(--radius-sm)',
        border: '1px solid var(--border-color)',
        opacity: group.isPaused ? 0.6 : (offline ? 0.78 : 1),
        cursor: 'pointer',
        transition: 'border-color 0.15s ease'
      }}
    >
      {/* Text column. minWidth:0 lets it shrink below its content width, which
          is what stops the action buttons being pushed outside the card. */}
      <div style={{ flex: '1 1 auto', minWidth: 0 }}>
        {/* Line 1 — identity and live throughput */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          <span
            style={{
              width: 7,
              height: 7,
              borderRadius: '50%',
              flexShrink: 0,
              background: group.isPaused ? 'var(--color-danger)' : (group.isActive ? 'var(--color-success)' : 'var(--text-muted)'),
              boxShadow: group.isActive && !group.isPaused ? '0 0 6px rgba(16, 185, 129, 0.5)' : 'none'
            }}
            title={group.isPaused ? t('paused') : (group.isActive ? t('online') : t('offline'))}
          />
          <span style={{ flexShrink: 0, display: 'flex', color: 'var(--text-secondary)' }}>
            {getDeviceIcon(d.vendor, d.hostname)}
          </span>
          <span style={{
            fontWeight: 600,
            fontSize: '0.8125rem',
            color: 'var(--text-primary)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            minWidth: 0
          }}>
            {d.custom_name || d.hostname || d.vendor || 'Device'}
          </span>

          {multiHomed && (
            <span className="badge badge-chip" title={t('multi_adapter_hint')}>
              {group.adapters.length}×
            </span>
          )}
          {d.is_hidden && (
            <span className="badge badge-chip" title={t('hidden_badge')}>{t('hidden_badge')}</span>
          )}
          {d.speed_limit && d.speed_limit !== 'default' && (
            <span className="badge badge-chip badge-chip-warn" title={`${t('table_speed_limit')}: ${d.speed_limit}`}>
              ⚡ {d.speed_limit}
            </span>
          )}
          {d.is_randomized_mac && (
            <span className="badge badge-chip badge-chip-warn" title={t('private_mac_hint')}>
              {t('private_badge')}
            </span>
          )}

          <span style={{ flex: 1, minWidth: 4 }} />

          {/* Live rate. Dimmed when idle so a quiet device reads as quiet. */}
          <span className="font-mono" style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: '0.75rem',
            flexShrink: 0,
            fontWeight: 700
          }}>
            <span style={{ color: isMoving && rateIn ? 'var(--color-success)' : 'var(--text-muted)' }}>
              ↓ {formatSpeed(rateIn)}
            </span>
            <span style={{ color: isMoving && rateOut ? 'var(--color-primary)' : 'var(--text-muted)' }}>
              ↑ {formatSpeed(rateOut)}
            </span>
          </span>
        </div>

        {/* Line 2 - identity. Kept apart from connectivity so neither has to
            compete for width; the vendor is the only element allowed to give way. */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 5,
          marginTop: 2,
          paddingLeft: 14,
          fontSize: '0.6875rem',
          color: 'var(--text-muted)',
          minWidth: 0,
          overflow: 'hidden'
        }}>
          <span className="font-mono" style={{ flexShrink: 0 }}>{d.ip_address || d.mac_address}</span>
          {d.vendor && <>{META_SEP}<span style={{
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap'
          }}>{d.vendor}</span></>}
          {offline && d.last_seen && (
            <>{META_SEP}<span style={{ flexShrink: 0 }}>
              {t('last_seen_ago', { time: formatRelativeTime(d.last_seen, lang) })}
            </span></>
          )}
        </div>

        {/* Line 3 - how it is connected, and what it has consumed today */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          marginTop: 2,
          paddingLeft: 14,
          fontSize: '0.6875rem',
          color: 'var(--text-muted)',
          minWidth: 0
        }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            minWidth: 0,
            overflow: 'hidden',
            flex: '1 1 auto'
          }}>
            {/* One entry per live connection. A dual-homed machine shows both
                its adapters; a WiFi 7 multi-link client shows each radio it is
                bonded over, since 'mld1' names no actual radio. */}
            {group.adapters.filter(a => a.is_active).flatMap(adapter =>
              connectionLinks(adapter).map((link, i) => (
                <span
                  key={`${adapter.id}-${link.interface}-${i}`}
                  style={{ display: 'flex', alignItems: 'center', gap: 3, flexShrink: 0 }}
                  title={link.band ? `${link.interface} - ${link.band}` : link.interface}
                >
                  {link.wireless ? <Wifi size={10} /> : <Cable size={10} />}
                  {link.interface}
                  {link.band && <span style={{ opacity: 0.65 }}>{bandLabel(link.band)}</span>}
                  {link.signal != null && (
                    <span className="font-mono" style={{ color: signalColor(link.signal) }}>
                      {link.signal}
                    </span>
                  )}
                </span>
              ))
            )}
          </div>

          <span className="font-mono" style={{ display: 'flex', gap: 7, flexShrink: 0 }}>
            <span title={t('today_download')}>↓ {formatBytes(group.bytesIn)}</span>
            <span title={t('today_upload')}>↑ {formatBytes(group.bytesOut)}</span>
          </span>
        </div>
      </div>

      {/* Action column, outside the text flow so it can never be clipped. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
        <button
          type="button"
          onClick={togglePause}
          disabled={busy}
          className="btn-icon"
          style={{
            width: 26,
            height: 26,
            background: group.isPaused ? 'var(--color-danger)' : 'var(--bg-card)',
            color: group.isPaused ? '#fff' : 'var(--text-secondary)',
            border: '1px solid var(--border-color)'
          }}
          title={group.isPaused ? t('resume_device') : t('pause_device')}
        >
          {group.isPaused ? <Play size={12} /> : <Pause size={12} />}
        </button>

        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onOpen(); }}
          className="btn-icon"
          style={{
            width: 26,
            height: 26,
            background: 'var(--bg-card)',
            color: 'var(--text-secondary)',
            border: '1px solid var(--border-color)'
          }}
          title={t('edit_device')}
        >
          <Sliders size={12} />
        </button>
      </div>
    </div>
  );
}

export function UserCard({ user, onEdit, onDelete, onLimitChange, onPauseToggle, onUpdate, showHidden = false, gatewayTotal = 0 }) {
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
      padding: 14,
      borderLeft: `4px solid ${isPaused ? 'var(--color-danger)' : (isOnline ? 'var(--color-primary)' : 'var(--border-color)')}`
    }}>
      <div>
        {/* Header — identity, status and today's consumption in one band */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, minWidth: 0 }}>
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
              <div style={{
                fontWeight: 700,
                fontSize: '1rem',
                color: 'var(--text-primary)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap'
              }}>
                {user.name}
              </div>
              <div style={{ fontSize: '0.6875rem', color: 'var(--text-muted)' }}>
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

        {/* Live throughput + today's volume. bytes_today_* was previously
            fetched by the app and never displayed anywhere. */}
        <div style={{
          background: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-md)',
          padding: '8px 12px',
          marginTop: 10,
          display: 'flex',
          alignItems: 'center',
          gap: 10
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
            <ArrowDown size={15} style={{ color: isOnline ? 'var(--color-success)' : 'var(--text-muted)', flexShrink: 0 }} />
            <div style={{ minWidth: 0 }}>
              <div className="font-mono" style={{ fontSize: '0.9rem', fontWeight: 800, color: isOnline ? 'var(--color-success)' : 'var(--text-muted)', lineHeight: 1.15 }}>
                {formatSpeed(user.current_rate_in || 0)}
              </div>
              <div className="font-mono" style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>
                {formatBytes(user.bytes_today_in || 0)}
              </div>
            </div>
          </div>

          <div style={{ width: 1, height: 26, background: 'var(--border-color)', flexShrink: 0 }} />

          <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
            <ArrowUp size={15} style={{ color: isOnline ? 'var(--color-primary)' : 'var(--text-muted)', flexShrink: 0 }} />
            <div style={{ minWidth: 0 }}>
              <div className="font-mono" style={{ fontSize: '0.9rem', fontWeight: 800, color: isOnline ? 'var(--color-primary)' : 'var(--text-muted)', lineHeight: 1.15 }}>
                {formatSpeed(user.current_rate_out || 0)}
              </div>
              <div className="font-mono" style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>
                {formatBytes(user.bytes_today_out || 0)}
              </div>
            </div>
          </div>

          <div style={{ flex: 1 }} />

          {/* Share of today's gateway traffic */}
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            <div className="font-mono" style={{ fontSize: '0.9rem', fontWeight: 800, color: 'var(--text-primary)', lineHeight: 1.15 }}>
              {formatBytes(todayTotal)}
            </div>
            <div className="font-mono" style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>
              {gatewayTotal > 0 ? `${sharePct.toFixed(1)}% ${t('of_total')}` : t('today_label')}
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
              onOpen={() => setSelectedDevice(g.primary)}
              onUpdate={onUpdate}
            />
          ))}
          {deviceGroups.length === 0 && (
            <div style={{
              fontSize: '0.75rem',
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
            borderRadius: 6,
            border: '1px solid var(--border-color)'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: 4 }}>
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
                <label style={{ fontSize: '0.675rem', color: 'var(--color-success)', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 3, marginBottom: 3 }}>
                  <ArrowDown size={11} /> {t('download_limit')}
                </label>
                <input
                  type="text"
                  className="form-input font-mono"
                  style={{ padding: '4px 6px', fontSize: '0.8rem', height: 30 }}
                  placeholder="50M or 100M"
                  value={customDown}
                  onChange={e => setCustomDown(e.target.value)}
                  autoFocus
                />
              </div>
              <div>
                <label style={{ fontSize: '0.675rem', color: 'var(--color-primary)', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 3, marginBottom: 3 }}>
                  <ArrowUp size={11} /> {t('upload_limit')}
                </label>
                <input
                  type="text"
                  className="form-input font-mono"
                  style={{ padding: '4px 6px', fontSize: '0.8rem', height: 30 }}
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
                style={{ fontSize: '0.75rem', height: 26, padding: '2px 8px' }}
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleApplyCustom}
                disabled={isUpdating || (!customDown.trim() && !customUp.trim())}
                style={{ fontSize: '0.75rem', height: 26, padding: '2px 10px', display: 'flex', alignItems: 'center', gap: 4 }}
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
                style={{ width: '100%', padding: '5px 8px', fontSize: '0.78rem' }}
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
