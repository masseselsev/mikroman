import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { X, User as UserIcon } from 'lucide-react';

export function UserModal({ user, unassignedDevices = [], isOpen, onClose, onSave }) {
  const { t } = useI18n();
  const [name, setName] = useState('');
  const [speedLimit, setSpeedLimit] = useState('unlimited');
  const [selectedMacs, setSelectedMacs] = useState([]);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (user) {
      setName(user.name || '');
      setSpeedLimit(user.speed_limit || 'unlimited');
      setSelectedMacs((user.devices || []).map(d => d.mac_address));
    } else {
      setName('');
      setSpeedLimit('unlimited');
      setSelectedMacs([]);
    }
  }, [user, isOpen]);

  if (!isOpen) return null;

  const handleToggleMac = (mac) => {
    setSelectedMacs(prev =>
      prev.includes(mac) ? prev.filter(m => m !== mac) : [...prev, mac]
    );
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setIsSaving(true);
    try {
      await onSave({
        name: name.trim(),
        speed_limit: speedLimit,
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
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="e.g. Alex, Kids, Smart Home"
                required
                autoFocus
              />
            </div>

            <div className="form-group">
              <label className="form-label">{t('speed_limit')}</label>
              <select
                className="form-select"
                value={speedLimit}
                onChange={e => setSpeedLimit(e.target.value)}
              >
                <option value="unlimited">Unlimited (Max)</option>
                <option value="5M/15M">5 Mbps Up / 15 Mbps Down (Low)</option>
                <option value="10M/30M">10 Mbps Up / 30 Mbps Down (Normal)</option>
                <option value="25M/50M">25 Mbps Up / 50 Mbps Down (Fast)</option>
                <option value="50M/100M">50 Mbps Up / 100 Mbps Down (Super)</option>
                <option value="100M/200M">100 Mbps Up / 200 Mbps Down (Ultra)</option>
              </select>
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
