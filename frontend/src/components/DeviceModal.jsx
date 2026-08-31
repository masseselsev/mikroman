import React, { useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import {
  Smartphone,
  Laptop,
  Tv,
  Globe,
  X,
  Pause,
  Play,
  EyeOff
} from 'lucide-react';
import { RateLimitInputs, LimitModeToggle } from './RateLimitInputs';

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
      <div className="modal-card" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-group">
            <div className="modal-icon">
              {getDeviceIcon(device.vendor, device.hostname)}
            </div>
            <div style={{ minWidth: 0 }}>
              <h3>{t('edit_device')}</h3>
              <div className="modal-subtitle truncate">
                {device.mac_address} • {device.ip_address || 'No IP'}
              </div>
            </div>
          </div>
          <button className="btn-icon" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {error && (
              <div className="alert alert-danger">{error}</div>
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
                <LimitModeToggle
                  isCustom={isCustomMode}
                  onPresets={() => {
                    setIsCustomMode(false);
                    setSpeedLimit('default');
                  }}
                  onCustom={() => {
                    setIsCustomMode(true);
                    if (!customDown && !customUp) {
                      setCustomDown('15M');
                      setCustomUp('5M');
                    }
                  }}
                />
              </div>

              {!isCustomMode ? (
                <select
                  className="form-select"
                  value={speedLimit}
                  onChange={e => handleSpeedSelect(e.target.value)}
                >
                  {SPEED_PRESETS.map(p => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
              ) : (
                <RateLimitInputs
                  down={customDown}
                  up={customUp}
                  onChangeDown={setCustomDown}
                  onChangeUp={setCustomUp}
                  downPlaceholder="e.g. 15M or 50M"
                  upPlaceholder="e.g. 5M or 20M"
                  hint={t('device_queue_hint', { user: user?.name || 'group' })}
                />
              )}
            </div>

            {/* Quick Pause & Status Toggle */}
            <div className={`setting-row${isPaused ? ' is-danger' : ''}`}>
              <div style={{ minWidth: 0 }}>
                <div className="setting-row-title">
                  {isPaused ? <Pause size={15} style={{ color: 'var(--color-danger)' }} /> : <Play size={15} style={{ color: 'var(--color-success)' }} />}
                  {isPaused ? t('device_paused') : t('device_active')}
                </div>
                <div className="setting-row-desc">
                  {isPaused ? t('device_paused_desc') : t('device_active_desc')}
                </div>
              </div>

              <button
                type="button"
                className={`btn btn-sm ${isPaused ? 'btn-secondary' : 'btn-danger'}`}
                onClick={() => setIsPaused(!isPaused)}
              >
                {isPaused ? t('resume_device') : t('pause_device')}
              </button>
            </div>

            {/* Hide Device Toggle */}
            <div className={`setting-row${isHidden ? ' is-warning' : ''}`}>
              <div style={{ minWidth: 0 }}>
                <div className="setting-row-title">
                  <EyeOff size={15} style={{ color: isHidden ? 'var(--color-warning)' : 'var(--text-muted)' }} />
                  {t('hide_device')}
                </div>
                <div className="setting-row-desc">
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
