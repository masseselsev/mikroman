import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { formatSpeed, formatBytes, formatUptime } from '../utils/formatters';
import {
  Cpu,
  HardDrive,
  Thermometer,
  Zap,
  ArrowDown,
  ArrowUp,
  Clock,
  Sliders,
  X,
  Check,
  Network
} from 'lucide-react';

export function TelemetryBar({ router, activeRouter, interfaces = [] }) {
  const { t, lang } = useI18n();
  const [modalOpen, setModalOpen] = useState(false);
  const [availableIfaces, setAvailableIfaces] = useState([]);
  const [selectedIfaces, setSelectedIfaces] = useState([]);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Load configured interfaces
  useEffect(() => {
    if (activeRouter?.id) {
      api.getMonitoredInterfacesConfig(activeRouter.id)
        .then(res => {
          if (res?.data?.selected_interfaces) {
            setSelectedIfaces(res.data.selected_interfaces);
          }
        })
        .catch(() => {});
    }
  }, [activeRouter?.id]);

  const openConfigModal = async () => {
    setModalOpen(true);
    setSaveSuccess(false);
    try {
      const [ifacesRes, cfgRes] = await Promise.all([
        api.getAvailableInterfaces(activeRouter?.id).catch(() => ({ data: [] })),
        api.getMonitoredInterfacesConfig(activeRouter?.id).catch(() => ({ data: { selected_interfaces: [] } }))
      ]);
      const list = ifacesRes.data || interfaces || [];
      setAvailableIfaces(list);
      setSelectedIfaces(cfgRes.data?.selected_interfaces || []);
    } catch (err) {
      console.error('Failed to load interfaces config', err);
    }
  };

  const handleToggleIface = (name) => {
    setSelectedIfaces(prev =>
      prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name]
    );
  };

  const handleSelectAll = () => {
    setSelectedIfaces(availableIfaces.map(i => i.name));
  };

  const handleClearAll = () => {
    setSelectedIfaces([]);
  };

  const handleSelectWanOnly = () => {
    const wan = availableIfaces.filter(i => /ether1|wan|pppoe|sfp/i.test(i.name)).map(i => i.name);
    setSelectedIfaces(wan.length > 0 ? wan : (availableIfaces[0] ? [availableIfaces[0].name] : []));
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await api.saveMonitoredInterfacesConfig(activeRouter?.id, selectedIfaces);
      setSaveSuccess(true);
      setTimeout(() => {
        setModalOpen(false);
        setSaveSuccess(false);
      }, 500);
    } catch (err) {
      console.error('Failed to save monitored interfaces', err);
    } finally {
      setIsSaving(false);
    }
  };

  if (!router) {
    return null;
  }

  const cpuLoad = router.cpu_load || 0;
  const cpuColor = cpuLoad > 85 ? 'var(--color-danger)' : (cpuLoad > 60 ? 'var(--color-warning)' : 'var(--color-success)');
  const monitored = router.monitored_interfaces || selectedIfaces || [];
  const monitoredShort = monitored.length === 0
    ? 'none'
    : monitored.length === 1
      ? monitored[0]
      : `${monitored.length} ifaces`;

  return (
    <>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
        gap: 12,
        marginBottom: 24,
        alignItems: 'stretch'
      }}>
        {/* Gateway Download */}
        <div
          className="card"
          style={{
            padding: '12px 14px',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            minHeight: 74,
            cursor: 'pointer',
            transition: 'border-color 0.2s ease, transform 0.15s ease'
          }}
          onClick={openConfigModal}
          title="Click to configure monitored interfaces"
        >
          <div style={{
            background: 'rgba(46, 204, 113, 0.12)',
            color: 'var(--color-success)',
            width: 36,
            height: 36,
            borderRadius: 'var(--radius-md)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0
          }}>
            <ArrowDown size={18} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 4, marginBottom: 2 }}>
              <span style={{ fontSize: '0.725rem', color: 'var(--text-muted)', fontWeight: 600 }}>{t('total_rx')}</span>
              <span
                style={{
                  fontSize: '0.65rem',
                  padding: '1px 5px',
                  borderRadius: 4,
                  background: 'var(--bg-secondary)',
                  color: 'var(--text-muted)',
                  border: '1px solid var(--border-color)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 3,
                  maxWidth: 70,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap'
                }}
              >
                <Sliders size={9} />
                {monitoredShort}
              </span>
            </div>
            <div className="font-mono" style={{ fontSize: '1.1rem', fontWeight: 800, color: 'var(--color-success)', lineHeight: 1.2 }}>
              {formatSpeed(router.wan_rx_bps)}
            </div>
          </div>
        </div>

        {/* Gateway Upload */}
        <div
          className="card"
          style={{
            padding: '12px 14px',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            minHeight: 74,
            cursor: 'pointer',
            transition: 'border-color 0.2s ease, transform 0.15s ease'
          }}
          onClick={openConfigModal}
          title="Click to configure monitored interfaces"
        >
          <div style={{
            background: 'rgba(11, 114, 201, 0.12)',
            color: 'var(--color-primary)',
            width: 36,
            height: 36,
            borderRadius: 'var(--radius-md)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0
          }}>
            <ArrowUp size={18} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 4, marginBottom: 2 }}>
              <span style={{ fontSize: '0.725rem', color: 'var(--text-muted)', fontWeight: 600 }}>{t('total_tx')}</span>
              <span
                style={{
                  fontSize: '0.65rem',
                  padding: '1px 5px',
                  borderRadius: 4,
                  background: 'var(--bg-secondary)',
                  color: 'var(--text-muted)',
                  border: '1px solid var(--border-color)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 3,
                  maxWidth: 70,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap'
                }}
              >
                <Sliders size={9} />
                {monitoredShort}
              </span>
            </div>
            <div className="font-mono" style={{ fontSize: '1.1rem', fontWeight: 800, color: 'var(--color-primary)', lineHeight: 1.2 }}>
              {formatSpeed(router.wan_tx_bps)}
            </div>
          </div>
        </div>

        {/* CPU Load */}
        <div className="card" style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 12, minHeight: 74 }}>
          <div style={{
            background: 'var(--bg-secondary)',
            color: cpuColor,
            width: 36,
            height: 36,
            borderRadius: 'var(--radius-md)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0
          }}>
            <Cpu size={18} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
              <span style={{ fontSize: '0.725rem', color: 'var(--text-muted)', fontWeight: 600 }}>{t('cpu')}</span>
              <span className="font-mono" style={{ fontSize: '0.825rem', fontWeight: 700, color: cpuColor }}>{cpuLoad}%</span>
            </div>
            <div style={{
              height: 5,
              background: 'var(--bg-input)',
              borderRadius: 3,
              overflow: 'hidden'
            }}>
              <div style={{
                width: `${Math.min(cpuLoad, 100)}%`,
                height: '100%',
                background: cpuColor,
                transition: 'width 0.4s ease'
              }}></div>
            </div>
          </div>
        </div>

        {/* Memory */}
        <div className="card" style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 12, minHeight: 74 }}>
          <div style={{
            background: 'var(--bg-secondary)',
            color: 'var(--color-info)',
            width: 36,
            height: 36,
            borderRadius: 'var(--radius-md)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0
          }}>
            <HardDrive size={18} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: '0.725rem', color: 'var(--text-muted)', fontWeight: 600, marginBottom: 2 }}>{t('ram_free')}</div>
            <div className="font-mono" style={{ fontSize: '1.05rem', fontWeight: 700, lineHeight: 1.2 }}>
              {router.free_memory_mb || 0} <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 500 }}>MB</span>
            </div>
          </div>
        </div>

        {/* Temp */}
        {router.temperature !== null && (
          <div className="card" style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 12, minHeight: 74 }}>
            <div style={{
              background: 'var(--bg-secondary)',
              color: (router.temperature > 65 ? 'var(--color-danger)' : 'var(--color-warning)'),
              width: 36,
              height: 36,
              borderRadius: 'var(--radius-md)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0
            }}>
              <Thermometer size={18} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: '0.725rem', color: 'var(--text-muted)', fontWeight: 600, marginBottom: 2 }}>{t('temp')}</div>
              <div className="font-mono" style={{ fontSize: '1.05rem', fontWeight: 700, lineHeight: 1.2 }}>
                {router.temperature}°C
              </div>
            </div>
          </div>
        )}

        {/* Uptime */}
        {router.uptime && (
          <div className="card" style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 12, minHeight: 74 }}>
            <div style={{
              background: 'var(--bg-secondary)',
              color: 'var(--text-secondary)',
              width: 36,
              height: 36,
              borderRadius: 'var(--radius-md)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0
            }}>
              <Clock size={18} />
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: '0.725rem', color: 'var(--text-muted)', fontWeight: 600, marginBottom: 2 }}>{t('uptime')}</div>
              <div className="font-mono" style={{ fontSize: '1.05rem', fontWeight: 700, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', lineHeight: 1.2 }}>
                {formatUptime(router.uptime, lang)}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Interface Configuration Modal */}
      {modalOpen && (
        <div className="modal-backdrop" onClick={() => setModalOpen(false)}>
          <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 500 }}>
            <div className="modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700 }}>
                <Network size={18} style={{ color: 'var(--color-primary)' }} />
                {t('gateway_ifaces_title')}
              </div>
              <button className="btn-icon" onClick={() => setModalOpen(false)} style={{ width: 28, height: 28 }}>
                <X size={16} />
              </button>
            </div>

            <div className="modal-body">
              <p style={{ fontSize: '0.825rem', color: 'var(--text-muted)', marginBottom: 14, lineHeight: 1.4 }}>
                {t('gateway_ifaces_desc')}
              </p>

              {/* Quick Preset Buttons */}
              <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  style={{ fontSize: '0.75rem', padding: '3px 10px' }}
                  onClick={handleSelectWanOnly}
                >
                  {t('wan_only')}
                </button>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  style={{ fontSize: '0.75rem', padding: '3px 10px' }}
                  onClick={handleSelectAll}
                >
                  {t('select_all')}
                </button>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  style={{ fontSize: '0.75rem', padding: '3px 10px' }}
                  onClick={handleClearAll}
                >
                  {t('clear_all')}
                </button>
              </div>

              {/* Interface Checkboxes List */}
              <div style={{
                maxHeight: 240,
                overflowY: 'auto',
                border: '1px solid var(--border-color)',
                borderRadius: 'var(--radius-md)',
                padding: 6,
                display: 'flex',
                flexDirection: 'column',
                gap: 4
              }}>
                {availableIfaces.length === 0 ? (
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', textAlign: 'center', padding: 16 }}>
                    Loading router interfaces...
                  </div>
                ) : (
                  availableIfaces.map(iface => {
                    const isChecked = selectedIfaces.includes(iface.name);
                    return (
                      <div
                        key={iface.name}
                        onClick={() => handleToggleIface(iface.name)}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          padding: '8px 12px',
                          borderRadius: 'var(--radius-sm)',
                          background: isChecked ? 'rgba(11, 114, 201, 0.12)' : 'transparent',
                          border: `1px solid ${isChecked ? 'var(--color-primary)' : 'transparent'}`,
                          cursor: 'pointer',
                          fontSize: '0.85rem',
                          userSelect: 'none',
                          transition: 'all 0.15s ease'
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                          <input
                            type="checkbox"
                            checked={isChecked}
                            readOnly
                            style={{ cursor: 'pointer', pointerEvents: 'none' }}
                          />
                          <span style={{
                            width: 8,
                            height: 8,
                            borderRadius: '50%',
                            background: iface.running ? 'var(--color-success)' : 'var(--text-muted)',
                            flexShrink: 0
                          }} />
                          <span style={{ fontWeight: isChecked ? 700 : 500 }}>{iface.name}</span>
                        </div>
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                          {iface.type || 'interface'}
                        </span>
                      </div>
                    );
                  })
                )}
              </div>
            </div>

            <div className="modal-footer">
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setModalOpen(false)}>
                {t('cancel')}
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleSave}
                disabled={isSaving}
                style={{ display: 'flex', alignItems: 'center', gap: 6 }}
              >
                {saveSuccess ? <Check size={14} /> : null}
                {saveSuccess ? t('saved_ifaces_success') : t('save')}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
