import React, { useState, useEffect, useMemo } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { formatBytes } from '../utils/formatters';
import {
  X,
  Calendar,
  Download,
  Upload,
  BarChart2,
  TrendingUp,
  User as UserIcon,
  Laptop,
  Smartphone,
  Server,
  Activity,
  ArrowRight,
  AlertCircle,
  Loader2,
  Maximize2
} from 'lucide-react';

const PRESETS = [
  { id: '1d', labelKey: 'range_1d', days: 1 },
  { id: '7d', labelKey: 'range_7d', days: 7 },
  { id: '30d', labelKey: 'range_30d', days: 30 },
  { id: '1y', labelKey: 'range_1y', days: 365 },
  { id: 'all_time', labelKey: 'range_all_time', days: 9999 },
  { id: 'custom', labelKey: 'range_custom' }
];

export function TrafficHistoryModal({ isOpen, target, onClose, onSelectTarget }) {
  const { t } = useI18n();

  const [preset, setPreset] = useState('7d');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState('');
  const [data, setData] = useState(null);
  const [hoveredPoint, setHoveredPoint] = useState(null);

  // Initialize dates when opened
  useEffect(() => {
    if (isOpen && target) {
      const today = new Date();
      const endStr = today.toISOString().split('T')[0];
      const startObj = new Date(today);
      startObj.setDate(startObj.getDate() - 6);
      const startStr = startObj.toISOString().split('T')[0];
      setEndDate(endStr);
      setStartDate(startStr);
      setPreset('7d');
      setHoveredPoint(null);
      setError('');
      setData(null);
      setIsLoading(true);
    }
  }, [isOpen, target]);

  // Load history data whenever target or range changes
  const loadHistory = async () => {
    if (!target || !isOpen) return;
    setIsLoading(true);
    setError('');
    try {
      let res;
      const opts = { preset };
      if (preset === 'custom' && startDate && endDate) {
        opts.startDate = startDate;
        opts.endDate = endDate;
      }
      if (target.type === 'user') {
        res = await api.getUserTrafficHistory(target.id, opts);
      } else {
        res = await api.getDeviceTrafficHistory(target.id, opts);
      }
      if (res?.data) {
        setData(res.data);
      }
    } catch (err) {
      console.error('Failed to load traffic history:', err);
      setError(err.message || 'Failed to load traffic history');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen && target) {
      loadHistory();
    }
  }, [isOpen, target, preset, preset === 'custom' ? `${startDate}:${endDate}` : null]);

  if (!isOpen || !target) return null;

  const timeline = data?.timeline || [];
  const maxDailyBytes = useMemo(() => {
    if (!timeline.length) return 1024 * 1024;
    return Math.max(...timeline.map(p => p.total_bytes), 1024 * 1024);
  }, [timeline]);

  const activePoint = hoveredPoint || (timeline.length > 0 ? timeline[timeline.length - 1] : null);

  const downloadPct = data?.total_bytes > 0
    ? Math.round((data.total_bytes_in / data.total_bytes) * 100)
    : 0;
  const uploadPct = data?.total_bytes > 0
    ? Math.round((data.total_bytes_out / data.total_bytes) * 100)
    : 0;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-card"
        onClick={e => e.stopPropagation()}
        style={{ maxWidth: 760, width: '94%', maxHeight: '90vh', display: 'flex', flexDirection: 'column' }}
      >
        {/* Header */}
        <div className="modal-header" style={{ paddingBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, flex: 1 }}>
            <div
              style={{
                width: 38,
                height: 38,
                borderRadius: 'var(--radius-sm)',
                background: target.type === 'user' ? 'rgba(59,130,246,0.15)' : 'rgba(16,185,129,0.15)',
                color: target.type === 'user' ? 'var(--color-primary)' : 'var(--color-success)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0
              }}
            >
              {target.type === 'user' ? <UserIcon size={20} /> : <Laptop size={20} />}
            </div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <h3 style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, margin: 0 }}>
                  {data?.entity_name || target.name || target.hostname || target.mac}
                </h3>
                <span className={`badge ${target.type === 'user' ? 'badge-primary' : 'badge-success'}`} style={{ fontSize: 'var(--fs-3xs)' }}>
                  {target.type === 'user' ? t('tab_users') : t('device')}
                </span>
              </div>
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }} className="font-mono">
                {target.type === 'user'
                  ? `${data?.devices?.length || 0} ${t('devs_short')}`
                  : `${data?.mac_address || target.mac || ''} ${data?.ip_address ? `• ${data.ip_address}` : ''} ${data?.user_name ? `• ${data.user_name}` : ''}`}
              </div>
            </div>
          </div>

          <button className="btn-icon" onClick={onClose} style={{ width: 32, height: 32 }}>
            <X size={18} />
          </button>
        </div>

        {/* Modal Body */}
        <div className="modal-body" style={{ overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 18, padding: '16px 20px' }}>
          {/* Preset Buttons & Custom Date Range */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
            <div style={{ display: 'flex', background: 'var(--bg-secondary)', padding: 3, borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-color)', gap: 2 }}>
              {PRESETS.map(p => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => setPreset(p.id)}
                  style={{
                    padding: '4px 12px',
                    fontSize: 'var(--fs-xs)',
                    fontWeight: preset === p.id ? 700 : 500,
                    borderRadius: 'var(--radius-xs)',
                    border: 'none',
                    background: preset === p.id ? 'var(--color-primary)' : 'transparent',
                    color: preset === p.id ? '#ffffff' : 'var(--text-secondary)',
                    cursor: 'pointer',
                    transition: 'all 0.15s ease'
                  }}
                >
                  {t(p.labelKey) || p.id.toUpperCase()}
                </button>
              ))}
            </div>

            {preset === 'custom' && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-xs)' }}>
                <input
                  type="date"
                  className="input input-sm font-mono"
                  style={{ width: 135 }}
                  value={startDate}
                  onChange={e => setStartDate(e.target.value)}
                />
                <span style={{ color: 'var(--text-muted)' }}>→</span>
                <input
                  type="date"
                  className="input input-sm font-mono"
                  style={{ width: 135 }}
                  value={endDate}
                  onChange={e => setEndDate(e.target.value)}
                />
              </div>
            )}
          </div>

          {/* Loading Indicator / Error Message */}
          {isLoading || (!data && !error) ? (
            <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
              <Loader2 size={28} className="spin" style={{ color: 'var(--color-primary)' }} />
              <div style={{ fontSize: 'var(--fs-sm)' }}>{t('loading_history')}...</div>
            </div>
          ) : error ? (
            <div className="card" style={{ padding: 20, textAlign: 'center', color: 'var(--color-danger)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
              <AlertCircle size={18} />
              <span>{error}</span>
            </div>
          ) : data ? (
            <>
              {/* Stat Tiles Row */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10 }}>
                {/* Total Traffic */}
                <div className="card panel-flush" style={{ padding: '10px 14px', background: 'var(--bg-secondary)' }}>
                  <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: 600 }}>
                    {t('total_traffic')}
                  </div>
                  <div style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, color: 'var(--text-primary)', marginTop: 2 }} className="font-mono">
                    {formatBytes(data.total_bytes)}
                  </div>
                  <div style={{ fontSize: 'var(--fs-3xs)', color: 'var(--text-muted)', marginTop: 2 }}>
                    {timeline.length} {t('days_count', { count: timeline.length })}
                  </div>
                </div>

                {/* Download */}
                <div className="card panel-flush" style={{ padding: '10px 14px', background: 'var(--bg-secondary)' }}>
                  <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--color-success)', textTransform: 'uppercase', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 4 }}>
                    <Download size={12} />
                    {t('download')}
                  </div>
                  <div style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, color: 'var(--color-success)', marginTop: 2 }} className="font-mono">
                    {formatBytes(data.total_bytes_in)}
                  </div>
                  <div style={{ fontSize: 'var(--fs-3xs)', color: 'var(--text-muted)', marginTop: 2 }}>
                    {downloadPct}% {t('of_total')}
                  </div>
                </div>

                {/* Upload */}
                <div className="card panel-flush" style={{ padding: '10px 14px', background: 'var(--bg-secondary)' }}>
                  <div style={{ fontSize: 'var(--fs-2xs)', color: '#3b82f6', textTransform: 'uppercase', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 4 }}>
                    <Upload size={12} />
                    {t('upload')}
                  </div>
                  <div style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, color: '#3b82f6', marginTop: 2 }} className="font-mono">
                    {formatBytes(data.total_bytes_out)}
                  </div>
                  <div style={{ fontSize: 'var(--fs-3xs)', color: 'var(--text-muted)', marginTop: 2 }}>
                    {uploadPct}% {t('of_total')}
                  </div>
                </div>

                {/* Daily Average */}
                <div className="card panel-flush" style={{ padding: '10px 14px', background: 'var(--bg-secondary)' }}>
                  <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: 600 }}>
                    {t('daily_average')}
                  </div>
                  <div style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, color: 'var(--text-primary)', marginTop: 2 }} className="font-mono">
                    {formatBytes(data.daily_average_bytes)}
                  </div>
                  <div style={{ fontSize: 'var(--fs-3xs)', color: 'var(--text-muted)', marginTop: 2 }}>
                    / {t('day_short')}
                  </div>
                </div>

                {/* Peak Day */}
                <div className="card panel-flush" style={{ padding: '10px 14px', background: 'var(--bg-secondary)' }}>
                  <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: 600 }}>
                    {t('peak_day')}
                  </div>
                  <div style={{ fontSize: 'var(--fs-md)', fontWeight: 700, color: 'var(--color-warning)', marginTop: 2 }} className="font-mono">
                    {data.peak_bytes > 0 ? formatBytes(data.peak_bytes) : '0 B'}
                  </div>
                  <div style={{ fontSize: 'var(--fs-3xs)', color: 'var(--text-muted)', marginTop: 2 }} className="font-mono">
                    {data.peak_date || '—'}
                  </div>
                </div>
              </div>

              {/* Interactive Timeline Chart */}
              <div
                className="card"
                style={{
                  padding: 16,
                  background: 'var(--bg-secondary)',
                  border: '1px solid var(--border-color)',
                  borderRadius: 'var(--radius-md)'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', fontWeight: 700 }}>
                    <BarChart2 size={16} style={{ color: 'var(--color-primary)' }} />
                    <span>{t('traffic_timeline')}</span>
                  </div>

                  {/* Legend & Tooltip readout */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 14, fontSize: 'var(--fs-xs)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                      <span style={{ width: 8, height: 8, borderRadius: 2, background: 'var(--color-success)' }} />
                      <span style={{ color: 'var(--text-muted)' }}>{t('download')}</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                      <span style={{ width: 8, height: 8, borderRadius: 2, background: '#3b82f6' }} />
                      <span style={{ color: 'var(--text-muted)' }}>{t('upload')}</span>
                    </div>
                  </div>
                </div>

                {/* Hover readout indicator */}
                {activePoint && (
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 12,
                      padding: '6px 12px',
                      background: 'var(--bg-card)',
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border-color)',
                      marginBottom: 10,
                      fontSize: 'var(--fs-xs)',
                      flexWrap: 'wrap'
                    }}
                  >
                    <div style={{ fontWeight: 700 }} className="font-mono">
                      📅 {activePoint.record_date}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <span style={{ color: 'var(--text-muted)' }}>{t('table_total')}:</span>
                      <strong className="font-mono">{formatBytes(activePoint.total_bytes)}</strong>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <span style={{ color: 'var(--color-success)' }}>↓ {formatBytes(activePoint.bytes_in)}</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <span style={{ color: '#3b82f6' }}>↑ {formatBytes(activePoint.bytes_out)}</span>
                    </div>
                  </div>
                )}

                {/* Stacked Bars Chart */}
                {timeline.length === 0 ? (
                  <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>
                    {t('no_traffic_recorded')}
                  </div>
                ) : (
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'flex-end',
                      gap: timeline.length > 60 ? 2 : (timeline.length > 20 ? 4 : 8),
                      height: 160,
                      paddingTop: 10,
                      borderBottom: '1px solid var(--border-color)',
                      overflowX: 'auto',
                      position: 'relative'
                    }}
                  >
                    {timeline.map((pt, idx) => {
                      const heightPct = Math.max((pt.total_bytes / maxDailyBytes) * 100, 3);
                      const rxPct = pt.total_bytes > 0 ? (pt.bytes_in / pt.total_bytes) * 100 : 50;
                      const isHovered = hoveredPoint?.record_date === pt.record_date;

                      return (
                        <div
                          key={pt.record_date}
                          onMouseEnter={() => setHoveredPoint(pt)}
                          onMouseLeave={() => setHoveredPoint(null)}
                          style={{
                            flex: 1,
                            minWidth: timeline.length > 90 ? 3 : (timeline.length > 30 ? 8 : 16),
                            maxWidth: 48,
                            height: '100%',
                            display: 'flex',
                            flexDirection: 'column',
                            justifyContent: 'flex-end',
                            alignItems: 'center',
                            cursor: 'pointer',
                            opacity: isHovered ? 1 : 0.85,
                            transition: 'opacity 0.1s ease, transform 0.1s ease',
                            transform: isHovered ? 'scaleY(1.03)' : 'none',
                            transformOrigin: 'bottom'
                          }}
                        >
                          <div
                            style={{
                              width: '100%',
                              height: `${heightPct}%`,
                              borderRadius: 'var(--radius-xs)',
                              overflow: 'hidden',
                              display: 'flex',
                              flexDirection: 'column',
                              background: 'var(--bg-input)',
                              boxShadow: isHovered ? '0 0 8px rgba(59,130,246,0.5)' : 'none'
                            }}
                          >
                            <div style={{ height: `${rxPct}%`, background: 'var(--color-success)' }} />
                            <div style={{ flex: 1, background: '#3b82f6' }} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* X-axis date range labels */}
                {timeline.length > 0 && (
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, fontSize: 'var(--fs-3xs)', color: 'var(--text-muted)' }} className="font-mono">
                    <span>{timeline[0]?.record_date}</span>
                    {timeline.length > 2 && <span>{timeline[Math.floor(timeline.length / 2)]?.record_date}</span>}
                    <span>{timeline[timeline.length - 1]?.record_date}</span>
                  </div>
                )}
              </div>

              {/* Per-Device Breakdown (when viewing User) */}
              {target.type === 'user' && data.devices && data.devices.length > 0 && (
                <div>
                  <h4 style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Laptop size={15} style={{ color: 'var(--color-primary)' }} />
                    <span>{t('device_breakdown')} ({data.devices.length})</span>
                  </h4>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {data.devices.map(dev => {
                      const sharePct = dev.percentage_of_total || 0;
                      return (
                        <div
                          key={dev.device_id}
                          style={{
                            padding: '10px 14px',
                            background: 'var(--bg-secondary)',
                            borderRadius: 'var(--radius-sm)',
                            border: '1px solid var(--border-color)',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            gap: 12
                          }}
                        >
                          <div style={{ minWidth: 0, flex: 1 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                              <span style={{ fontWeight: 700, fontSize: 'var(--fs-sm)' }}>
                                {dev.custom_name || dev.hostname || dev.mac_address}
                              </span>
                              <span className="badge badge-neutral" style={{ fontSize: 'var(--fs-3xs)' }}>
                                {sharePct}%
                              </span>
                            </div>
                            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginTop: 2 }} className="font-mono">
                              {dev.mac_address} {dev.ip_address ? `• ${dev.ip_address}` : ''} {dev.vendor ? `• ${dev.vendor}` : ''}
                            </div>

                            {/* Progress bar */}
                            <div
                              style={{
                                width: '100%',
                                height: 4,
                                background: 'var(--bg-input)',
                                borderRadius: 2,
                                marginTop: 6,
                                overflow: 'hidden'
                              }}
                            >
                              <div
                                style={{
                                  width: `${Math.min(100, Math.max(0, sharePct))}%`,
                                  height: '100%',
                                  background: 'var(--color-primary)',
                                  borderRadius: 2
                                }}
                              />
                            </div>
                          </div>

                          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
                            <div style={{ textAlign: 'right' }}>
                              <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)' }} className="font-mono">
                                {formatBytes(dev.total_bytes)}
                              </div>
                              <div style={{ fontSize: 'var(--fs-3xs)', color: 'var(--text-muted)' }}>
                                ↓ {formatBytes(dev.bytes_in)} • ↑ {formatBytes(dev.bytes_out)}
                              </div>
                            </div>

                            {onSelectTarget && (
                              <button
                                type="button"
                                className="btn btn-secondary btn-sm"
                                onClick={() => onSelectTarget({
                                  type: 'device',
                                  id: dev.device_id,
                                  name: dev.custom_name || dev.hostname || dev.mac_address,
                                  mac: dev.mac_address,
                                  ip: dev.ip_address
                                })}
                                title={t('view_device_history')}
                                style={{ fontSize: 'var(--fs-xs)', padding: '4px 8px' }}
                              >
                                <BarChart2 size={13} style={{ marginRight: 4 }} />
                                {t('view_graph')}
                              </button>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Daily Data Table (when viewing Device or User) */}
              {timeline.length > 0 && (
                <div>
                  <h4 style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Calendar size={15} style={{ color: 'var(--color-primary)' }} />
                    <span>{t('daily_breakdown')}</span>
                  </h4>

                  <div
                    className="card panel-flush"
                    style={{
                      maxHeight: 220,
                      overflowY: 'auto',
                      border: '1px solid var(--border-color)',
                      background: 'var(--bg-secondary)'
                    }}
                  >
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-xs)' }}>
                      <thead>
                        <tr style={{ borderBottom: '1px solid var(--border-color)', background: 'var(--bg-card)', position: 'sticky', top: 0, zIndex: 1 }}>
                          <th style={{ padding: '8px 12px', textAlign: 'left' }}>{t('date')}</th>
                          <th style={{ padding: '8px 12px', textAlign: 'right', color: 'var(--color-success)' }}>{t('download')}</th>
                          <th style={{ padding: '8px 12px', textAlign: 'right', color: '#3b82f6' }}>{t('upload')}</th>
                          <th style={{ padding: '8px 12px', textAlign: 'right' }}>{t('table_total')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[...timeline].reverse().map(pt => (
                          <tr
                            key={pt.record_date}
                            style={{
                              borderBottom: '1px solid var(--border-color)',
                              background: pt.total_bytes > 0 ? 'transparent' : 'rgba(0,0,0,0.03)'
                            }}
                          >
                            <td style={{ padding: '6px 12px', fontWeight: 600 }} className="font-mono">
                              {pt.record_date}
                            </td>
                            <td style={{ padding: '6px 12px', textAlign: 'right', color: 'var(--color-success)' }} className="font-mono">
                              {formatBytes(pt.bytes_in)}
                            </td>
                            <td style={{ padding: '6px 12px', textAlign: 'right', color: '#3b82f6' }} className="font-mono">
                              {formatBytes(pt.bytes_out)}
                            </td>
                            <td style={{ padding: '6px 12px', textAlign: 'right', fontWeight: 700 }} className="font-mono">
                              {formatBytes(pt.total_bytes)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          ) : null}
        </div>

        {/* Footer */}
        <div className="modal-footer" style={{ borderTop: '1px solid var(--border-color)', padding: '12px 20px' }}>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>
            {t('close')}
          </button>
        </div>
      </div>
    </div>
  );
}
