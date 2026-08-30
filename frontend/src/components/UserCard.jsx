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
 * A single device, rendered on two lines.
 *
 * Line 1 answers "is this device using bandwidth right now"; line 2 answers
 * "what and where is it, and how much has it used today". Splitting them means
 * the name no longer has to compete with the metadata for width, so it stops
 * being truncated to "Nama...".
 */
function DeviceRow({ device: d, t, lang, onOpen, onUpdate }) {
  const [busy, setBusy] = useState(false);

  const rateIn = d.current_rate_in || 0;
  const rateOut = d.current_rate_out || 0;
  const isMoving = rateIn > 0 || rateOut > 0;
  const offline = !d.is_active;

  const togglePause = async (e) => {
    e.stopPropagation();
    setBusy(true);
    try {
      await api.toggleDevicePause(d.id, !d.is_paused);
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
        padding: '6px 9px',
        background: 'var(--bg-secondary)',
        borderRadius: 'var(--radius-sm)',
        border: '1px solid var(--border-color)',
        opacity: d.is_paused ? 0.6 : (offline ? 0.78 : 1),
        cursor: 'pointer',
        transition: 'border-color 0.15s ease'
      }}
    >
      {/* Line 1 — identity and live throughput */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
        <span
          style={{
            width: 7,
            height: 7,
            borderRadius: '50%',
            flexShrink: 0,
            background: d.is_paused ? 'var(--color-danger)' : (d.is_active ? 'var(--color-success)' : 'var(--text-muted)'),
            boxShadow: d.is_active && !d.is_paused ? '0 0 6px rgba(16, 185, 129, 0.5)' : 'none'
          }}
          title={d.is_paused ? t('paused') : (d.is_active ? t('online') : t('offline'))}
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

        <span style={{ flex: 1 }} />

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

      {/* Line 2 — where it is, and what it has consumed today */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        marginTop: 3,
        paddingLeft: 14,
        fontSize: '0.6875rem',
        color: 'var(--text-muted)',
        minWidth: 0
      }}>
        <span className="font-mono" style={{ flexShrink: 0 }}>{d.ip_address || d.mac_address}</span>

        {d.vendor && <>{META_SEP}<span style={{
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
        }}>{d.vendor}</span></>}

        {d.last_interface && <>{META_SEP}<span style={{ display: 'flex', alignItems: 'center', gap: 3, flexShrink: 0 }}>
          {d.last_wifi_signal ? <Wifi size={10} /> : <Cable size={10} />}
          {d.last_interface}
        </span></>}

        {/* A stale signal reading is meaningless once the device has left. */}
        {!offline && d.last_wifi_signal ? (
          <>{META_SEP}<span className="font-mono" style={{ color: signalColor(d.last_wifi_signal), flexShrink: 0 }}>
            {d.last_wifi_signal} dBm
          </span></>
        ) : null}

        {offline && d.last_seen && (
          <>{META_SEP}<span style={{ flexShrink: 0 }}>
            {t('last_seen_ago', { time: formatRelativeTime(d.last_seen, lang) })}
          </span></>
        )}

        <span style={{ flex: 1 }} />

        {/* Today's volume for this specific device */}
        <span className="font-mono" style={{ display: 'flex', gap: 7, flexShrink: 0 }}>
          <span title={t('today_download')}>↓ {formatBytes(d.bytes_today_in || 0)}</span>
          <span title={t('today_upload')}>↑ {formatBytes(d.bytes_today_out || 0)}</span>
        </span>

        <button
          type="button"
          onClick={togglePause}
          disabled={busy}
          className="btn-icon"
          style={{
            width: 22,
            height: 22,
            flexShrink: 0,
            background: d.is_paused ? 'var(--color-danger)' : 'transparent',
            color: d.is_paused ? '#fff' : 'var(--text-muted)'
          }}
          title={d.is_paused ? t('resume_device') : t('pause_device')}
        >
          {d.is_paused ? <Play size={11} /> : <Pause size={11} />}
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
  const activeDevices = visibleDevices.filter(d => d.is_active);
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
                {activeDevices.length}/{visibleDevices.length} {t('online_of_devices')}
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
          {visibleDevices.map(d => (
            <DeviceRow
              key={d.id}
              device={d}
              t={t}
              lang={lang}
              onOpen={() => setSelectedDevice(d)}
              onUpdate={onUpdate}
            />
          ))}
          {visibleDevices.length === 0 && (
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
