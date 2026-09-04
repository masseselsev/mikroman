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
  EyeOff,
  Trash2,
  Activity
} from 'lucide-react';
import { RateLimitInputs } from './RateLimitInputs';

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

export function DeviceModal({ device, user, users = [], onClose, onUpdated, onViewConnections }) {
  const { t } = useI18n();
  const [userList, setUserList] = useState(users || []);
  const [selectedUserId, setSelectedUserId] = useState(device.user_id || (user?.id ? String(user.id) : ''));
  const [customName, setCustomName] = useState(device.custom_name || device.hostname || '');
  const [isPaused, setIsPaused] = useState(device.is_paused || false);
  const [isHidden, setIsHidden] = useState(device.is_hidden || false);
  const [priority, setPriority] = useState(device.priority ?? 1);
  // Manual rate only, same control as the user card. A device carries a
  // `<up>/<down>` override or nothing; empty both fields means "inherit the
  // owner's queue" (stored as `default`).
  const _hasOverride = device.speed_limit && device.speed_limit.includes('/');
  const [customDown, setCustomDown] = useState(_hasOverride ? device.speed_limit.split('/')[1] : '');
  const [customUp, setCustomUp] = useState(_hasOverride ? device.speed_limit.split('/')[0] : '');
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [error, setError] = useState(null);

  // The block is a firewall rule, so it must hit the router now - not wait for
  // Save. Matches the per-user card and the device-row pause button.
  const handlePauseToggle = async () => {
    const next = !isPaused;
    setPausing(true);
    setError(null);
    try {
      await api.toggleDevicePause(device.id, next);
      setIsPaused(next);
      if (onUpdated) onUpdated();
    } catch (err) {
      setError(err.message || 'Failed to change pause state');
    } finally {
      setPausing(false);
    }
  };

  React.useEffect(() => {
    if (!users || users.length === 0) {
      api.getUsers().then(res => {
        if (res?.data) setUserList(res.data);
      }).catch(err => console.debug('Failed to load users for device modal', err));
    } else {
      setUserList(users);
    }
  }, [users]);

  const handleDelete = async () => {
    if (!window.confirm(t('delete_device_confirm'))) return;
    setIsDeleting(true);
    setError(null);
    try {
      await api.deleteDevice(device.id);
      if (onUpdated) onUpdated();
      onClose();
    } catch (err) {
      setError(err.message || 'Failed to delete device');
    } finally {
      setIsDeleting(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsSaving(true);
    setError(null);

    // Empty both fields -> inherit the owner's queue. Otherwise a bare number
    // is megabits, and a missing side mirrors the other (RouterOS: up/down).
    let down = customDown.trim();
    let up = customUp.trim();
    let effectiveLimit;
    if (!down && !up) {
      effectiveLimit = 'default';
    } else {
      if (!down) down = up;
      if (!up) up = down;
      if (/^\d+$/.test(down)) down += 'M';
      if (/^\d+$/.test(up)) up += 'M';
      effectiveLimit = `${up}/${down}`;
    }

    try {
      await api.updateDevice(device.id, {
        custom_name: customName.trim(),
        speed_limit: effectiveLimit,
        is_paused: isPaused,
        is_hidden: isHidden,
        priority: Number(priority),
        user_id: selectedUserId ? Number(selectedUserId) : null,
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

            {/* Owner / Assigned User */}
            <div className="form-group">
              <label className="form-label">{t('table_user')}</label>
              <select
                className="form-select"
                value={selectedUserId}
                onChange={e => setSelectedUserId(e.target.value)}
              >
                <option value="">{t('unassigned_traffic')}</option>
                {userList.map(u => (
                  <option key={u.id} value={String(u.id)}>
                    {u.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Speed Limit - manual rate only, the same control the user card
                uses. Empty both fields to inherit the owner's queue. */}
            <div className="form-group">
              <label className="form-label" style={{ marginBottom: 8, fontWeight: 600 }}>{t('speed_limit')}</label>
              <RateLimitInputs
                down={customDown}
                up={customUp}
                onChangeDown={setCustomDown}
                onChangeUp={setCustomUp}
                downPlaceholder={t('rate_inherit_ph')}
                upPlaceholder={t('rate_inherit_ph')}
                hint={t('device_queue_hint', { user: user?.name || 'group' })}
              />
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
                onClick={handlePauseToggle}
                disabled={pausing}
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

          <div className="modal-footer" style={{ justifyContent: 'space-between' }}>
            {/* Removing a device takes it out of every live view but keeps its
                traffic history on the profile, pooled as "Old devices". */}
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={handleDelete}
              disabled={isSaving || isDeleting}
              title={t('delete_device_hint')}
              style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--color-danger)' }}
            >
              <Trash2 size={14} />
              {t('delete_device')}
            </button>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              {onViewConnections && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => {
                    onClose();
                    onViewConnections(device.id);
                  }}
                  style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                >
                  <Activity size={14} style={{ color: 'var(--color-primary)' }} />
                  <span>{t('live_connections_title')}</span>
                </button>
              )}
              <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>
                {t('cancel')}
              </button>
              <button
                type="submit"
                className="btn btn-primary btn-sm"
                disabled={isSaving || isDeleting}
                style={{ display: 'flex', alignItems: 'center', gap: 6 }}
              >
                {t('save')}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
