import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { X, User as UserIcon } from 'lucide-react';
import { RateLimitInputs, LimitModeToggle } from './RateLimitInputs';

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

export function UserModal({ user, unassignedDevices = [], isOpen, onClose, onSave }) {
  const { t } = useI18n();
  const [name, setName] = useState('');
  const [speedLimit, setSpeedLimit] = useState('unlimited');
  const [isCustomMode, setIsCustomMode] = useState(false);
  const [customDown, setCustomDown] = useState('');
  const [customUp, setCustomUp] = useState('');
  const [nameError, setNameError] = useState('');
  const [selectedMacs, setSelectedMacs] = useState([]);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (user) {
      setName(user.name || '');
      setNameError('');
      const limit = user.speed_limit || 'unlimited';
      setSpeedLimit(limit);
      const isKnown = SPEED_PRESETS.some(p => p.value === limit);
      if (!isKnown && limit !== 'unlimited') {
        setIsCustomMode(true);
        if (limit.includes('/')) {
          const [up, down] = limit.split('/');
          setCustomUp(up);
          setCustomDown(down);
        } else {
          setCustomUp(limit);
          setCustomDown(limit);
        }
      } else {
        setIsCustomMode(false);
      }
      setSelectedMacs((user.devices || []).map(d => d.mac_address));
    } else {
      setName('');
      setNameError('');
      setSpeedLimit('unlimited');
      setIsCustomMode(false);
      setCustomDown('50M');
      setCustomUp('20M');
      setSelectedMacs([]);
    }
  }, [user, isOpen]);

  if (!isOpen) return null;

  const handleNameChange = (val) => {
    setName(val);
    if (val && !/^[a-zA-Z0-9_\-\. ]+$/.test(val)) {
      setNameError('Only English letters, numbers, spaces, hyphens, or underscores are allowed.');
    } else {
      setNameError('');
    }
  };

  const handleToggleMac = (mac) => {
    setSelectedMacs(prev =>
      prev.includes(mac) ? prev.filter(m => m !== mac) : [...prev, mac]
    );
  };

  const handleSpeedSelect = (val) => {
    if (val === 'custom') {
      setIsCustomMode(true);
      if (!customDown && !customUp) {
        setCustomDown('50M');
        setCustomUp('20M');
      }
    } else {
      setIsCustomMode(false);
      setSpeedLimit(val);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name.trim() || nameError) return;

    let finalLimit = speedLimit;
    if (isCustomMode) {
      let down = customDown.trim();
      let up = customUp.trim();
      if (!down && !up) {
        finalLimit = 'unlimited';
      } else {
        if (!down) down = up;
        if (!up) up = down;
        if (/^\d+$/.test(down)) down += 'M';
        if (/^\d+$/.test(up)) up += 'M';
        finalLimit = `${up}/${down}`; // RouterOS format: upload/download
      }
    }

    setIsSaving(true);
    try {
      await onSave({
        name: name.trim(),
        speed_limit: finalLimit,
        device_macs: selectedMacs
      });
      onClose();
    } finally {
      setIsSaving(false);
    }
  };

  // Combine already assigned devices for this user + unassigned devices
  const availableDevices = [
    ...(user ? (user.devices || []) : []),
    ...unassignedDevices
  ];

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div className="panel-title">
            <UserIcon size={18} />
            {user ? t('edit_user') : t('add_user')}
          </div>
          <button className="btn-icon" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <div className="form-group">
              <label className="form-label">{t('user_name')}</label>
              <input
                type="text"
                className="form-input"
                style={{ borderColor: nameError ? 'var(--color-danger)' : undefined }}
                value={name}
                onChange={e => handleNameChange(e.target.value)}
                placeholder="e.g. Alex, Kids, Smart Home"
                pattern="^[a-zA-Z0-9_\-\. ]+$"
                required
                autoFocus
              />
              {nameError && (
                <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--color-danger)', marginTop: 4 }}>
                  {nameError}
                </div>
              )}
            </div>

            <div className="form-group">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <label className="form-label" style={{ marginBottom: 0, fontWeight: 600 }}>{t('speed_limit')}</label>
                <LimitModeToggle
                  isCustom={isCustomMode}
                  onPresets={() => setIsCustomMode(false)}
                  onCustom={() => {
                    setIsCustomMode(true);
                    if (!customDown && !customUp) {
                      setCustomDown('50M');
                      setCustomUp('20M');
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
                  downPlaceholder="e.g. 50M or 100M"
                  upPlaceholder="e.g. 20M or 50M"
                  hint={t('rate_units_hint')}
                />
              )}
            </div>

            <div className="form-group">
              <label className="form-label">{t('assigned_devices')}</label>
              <div className="list-box" style={{ maxHeight: 180 }}>
                {availableDevices.length === 0 ? (
                  <div className="empty-note">{t('no_devices_detected')}</div>
                ) : (
                  availableDevices.map(dev => {
                    const isChecked = selectedMacs.includes(dev.mac_address);
                    return (
                      <label
                        key={dev.mac_address}
                        className={`list-row${isChecked ? ' is-selected' : ''}`}
                        style={{ justifyContent: 'flex-start' }}
                      >
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => handleToggleMac(dev.mac_address)}
                        />
                        <div className="truncate" style={{ flex: 1, minWidth: 0 }}>
                          <span style={{ fontWeight: 600 }}>{dev.custom_name || dev.hostname || dev.vendor || 'Device'}</span>
                          <span className="font-mono" style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginLeft: 8 }}>
                            {dev.ip_address || dev.mac_address}
                          </span>
                        </div>
                      </label>
                    );
                  })
                )}
              </div>
            </div>
          </div>

          <div className="modal-footer">
            <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>
              {t('cancel')}
            </button>
            <button type="submit" className="btn btn-primary btn-sm" disabled={isSaving || !name.trim()}>
              {t('save')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
