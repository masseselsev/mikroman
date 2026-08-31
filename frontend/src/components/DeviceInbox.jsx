import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { DeviceModal } from './DeviceModal';
import { formatBytes, formatRelativeTime } from '../utils/formatters';

import { RefreshCw, UserPlus, Laptop, Smartphone, Wifi, Tag, History, Link, X, Clock, ShieldAlert, Sliders, Pause, Play, EyeOff, Eye } from 'lucide-react';

/**
 * Earliest known sighting of a device: the 'discovered' history entry if one
 * survives, otherwise the oldest entry recorded for it.
 */
function firstSeenOf(device) {
  const history = device.history || [];
  if (history.length === 0) return null;
  const discovery = history.filter(h => h.event_type === 'discovered');
  const pool = discovery.length > 0 ? discovery : history;
  return pool.reduce(
    (oldest, h) => (!oldest || new Date(h.created_at) < new Date(oldest) ? h.created_at : oldest),
    null
  );
}


export function DeviceInbox({ devices = [], users = [], onAssign, onScan, isScanning }) {
  const { t, lang } = useI18n();
  const [selectedUserMap, setSelectedUserMap] = useState({});
  const [suggestions, setSuggestions] = useState([]);
  const [historyDevice, setHistoryDevice] = useState(null);
  const [editingDevice, setEditingDevice] = useState(null);
  const [deviceHistory, setDeviceHistory] = useState([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [mergingId, setMergingId] = useState(null);
  const [linkSuggestions, setLinkSuggestions] = useState([]);
  const [linkingId, setLinkingId] = useState(null);
  const [showHidden, setShowHidden] = useState(false);
  const [autoScanEnabled, setAutoScanEnabled] = useState(true);

  // Load auto-scan setting
  useEffect(() => {
    api.getSettings().then(res => {
      if (res.data?.auto_scan_enabled !== undefined) {
        setAutoScanEnabled(res.data.auto_scan_enabled !== 'false');
      }
    }).catch(() => {});
  }, []);

  const handleToggleAutoScan = async () => {
    const nextState = !autoScanEnabled;
    setAutoScanEnabled(nextState);
    try {
      await api.saveSettings({ auto_scan_enabled: nextState ? 'true' : 'false' });
    } catch (e) {
      console.debug('Failed to toggle auto scan setting', e);
    }
  };

  // Fetch smart suggestions when devices change
  useEffect(() => {
    loadSuggestions();
  }, [devices]);

  const loadSuggestions = async () => {
    try {
      const [merge, links] = await Promise.all([
        api.getMergeSuggestions().catch(() => ({ data: [] })),
        api.getLinkSuggestions().catch(() => ({ data: [] })),
      ]);
      setSuggestions(merge.data || []);
      setLinkSuggestions(links.data || []);
    } catch (e) {
      console.debug('Failed to load suggestions', e);
    }
  };

  const handleLinkClick = async (deviceId, primaryDeviceId) => {
    setLinkingId(deviceId);
    try {
      await api.linkDevice(deviceId, primaryDeviceId);
      if (onScan) onScan();
    } catch (err) {
      alert(`Link failed: ${err.message}`);
    } finally {
      setLinkingId(null);
    }
  };

  const handleUserSelect = (deviceId, userId) => {
    setSelectedUserMap(prev => ({ ...prev, [deviceId]: userId }));
  };

  const handleAssignClick = (deviceId) => {
    const userId = selectedUserMap[deviceId];
    if (userId) {
      onAssign(deviceId, parseInt(userId));
    }
  };

  const handleMergeClick = async (sourceDeviceId, targetDeviceId, targetName) => {
    if (!window.confirm(t('merge_confirm', { name: targetName }))) return;
    setMergingId(sourceDeviceId);
    try {
      await api.mergeDevice(sourceDeviceId, targetDeviceId);
      if (onScan) onScan();
    } catch (err) {
      alert(`Merge failed: ${err.message}`);
    } finally {
      setMergingId(null);
    }
  };

  const handleOpenHistory = async (device) => {
    setHistoryDevice(device);
    setLoadingHistory(true);
    try {
      const res = await api.getDeviceHistory(device.id);
      setDeviceHistory(res.data || []);
    } catch (e) {
      setDeviceHistory([]);
    } finally {
      setLoadingHistory(false);
    }
  };

  const visibleDevices = devices.filter(d => showHidden || !d.is_hidden);

  return (
    <div>
      {/* Header bar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18, flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h2 style={{ fontSize: 'var(--fs-xl)', fontWeight: 700 }}>{t('tab_devices')}</h2>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 4, flexWrap: 'wrap' }}>
            <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)' }}>
              {t('unassigned_count', { count: visibleDevices.length })}
            </p>

            {/* Interactive Auto-Scan Toggle Button */}
            <button
              type="button"
              onClick={handleToggleAutoScan}
              style={{
                background: autoScanEnabled ? 'rgba(16, 185, 129, 0.12)' : 'rgba(100, 116, 139, 0.15)',
                border: `1px solid ${autoScanEnabled ? 'rgba(16, 185, 129, 0.3)' : 'rgba(100, 116, 139, 0.3)'}`,
                borderRadius: 'var(--radius-xl)',
                padding: '2px 10px',
                fontSize: 'var(--fs-xs)',
                fontWeight: 600,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 5,
                color: autoScanEnabled ? 'var(--color-success)' : 'var(--text-muted)',
                transition: 'all 0.15s ease'
              }}
              title="Click to toggle automatic background discovery"
            >
              <span style={{
                width: 6,
                height: 6,
                borderRadius: 'var(--radius-full)',
                background: autoScanEnabled ? 'var(--color-success)' : 'var(--text-muted)',
                display: 'inline-block'
              }} />
              <span>{autoScanEnabled ? t('auto_scan_active') : t('auto_scan_paused')}</span>
            </button>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {/* Show Hidden Devices Checkbox */}
          <label className="toggle-pill">
            <input
              type="checkbox"
              checked={showHidden}
              onChange={e => setShowHidden(e.target.checked)}
            />
            <EyeOff size={13} style={{ color: showHidden ? 'var(--color-primary)' : 'var(--text-muted)' }} />
            {t('show_hidden_devices')}
          </label>

          <button
            className="btn btn-secondary btn-sm"
            onClick={onScan}
            disabled={isScanning}
          >
            <RefreshCw size={14} className={isScanning ? "live-indicator" : ""} />
            {t('scan_now')}
          </button>
        </div>
      </div>

      {visibleDevices.length === 0 ? (
        <div className="card empty-state">
          <div className="empty-state-title">{t('no_unassigned')}</div>
          <p style={{ fontSize: 'var(--fs-sm)' }}>{t('all_devices_assigned')}</p>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 16 }}>
          {visibleDevices.map(device => {
            const suggestion = suggestions.find(s => s.unassigned_device_id === device.id);
            const linkSuggestion = linkSuggestions.find(s => s.device_id === device.id);

            return (
              <div key={device.id} className="card" style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between', gap: 12 }}>
                <div>
                  {/* Smart Link Suggestion Banner */}
                  {suggestion && (
                    <div style={{
                      background: 'rgba(59, 130, 246, 0.12)',
                      border: '1px solid rgba(59, 130, 246, 0.35)',
                      borderRadius: 'var(--radius-sm)',
                      padding: '8px 10px',
                      marginBottom: 10,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 8
                    }}>
                      <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-primary)' }}>
                        <div style={{ fontWeight: 700, color: 'var(--color-primary)', display: 'flex', alignItems: 'center', gap: 4 }}>
                          <Link size={12} />
                          <span>{t('smart_link_title')}</span>
                        </div>
                        <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
                          {suggestion.reason}
                        </div>
                      </div>
                      <button
                        className="btn btn-primary btn-sm"
                        style={{ fontSize: 'var(--fs-xs)', padding: '4px 8px', whiteSpace: 'nowrap' }}
                        disabled={mergingId === device.id}
                        onClick={() => handleMergeClick(device.id, suggestion.suggested_target_device_id, suggestion.target_device_name)}
                      >
                        {mergingId === device.id ? 'Linking...' : t('merge_with', { name: suggestion.target_device_name })}
                      </button>
                    </div>
                  )}

                  {/* Adapter link proposal. Distinct from a merge: both MAC
                      addresses are real and stay, they are simply shown as one
                      machine with several network connections. */}
                  {linkSuggestion && (
                    <div style={{
                      background: 'rgba(16, 185, 129, 0.10)',
                      border: '1px solid rgba(16, 185, 129, 0.32)',
                      borderRadius: 'var(--radius-sm)',
                      padding: '8px 10px',
                      marginBottom: 10,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 8
                    }}>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 700, fontSize: 'var(--fs-xs)' }}>
                          <Link size={13} style={{ color: 'var(--color-success)' }} />
                          <span>{t('link_as_adapter')}</span>
                        </div>
                        <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
                          {linkSuggestion.reason}
                        </div>
                      </div>
                      <button
                        className="btn btn-secondary btn-sm"
                        style={{ fontSize: 'var(--fs-xs)', padding: '4px 8px', whiteSpace: 'nowrap' }}
                        disabled={linkingId === device.id}
                        onClick={() => handleLinkClick(device.id, linkSuggestion.primary_device_id)}
                      >
                        {linkingId === device.id ? '...' : t('link_suggestion', { name: linkSuggestion.primary_device_name })}
                      </button>
                    </div>
                  )}

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
                        <div style={{ fontWeight: 700, fontSize: 'var(--fs-md)', display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span>{device.custom_name || device.hostname || 'Unknown Device'}</span>
                          {device.is_hidden && (
                            <span
                              className="badge"
                              style={{
                                fontSize: 'var(--fs-3xs)',
                                padding: '0px 4px',
                                background: 'rgba(100, 116, 139, 0.2)',
                                color: 'var(--text-muted)',
                                border: '1px solid rgba(100, 116, 139, 0.3)'
                              }}
                            >
                              {t('hidden_badge')}
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
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
                    fontSize: 'var(--fs-sm)'
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: 'var(--text-muted)' }}>{t('ip_address')}:</span>
                      <span className="font-mono" style={{ fontWeight: 600 }}>{device.ip_address || 'N/A'}</span>
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span style={{ color: 'var(--text-muted)' }}>{t('mac_address')}:</span>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>{device.mac_address}</span>
                        {device.is_randomized_mac && (
                          <span
                            className="badge"
                            style={{
                              fontSize: 'var(--fs-3xs)',
                              padding: '1px 5px',
                              background: 'rgba(234, 179, 8, 0.15)',
                              color: 'var(--color-warning)',
                              border: '1px solid rgba(234, 179, 8, 0.3)',
                              fontWeight: 600
                            }}
                            title="Private / Randomized MAC (iOS Private Wi-Fi / Android MAC randomization)"
                          >
                            {t('private_mac')}
                          </span>
                        )}
                      </div>
                    </div>
                    {device.last_interface && (
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--text-muted)' }}>{t('interface')}:</span>
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

                    {/* When this device first appeared - a client seen minutes ago
                        deserves more scrutiny than one that has been around for weeks. */}
                    {firstSeenOf(device) && (
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                        <span style={{ color: 'var(--text-muted)' }}>{t('first_seen')}:</span>
                        <span className="font-mono">
                          {t('last_seen_ago', { time: formatRelativeTime(firstSeenOf(device), lang) })}
                        </span>
                      </div>
                    )}

                    {/* Volume consumed today by an as-yet unidentified device. */}
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: 'var(--text-muted)' }}>{t('today_label')}:</span>
                      <span className="font-mono" style={{ display: 'flex', gap: 8 }}>
                        <span style={{ color: 'var(--color-success)' }}>↓ {formatBytes(device.bytes_today_in || 0)}</span>
                        <span style={{ color: 'var(--color-primary)' }}>↑ {formatBytes(device.bytes_today_out || 0)}</span>
                      </span>
                    </div>
                  </div>
                </div>

                {/* Assignment footer */}
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', borderTop: '1px solid var(--border-color)', paddingTop: 10 }}>
                  <button
                    className="btn btn-ghost btn-sm btn-icon"
                    onClick={() => handleOpenHistory(device)}
                    title={t('view_history')}
                    style={{ width: 32, height: 32 }}
                  >
                    <History size={14} />
                  </button>

                  <button
                    className={`btn-icon ${device.is_paused ? 'btn-danger' : ''}`}
                    onClick={async () => {
                      try {
                        await api.toggleDevicePause(device.id, !device.is_paused);
                        if (onScan) onScan();
                      } catch (err) {
                        console.error('Failed to toggle device pause:', err);
                      }
                    }}
                    style={{ width: 32, height: 32, color: device.is_paused ? 'var(--color-danger)' : 'var(--text-muted)' }}
                    title={device.is_paused ? t('resume_device') : t('pause_device')}
                  >
                    {device.is_paused ? <Play size={13} /> : <Pause size={13} />}
                  </button>

                  {/* Hide / Unhide Quick Toggle */}
                  <button
                    className="btn btn-ghost btn-sm btn-icon"
                    onClick={async () => {
                      try {
                        await api.toggleHideDevice(device.id, !device.is_hidden);
                        if (onScan) onScan();
                      } catch (err) {
                        console.error('Failed to toggle device hide:', err);
                      }
                    }}
                    style={{ width: 32, height: 32, color: device.is_hidden ? 'var(--color-warning, #f59e0b)' : 'var(--text-muted)' }}
                    title={device.is_hidden ? t('unhide_device') : t('hide_device')}
                  >
                    {device.is_hidden ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>

                  <button
                    className="btn btn-ghost btn-sm btn-icon"
                    onClick={() => setEditingDevice(device)}
                    title={t('edit_device')}
                    style={{ width: 32, height: 32 }}
                  >
                    <Sliders size={14} />
                  </button>

                  <div style={{ flex: 1 }}>
                    <select
                      className="form-select"
                      style={{ width: '100%', padding: '6px 8px', fontSize: 'var(--fs-sm)' }}
                      value={selectedUserMap[device.id] || ''}
                      onChange={(e) => handleUserSelect(device.id, e.target.value)}
                    >
                      <option value="">{t('choose_user')}</option>
                      {users.map(u => (
                        <option key={u.id} value={u.id}>{u.name}</option>
                      ))}
                    </select>
                  </div>
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={() => handleAssignClick(device.id)}
                    disabled={!selectedUserMap[device.id]}
                    style={{ fontSize: 'var(--fs-sm)', whiteSpace: 'nowrap' }}
                  >
                    <UserPlus size={14} />
                    {t('assign_btn')}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Device Edit Modal */}
      {editingDevice && (
        <DeviceModal
          device={editingDevice}
          user={null}
          onClose={() => setEditingDevice(null)}
          onUpdated={onScan}
        />
      )}

      {/* Device History Modal */}
      {historyDevice && (
        <div className="modal-backdrop" onClick={() => setHistoryDevice(null)}>
          <div className="modal-content card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 480, width: '90%' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <History size={18} style={{ color: 'var(--color-primary)' }} />
                <h3 style={{ fontSize: 'var(--fs-lg)', fontWeight: 700 }}>
                  {t('device_history')}
                </h3>
              </div>
              <button className="btn-icon" onClick={() => setHistoryDevice(null)}>
                <X size={16} />
              </button>
            </div>

            <div style={{ marginBottom: 12, padding: '8px 10px', background: 'var(--bg-secondary)', borderRadius: 'var(--radius-sm)', fontSize: 'var(--fs-sm)' }}>
              <div style={{ fontWeight: 600 }}>{historyDevice.custom_name || historyDevice.hostname || 'Device'}</div>
              <div className="font-mono" style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-xs)' }}>{historyDevice.mac_address}</div>
            </div>

            {loadingHistory ? (
              <div style={{ textAlign: 'center', padding: '24px', color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>
                Loading change history...
              </div>
            ) : deviceHistory.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '20px', color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>
                {t('no_history')}
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxHeight: 320, overflowY: 'auto' }}>
                {deviceHistory.map(item => (
                  <div
                    key={item.id}
                    style={{
                      padding: '8px 10px',
                      background: 'var(--bg-secondary)',
                      borderRadius: 'var(--radius-sm)',
                      borderLeft: '3px solid var(--color-primary)',
                      fontSize: 'var(--fs-xs)'
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                      <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>
                        {t(`event_${item.event_type}`) || item.event_type}
                      </span>
                      <span style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-2xs)' }}>
                        {new Date(item.created_at).toLocaleString()}
                      </span>
                    </div>
                    {item.details && (
                      <div style={{ color: 'var(--text-secondary)', marginBottom: 2 }}>{item.details}</div>
                    )}
                    <div style={{ display: 'flex', gap: 12, color: 'var(--text-muted)', fontSize: 'var(--fs-2xs)' }} className="font-mono">
                      <span>MAC: {item.mac_address}</span>
                      {item.ip_address && <span>IP: {item.ip_address}</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
