import React, { useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { formatSpeed, formatBytes } from '../utils/formatters';
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
  Gauge
} from 'lucide-react';

const SPEED_PRESETS = [
  { label: 'Unlimited (Max)', value: 'unlimited' },
  { label: '5M / 15M (Low)', value: '5M/15M' },
  { label: '10M / 30M (Normal)', value: '10M/30M' },
  { label: '25M / 50M (Fast)', value: '25M/50M' },
  { label: '50M / 100M (Super)', value: '50M/100M' },
  { label: '100M / 200M (Ultra)', value: '100M/200M' },
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

export function UserCard({ user, onEdit, onDelete, onLimitChange, onPauseToggle }) {
  const { t } = useI18n();
  const [isUpdating, setIsUpdating] = useState(false);

  const activeDevices = (user.devices || []).filter(d => d.is_active);
  const isPaused = user.is_paused;
  const isOnline = activeDevices.length > 0 && !isPaused;

  const handleLimitSelect = async (e) => {
    const val = e.target.value;
    setIsUpdating(true);
    try {
      await onLimitChange(user.id, val);
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
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 600 }}>Download</div>
              <div className="font-mono" style={{ fontSize: '0.95rem', fontWeight: 800, color: isOnline ? 'var(--color-success)' : 'var(--text-muted)' }}>
                {formatSpeed(user.current_rate_in || 0)}
              </div>
            </div>
          </div>

          <div style={{ width: 1, height: 28, background: 'var(--border-color)' }}></div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <ArrowUp size={18} style={{ color: isOnline ? 'var(--color-primary)' : 'var(--text-muted)' }} />
            <div>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 600 }}>Upload</div>
              <div className="font-mono" style={{ fontSize: '0.95rem', fontWeight: 800, color: isOnline ? 'var(--color-primary)' : 'var(--text-muted)' }}>
                {formatSpeed(user.current_rate_out || 0)}
              </div>
            </div>
          </div>
        </div>

        {/* Associated Devices List */}
        <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {(user.devices || []).map(d => (
            <div
              key={d.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                fontSize: '0.8rem',
                padding: '5px 8px',
                background: d.is_active ? 'var(--bg-input)' : 'transparent',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-color)',
                opacity: d.is_active ? 1 : 0.6
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                {getDeviceIcon(d.vendor, d.hostname)}
                <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {d.custom_name || d.hostname || d.vendor || 'Device'}
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {d.last_wifi_signal && (
                  <span style={{ fontSize: '0.7rem', color: d.last_wifi_signal > -65 ? 'var(--color-success)' : 'var(--color-warning)', display: 'flex', alignItems: 'center', gap: 2 }}>
                    <Wifi size={11} /> {d.last_wifi_signal} dBm
                  </span>
                )}
                <span className="font-mono" style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                  {d.ip_address || d.mac_address}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Card Footer: Speed Limiter & Pause Toggle */}
      <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: 12, display: 'flex', gap: 10, alignItems: 'center' }}>
        <div style={{ flex: 1 }}>
          <select
            className="form-select"
            style={{ width: '100%', padding: '6px 8px', fontSize: '0.8rem' }}
            value={user.speed_limit || 'unlimited'}
            onChange={handleLimitSelect}
            disabled={isUpdating}
          >
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
    </div>
  );
}
