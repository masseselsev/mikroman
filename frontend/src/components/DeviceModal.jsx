import React, { useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import {
  Smartphone,
  Laptop,
  Tv,
  Globe,
  HelpCircle,
  X,
  ArrowDown,
  ArrowUp,
  Sliders,
  Pause,
  Play,
  Check,
  Shield,
  Zap,
  EyeOff
} from 'lucide-react';

const SPEED_PRESETS = [
  { label: 'Inherit User Limit (Default)', value: 'default' },
  { label: 'Unlimited (No Device Capping)', value: 'unlimited' },
  { label: '↓ 5 Mbps (Down) / ↑ 2 Mbps (Up) — Low', value: '2M/5M' },
  { label: '↓ 15 Mbps (Down) / ↑ 5 Mbps (Up) — Light', value: '5M/15M' },
  { label: '↓ 30 Mbps (Down) / ↑ 10 Mbps (Up) — Standard', value: '10M/30M' },
  { label: '↓ 50 Mbps (Down) / ↑ 20 Mbps (Up) — Media', value: '20M/50M' },
  { label: '↓ 100 Mbps (Down) / ↑ 30 Mbps (Up) — Gaming / 4K', value: '30M/100M' },
];

function getDeviceIcon(vendor = '', hostname = '') {
  const text = `${vendor} ${hostname}`.toLowerCase();
  if (/phone|iphone|pixel|galaxy|xiaomi|huawei|redmi|oppo|oneplus|poco|realme|vivo|mobile/i.test(text)) {
    return <Smartphone size={20} />;
  }
  if (/tv|smart-tv|bravia|webos|tizen|roku|appletv|firetv|chromecast/i.test(text)) {
    return <Tv size={20} />;
  }
  if (/macbook|laptop|pc|desktop|thinkpad|dell|lenovo|asus|intel|msi|workstation/i.test(text)) {
    return <Laptop size={20} />;
  }
  return <Globe size={20} />;
}

export function DeviceModal({ device, user, onClose, onUpdated }) {
  const { t } = useI18n();
  const [customName, setCustomName] = useState(device.custom_name || device.hostname || '');
  const [speedLimit, setSpeedLimit] = useState(device.speed_limit || 'default');
  const [isPaused, setIsPaused] = useState(device.is_paused || false);
  const [isHidden, setIsHidden] = useState(device.is_hidden || false);
  const [priority, setPriority] = useState(device.priority ?? 1);
  const [isCustomMode, setIsCustomMode] = useState(!SPEED_PRESETS.some(p => p.value === (device.speed_limit || 'default')));
  const [customDown, setCustomDown] = useState(device.speed_limit && device.speed_limit.includes('/') ? device.speed_limit.split('/')[1] : '15M');
  const [customUp, setCustomUp] = useState(device.speed_limit && device.speed_limit.includes('/') ? device.speed_limit.split('/')[0] : '5M');
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState(null);

  const handleSpeedSelect = (val) => {
    setSpeedLimit(val);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsSaving(true);
    setError(null);

    let effectiveLimit = speedLimit;
    if (isCustomMode) {
      const down = customDown.trim() || '15M';
      const up = customUp.trim() || '5M';
      effectiveLimit = `${up}/${down}`;
    }

    try {
      await api.updateDevice(device.id, {
        custom_name: customName.trim(),
        speed_limit: effectiveLimit,
        is_paused: isPaused,
        is_hidden: isHidden,
        priority: Number(priority)
      });
      if (onUpdated) onUpdated();
      onClose();
    } catch (err) {
      setError(err.message || 'Failed to update device');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 520 }}>
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              background: 'rgba(11, 114, 201, 0.15)',
              color: 'var(--color-primary)',
              width: 38,
              height: 38,
              borderRadius: 'var(--radius-md)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}>
              {getDeviceIcon(device.vendor, device.hostname)}
            </div>
            <div>
              <h3 style={{ fontSize: '1.05rem', fontWeight: 700 }}>{t('edit_device')}</h3>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                {device.mac_address} • {device.ip_address || 'No IP'}
              </div>
            </div>
          </div>
          <button className="btn-icon" onClick={onClose} style={{ width: 28, height: 28 }}>
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {error && (
              <div style={{ padding: '8px 12px', background: 'rgba(231, 76, 60, 0.15)', color: 'var(--color-danger)', borderRadius: 'var(--radius-sm)', fontSize: '0.8rem' }}>
                {error}
              </div>
            )}

            {/* Device Name */}
            <div className="form-group">
              <label className="form-label">{t('device_name')}</label>
              <input
                type="text"
                className="form-input"
                value={customName}
                onChange={e => setCustomName(e.target.value)}
                placeholder="e.g. My Phone, Living Room TV"
                required
              />
            </div>

            {/* Speed Limit */}
            <div className="form-group">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <label className="form-label" style={{ marginBottom: 0, fontWeight: 600 }}>{t('speed_limit')}</label>
                <div style={{
                  display: 'inline-flex',
                  background: 'var(--bg-input)',
                  padding: 2,
                  borderRadius: 6,
                  border: '1px solid var(--border-color)',
                  gap: 2
                }}>
                  <button
                    type="button"
                    onClick={() => {
                      setIsCustomMode(false);
                      setSpeedLimit('default');
                    }}
                    style={{
                      padding: '3px 10px',
                      fontSize: '0.725rem',
                      fontWeight: 600,
                      borderRadius: 4,
                      border: 'none',
                      background: !isCustomMode ? 'var(--color-primary)' : 'transparent',
                      color: !isCustomMode ? '#ffffff' : 'var(--text-muted)',
                      cursor: 'pointer',
                      transition: 'all 0.15s ease'
                    }}
                  >
                    ⚡ {t('presets')}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setIsCustomMode(true);
                      if (!customDown && !customUp) {
                        setCustomDown('15M');
                        setCustomUp('5M');
                      }
                    }}
                    style={{
                      padding: '3px 10px',
                      fontSize: '0.725rem',
                      fontWeight: 600,
                      borderRadius: 4,
                      border: 'none',
                      background: isCustomMode ? 'var(--color-primary)' : 'transparent',
                      color: isCustomMode ? '#ffffff' : 'var(--text-muted)',
                      cursor: 'pointer',
                      transition: 'all 0.15s ease'
                    }}
                  >
                    ✏️ {t('custom')}
                  </button>
                </div>
              </div>

              {!isCustomMode ? (
                <select
                  className="form-select"
                  value={speedLimit}
                  onChange={e => handleSpeedSelect(e.target.value)}
                  style={{ height: 38 }}
                >
                  {SPEED_PRESETS.map(p => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
              ) : (
                <div style={{
                  background: 'var(--bg-secondary)',
                  padding: '12px 14px',
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid var(--border-color)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 10
                }}>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                    <div>
                      <label style={{ fontSize: '0.725rem', color: 'var(--color-success)', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 4, marginBottom: 5 }}>
                        <ArrowDown size={13} /> {t('download_limit')}
                      </label>
                      <input
                        type="text"
                        className="form-input font-mono"
                        placeholder="e.g. 15M or 50M"
                        value={customDown}
                        onChange={e => setCustomDown(e.target.value)}
                        style={{ height: 34, fontSize: '0.85rem' }}
                      />
                    </div>
                    <div>
                      <label style={{ fontSize: '0.725rem', color: 'var(--color-primary)', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 4, marginBottom: 5 }}>
                        <ArrowUp size={13} /> {t('upload_limit')}
                      </label>
                      <input
                        type="text"
                        className="form-input font-mono"
                        placeholder="e.g. 5M or 20M"
                        value={customUp}
                        onChange={e => setCustomUp(e.target.value)}
                        style={{ height: 34, fontSize: '0.85rem' }}
                      />
                    </div>
                  </div>
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    💡 Rate limit is shaped as a child queue under user parent queue <code>mikroman-{user?.name || 'group'}</code>.
                  </div>
                </div>
              )}
            </div>

            {/* Quick Pause & Status Toggle */}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '10px 14px',
              borderRadius: 'var(--radius-md)',
              background: isPaused ? 'rgba(231, 76, 60, 0.12)' : 'var(--bg-secondary)',
              border: `1px solid ${isPaused ? 'var(--color-danger)' : 'var(--border-color)'}`
            }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: 6 }}>
                  {isPaused ? <Pause size={15} style={{ color: 'var(--color-danger)' }} /> : <Play size={15} style={{ color: 'var(--color-success)' }} />}
                  {isPaused ? t('device_paused') : t('device_active')}
                </div>
                <div style={{ fontSize: '0.725rem', color: 'var(--text-muted)' }}>
                  {isPaused ? 'Internet traffic for this device is completely blocked' : 'Internet access is enabled'}
                </div>
              </div>

              <button
                type="button"
                className={`btn btn-sm ${isPaused ? 'btn-success' : 'btn-danger'}`}
                onClick={() => setIsPaused(!isPaused)}
                style={{ fontSize: '0.75rem', padding: '4px 10px' }}
              >
                {isPaused ? t('resume_device') : t('pause_device')}
              </button>
            </div>

            {/* Hide Device Toggle */}
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '10px 14px',
              borderRadius: 'var(--radius-md)',
              background: isHidden ? 'rgba(255, 170, 0, 0.08)' : 'var(--bg-secondary)',
              border: `1px solid ${isHidden ? 'rgba(255, 170, 0, 0.35)' : 'var(--border-color)'}`
            }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: 6 }}>
                  <EyeOff size={15} style={{ color: isHidden ? 'var(--color-warning, #f59e0b)' : 'var(--text-muted)' }} />
                  {t('hide_device')}
                </div>
                <div style={{ fontSize: '0.725rem', color: 'var(--text-muted)' }}>
                  {t('hide_device_desc')}
                </div>
              </div>

              <input
                type="checkbox"
                id="device_modal_hide_checkbox"
                checked={isHidden}
                onChange={e => setIsHidden(e.target.checked)}
                style={{ width: 18, height: 18, cursor: 'pointer', accentColor: 'var(--color-primary)' }}
              />
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>
              {t('cancel')}
            </button>
            <button
              type="submit"
              className="btn btn-primary btn-sm"
              disabled={isSaving}
              style={{ display: 'flex', alignItems: 'center', gap: 6 }}
            >
              {t('save')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
