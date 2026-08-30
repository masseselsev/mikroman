import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { X, User as UserIcon, ArrowDown, ArrowUp, Sliders } from 'lucide-react';

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
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700 }}>
            <UserIcon size={18} style={{ color: 'var(--color-primary)' }} />
            {user ? t('edit_user') : t('add_user')}
          </div>
          <button className="btn-icon" onClick={onClose} style={{ width: 28, height: 28 }}>
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
                <div style={{ fontSize: '0.75rem', color: 'var(--color-danger)', marginTop: 4 }}>
                  {nameError}
                </div>
              )}
            </div>

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
                    onClick={() => setIsCustomMode(false)}
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
                    ⚡ Presets
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setIsCustomMode(true);
                      if (!customDown && !customUp) {
                        setCustomDown('50M');
                        setCustomUp('20M');
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
                    ✏️ Custom
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
                        placeholder="e.g. 50M or 100M"
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
                        placeholder="e.g. 20M or 50M"
                        value={customUp}
                        onChange={e => setCustomUp(e.target.value)}
                        style={{ height: 34, fontSize: '0.85rem' }}
                      />
                    </div>
                  </div>
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    💡 Enter rates with units (e.g. <code>50M</code> for 50 Mbps, <code>500k</code> for 500 Kbps)
                  </div>
                </div>
              )}
            </div>

            <div className="form-group">
              <label className="form-label">Assigned Devices</label>
              <div style={{
                maxHeight: 180,
                overflowY: 'auto',
                border: '1px solid var(--border-color)',
                borderRadius: 'var(--radius-md)',
                padding: 8,
                display: 'flex',
                flexDirection: 'column',
                gap: 6
              }}>
                {availableDevices.length === 0 ? (
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', textAlign: 'center', padding: 12 }}>
                    No devices detected on network yet.
                  </div>
                ) : (
                  availableDevices.map(dev => {
                    const isChecked = selectedMacs.includes(dev.mac_address);
                    return (
                      <label
                        key={dev.mac_address}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 10,
                          padding: '6px 8px',
                          borderRadius: 'var(--radius-sm)',
                          background: isChecked ? 'var(--bg-input)' : 'transparent',
                          cursor: 'pointer',
                          fontSize: '0.825rem'
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => handleToggleMac(dev.mac_address)}
                        />
                        <div style={{ flex: 1 }}>
                          <span style={{ fontWeight: 600 }}>{dev.custom_name || dev.hostname || dev.vendor || 'Device'}</span>
                          <span className="font-mono" style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginLeft: 8 }}>
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
