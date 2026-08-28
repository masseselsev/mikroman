import React, { useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { RefreshCw, UserPlus, Laptop, Smartphone, Wifi, Tag } from 'lucide-react';

export function DeviceInbox({ devices = [], users = [], onAssign, onScan, isScanning }) {
  const { t } = useI18n();
  const [selectedUserMap, setSelectedUserMap] = useState({});

  const handleUserSelect = (deviceId, userId) => {
    setSelectedUserMap(prev => ({ ...prev, [deviceId]: userId }));
  };

  const handleAssignClick = (deviceId) => {
    const userId = selectedUserMap[deviceId];
    if (userId) {
      onAssign(deviceId, parseInt(userId));
    }
  };

  return (
    <div>
      {/* Header bar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18, flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h2 style={{ fontSize: '1.25rem', fontWeight: 700 }}>{t('tab_devices')}</h2>
          <p style={{ fontSize: '0.825rem', color: 'var(--text-muted)' }}>
            {t('unassigned_count', { count: devices.length })}
          </p>
        </div>
        <button
          className="btn btn-secondary btn-sm"
          onClick={onScan}
          disabled={isScanning}
        >
          <RefreshCw size={14} className={isScanning ? "live-indicator" : ""} />
          {t('scan_now')}
        </button>
      </div>

      {devices.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--text-muted)' }}>
          <div style={{ fontSize: '1rem', fontWeight: 600, marginBottom: 6 }}>{t('no_unassigned')}</div>
          <p style={{ fontSize: '0.85rem' }}>All devices currently active on your network are mapped to user accounts.</p>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 16 }}>
          {devices.map(device => (
            <div key={device.id} className="card" style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between', gap: 14 }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <div style={{
                      background: 'var(--bg-secondary)',
                      color: 'var(--text-primary)',
                      padding: 8,
                      borderRadius: 'var(--radius-md)'
                    }}>
                      <Laptop size={18} />
                    </div>
                    <div>
                      <div style={{ fontWeight: 700, fontSize: '0.95rem' }}>
                        {device.custom_name || device.hostname || 'Unknown Device'}
                      </div>
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        {device.vendor || 'Generic Device'}
                      </div>
                    </div>
                  </div>

                  <span className={`badge ${device.is_active ? 'badge-success' : 'badge-neutral'}`}>
                    {device.is_active ? t('active_now') : t('idle')}
                  </span>
                </div>

                <div style={{
                  background: 'var(--bg-secondary)',
                  borderRadius: 'var(--radius-sm)',
                  padding: '8px 12px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4,
                  fontSize: '0.8rem'
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--text-muted)' }}>{t('ip_address')}:</span>
                    <span className="font-mono" style={{ fontWeight: 600 }}>{device.ip_address || 'N/A'}</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--text-muted)' }}>{t('mac_address')}:</span>
                    <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>{device.mac_address}</span>
                  </div>
                  {device.last_interface && (
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: 'var(--text-muted)' }}>Interface:</span>
                      <span className="font-mono">{device.last_interface}</span>
                    </div>
                  )}
                  {device.last_wifi_signal && (
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: 'var(--text-muted)' }}>{t('signal')}:</span>
                      <span className="font-mono" style={{ color: device.last_wifi_signal > -65 ? 'var(--color-success)' : 'var(--color-warning)' }}>
                        {device.last_wifi_signal} dBm
                      </span>
                    </div>
                  )}
                </div>
              </div>

              {/* Assignment footer */}
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', borderTop: '1px solid var(--border-color)', paddingTop: 10 }}>
                <select
                  className="form-select"
                  style={{ flex: 1, padding: '6px 8px', fontSize: '0.8rem' }}
                  value={selectedUserMap[device.id] || ''}
                  onChange={(e) => handleUserSelect(device.id, e.target.value)}
                >
                  <option value="">-- Choose User --</option>
                  {users.map(u => (
                    <option key={u.id} value={u.id}>{u.name}</option>
                  ))}
                </select>

                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => handleAssignClick(device.id)}
                  disabled={!selectedUserMap[device.id]}
                >
                  <UserPlus size={14} />
                  {t('assign_to_user')}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
