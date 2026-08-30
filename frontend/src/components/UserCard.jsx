import React, { useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { formatSpeed, formatBytes } from '../utils/formatters';
import { DeviceModal } from './DeviceModal';
import {
  User as UserIcon,
  Laptop,
  Smartphone,
  Tv,
  Gamepad2,
  Wifi,
  Pause,
  Play,
  Settings,
  Trash2,
  ArrowDown,
  ArrowUp,
  Check,
  X,
  Sliders,
  EyeOff,
  Eye
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

function getDeviceIcon(vendor, hostname) {
  const text = `${vendor || ''} ${hostname || ''}`.toLowerCase();
  if (text.includes('phone') || text.includes('iphone') || text.includes('pixel') || text.includes('galaxy')) {
    return <Smartphone size={14} />;
  }
  if (text.includes('tv') || text.includes('cast') || text.includes('samsung')) {
    return <Tv size={14} />;
  }
  if (text.includes('playstation') || text.includes('xbox') || text.includes('nintendo') || text.includes('game')) {
    return <Gamepad2 size={14} />;
  }
  return <Laptop size={14} />;
}

export function formatLimitSummary(limitStr) {
  if (!limitStr || limitStr === 'unlimited' || limitStr === '0/0') return '⚡ Unlimited';
  if (limitStr.includes('/')) {
    const [up, down] = limitStr.split('/');
    return `↓ ${down} / ↑ ${up}`;
  }
  return `↓↑ ${limitStr}`;
}

export function UserCard({ user, onEdit, onDelete, onLimitChange, onPauseToggle, onUpdate, showHidden = false }) {
  const { t } = useI18n();
  const [isUpdating, setIsUpdating] = useState(false);
  const [showCustomInput, setShowCustomInput] = useState(false);
  const [customDown, setCustomDown] = useState('');
  const [customUp, setCustomUp] = useState('');
  const [selectedDevice, setSelectedDevice] = useState(null);

  const visibleDevices = (user.devices || []).filter(d => showHidden || !d.is_hidden);
  const activeDevices = visibleDevices.filter(d => d.is_active);
  const isPaused = user.is_paused;
  const isOnline = activeDevices.length > 0 && !isPaused;

  const currentLimit = user.speed_limit || 'unlimited';
  const isKnownPreset = SPEED_PRESETS.some(p => p.value === currentLimit);

  const handleLimitSelect = async (e) => {
    const val = e.target.value;
    if (val === 'custom') {
      // Pre-fill custom values from current limit
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

    // Normalize units e.g. 50 -> 50M
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
      gap: 16,
      borderLeft: `4px solid ${isPaused ? 'var(--color-danger)' : (isOnline ? 'var(--color-primary)' : 'var(--border-color)')}`
    }}>
      {/* Card Header */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              background: isPaused ? 'var(--color-danger-bg)' : 'var(--bg-secondary)',
              color: isPaused ? 'var(--color-danger)' : 'var(--color-primary)',
              width: 38,
              height: 38,
              borderRadius: 'var(--radius-full)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontWeight: 700
            }}>
              <UserIcon size={20} />
            </div>
            <div>
              <div style={{ fontWeight: 700, fontSize: '1.05rem', color: 'var(--text-primary)' }}>
                {user.name}
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                {t('devices_count', { count: user.devices?.length || 0 })}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span className={`badge ${isPaused ? 'badge-danger' : (isOnline ? 'badge-success' : 'badge-neutral')}`}>
              {isPaused ? t('paused') : (isOnline ? t('active_now') : t('idle'))}
            </span>
            <button className="btn-icon" onClick={() => onEdit(user)} title={t('edit_user')} style={{ width: 30, height: 30 }}>
              <Settings size={14} />
            </button>
            <button className="btn-icon" onClick={() => onDelete(user.id)} title={t('delete_user')} style={{ width: 30, height: 30, color: 'var(--color-danger)' }}>
              <Trash2 size={14} />
            </button>
          </div>
        </div>

        {/* Real-time Bandwidth Gauges */}
        <div style={{
          background: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-md)',
          padding: '10px 14px',
          display: 'flex',
          justifyContent: 'space-around',
          alignItems: 'center',
          marginTop: 8
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <ArrowDown size={18} style={{ color: isOnline ? 'var(--color-success)' : 'var(--text-muted)' }} />
            <div>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 600 }}>{t('download_rx')}</div>
              <div className="font-mono" style={{ fontSize: '0.95rem', fontWeight: 800, color: isOnline ? 'var(--color-success)' : 'var(--text-muted)' }}>
                {formatSpeed(user.current_rate_in || 0)}
              </div>
            </div>
          </div>

          <div style={{ width: 1, height: 28, background: 'var(--border-color)' }}></div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <ArrowUp size={18} style={{ color: isOnline ? 'var(--color-primary)' : 'var(--text-muted)' }} />
            <div>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 600 }}>{t('upload_tx')}</div>
              <div className="font-mono" style={{ fontSize: '0.95rem', fontWeight: 800, color: isOnline ? 'var(--color-primary)' : 'var(--text-muted)' }}>
                {formatSpeed(user.current_rate_out || 0)}
              </div>
            </div>
          </div>
        </div>

        {/* Associated Devices List */}
        <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {visibleDevices.map(d => (
            <div
              key={d.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '6px 10px',
                background: 'var(--bg-secondary)',
                borderRadius: 'var(--radius-sm)',
                fontSize: '0.8125rem',
                border: '1px solid var(--border-color)',
                opacity: d.is_paused ? 0.65 : (d.is_hidden ? 0.75 : 1),
                gap: 8,
                transition: 'all 0.15s ease'
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 7,
                  minWidth: 0,
                  flex: '1 1 auto',
                  cursor: 'pointer'
                }}
                onClick={() => setSelectedDevice(d)}
                title="Click to edit device settings & limits"
              >
                <span
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: '50%',
                    background: d.is_paused ? 'var(--color-danger)' : (d.is_active ? 'var(--color-success)' : 'var(--text-muted)'),
                    boxShadow: d.is_active && !d.is_paused ? '0 0 6px rgba(16, 185, 129, 0.5)' : 'none',
                    flexShrink: 0
                  }}
                  title={d.is_paused ? 'Paused' : (d.is_active ? 'Online / Active' : 'Offline / Idle')}
                />
                <span style={{ flexShrink: 0, display: 'flex', alignItems: 'center' }}>
                  {getDeviceIcon(d.vendor, d.hostname)}
                </span>
                <span style={{
                  fontWeight: 600,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  color: 'var(--text-primary)'
                }}>
                  {d.custom_name || d.hostname || d.vendor || 'Device'}
                </span>
                {d.is_hidden && (
                  <span
                    className="badge"
                    style={{
                      fontSize: '0.6rem',
                      padding: '1px 5px',
                      background: 'rgba(100, 116, 139, 0.2)',
                      color: 'var(--text-muted)',
                      border: '1px solid rgba(100, 116, 139, 0.3)',
                      flexShrink: 0
                    }}
                    title="Hidden device"
                  >
                    {t('hidden_badge')}
                  </span>
                )}
                {d.speed_limit && d.speed_limit !== 'default' && (
                  <span
                    className="badge"
                    style={{
                      fontSize: '0.625rem',
                      padding: '1px 5px',
                      background: 'rgba(234, 179, 8, 0.15)',
                      color: 'var(--color-warning)',
                      border: '1px solid rgba(234, 179, 8, 0.3)',
                      flexShrink: 0
                    }}
                    title={`Device custom limit: ${d.speed_limit}`}
                  >
                    ⚡ {d.speed_limit}
                  </span>
                )}
                {d.is_randomized_mac && (
                  <span
                    className="badge"
                    style={{
                      fontSize: '0.6rem',
                      padding: '1px 5px',
                      background: 'rgba(234, 179, 8, 0.15)',
                      color: 'var(--color-warning)',
                      border: '1px solid rgba(234, 179, 8, 0.3)',
                      flexShrink: 0
                    }}
                    title="Private / Randomized MAC address"
                  >
                    Private
                  </span>
                )}
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                {d.last_wifi_signal && (
                  <span style={{ fontSize: '0.7rem', color: d.last_wifi_signal > -65 ? 'var(--color-success)' : 'var(--color-warning)', display: 'flex', alignItems: 'center', gap: 2 }}>
                    <Wifi size={11} /> {d.last_wifi_signal} dBm
                  </span>
                )}
                <span className="font-mono" style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                  {d.ip_address || d.mac_address}
                </span>

                {/* Device Pause/Resume Toggle */}
                <button
                  type="button"
                  onClick={async (e) => {
                    e.stopPropagation();
                    try {
                      await api.toggleDevicePause(d.id, !d.is_paused);
                      if (onUpdate) onUpdate();
                    } catch (err) {
                      console.error('Failed to toggle device pause:', err);
                    }
                  }}
                  style={{
                    width: 26,
                    height: 26,
                    borderRadius: 'var(--radius-sm)',
                    background: d.is_paused ? 'var(--color-danger)' : 'var(--bg-card)',
                    color: d.is_paused ? '#fff' : 'var(--text-secondary)',
                    border: '1px solid var(--border-color)'
                  }}
                  title={d.is_paused ? t('resume_device') : t('pause_device')}
                >
                  {d.is_paused ? <Play size={12} /> : <Pause size={12} />}
                </button>

                {/* Device Hide/Unhide Toggle */}
                <button
                  type="button"
                  onClick={async (e) => {
                    e.stopPropagation();
                    try {
                      await api.toggleHideDevice(d.id, !d.is_hidden);
                      if (onUpdate) onUpdate();
                    } catch (err) {
                      console.error('Failed to toggle device hide:', err);
                    }
                  }}
                  style={{
                    width: 26,
                    height: 26,
                    borderRadius: 'var(--radius-sm)',
                    background: d.is_hidden ? 'rgba(234, 179, 8, 0.15)' : 'var(--bg-card)',
                    color: d.is_hidden ? 'var(--color-warning, #f59e0b)' : 'var(--text-muted)',
                    border: '1px solid var(--border-color)'
                  }}
                  title={d.is_hidden ? t('unhide_device') : t('hide_device')}
                >
                  {d.is_hidden ? <EyeOff size={12} /> : <Eye size={12} />}
                </button>

                {/* Device Settings Edit Button */}
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedDevice(d);
                  }}
                  style={{
                    width: 26,
                    height: 26,
                    borderRadius: 'var(--radius-sm)',
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
          ))}
        </div>
      </div>

      {/* Card Footer: Speed Limiter & Pause Toggle */}
      <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: 12 }}>
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
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <select
                className="form-select"
                style={{ width: '100%', padding: '6px 8px', fontSize: '0.8rem' }}
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

      {/* Individual Device Settings & Limits Modal */}
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
