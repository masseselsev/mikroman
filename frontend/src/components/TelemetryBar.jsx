import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { formatSpeed, formatUptime } from '../utils/formatters';
import {
  Cpu,
  HardDrive,
  Thermometer,
  ArrowDown,
  ArrowUp,
  Clock,
  Sliders,
  X,
  Check,
  Network,
  Users,
  Globe
} from 'lucide-react';

/** How many telemetry ticks each sparkline remembers (~1 tick/second). */
const HISTORY_LENGTH = 60;

function pushHistory(previous, value) {
  if (value == null || Number.isNaN(value)) return previous;
  const next = [...previous, value];
  return next.length > HISTORY_LENGTH ? next.slice(next.length - HISTORY_LENGTH) : next;
}

/**
 * Minimal inline sparkline. Drawn as an SVG polyline from values already
 * arriving over the telemetry socket, so it costs no extra request and needs
 * no charting dependency.
 */
function Sparkline({ values, color, max }) {
  if (!values || values.length < 2) {
    return <div style={{ height: 18 }} />;
  }
  const width = 100;
  const height = 18;
  const peak = max != null ? max : Math.max(...values, 1);
  const scale = peak > 0 ? peak : 1;
  const step = width / (values.length - 1);

  const points = values
    .map((v, i) => `${(i * step).toFixed(1)},${(height - Math.min(v / scale, 1) * height).toFixed(1)}`)
    .join(' ');
  const area = `0,${height} ${points} ${width},${height}`;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"
         style={{ width: '100%', height: 18, display: 'block' }} aria-hidden="true">
      <polygon points={area} fill={color} opacity="0.14" />
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5"
                vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * One compact telemetry tile: label, primary value, a secondary detail, and an
 * optional sparkline. Replaces eight near-identical blocks of inline markup.
 *
 * `sub` accepts either a single line or an array of them, so a tile can carry
 * several related facts (the WAN tile shows the interface address, the public
 * address and the operator) without each caller inventing its own layout.
 * Falsy entries are dropped, which keeps the call sites free of conditionals.
 */
function Tile({ icon, tone, label, value, sub, history, historyMax, onClick, title, valueSize = 'var(--fs-lg)' }) {
  const subLines = (Array.isArray(sub) ? sub : [sub]).filter(Boolean);

  return (
    <div
      className={`tile${onClick ? ' is-clickable' : ''}`}
      onClick={onClick}
      title={title}
    >
      <div className="tile-head">
        <span className="tile-icon" style={{ color: tone }}>{icon}</span>
        <span className="section-label truncate">{label}</span>
      </div>

      <div className="tile-value truncate" style={{ fontSize: valueSize, color: tone }}>
        {value}
      </div>

      {history ? (
        <Sparkline values={history} color={tone} max={historyMax} />
      ) : null}

      {subLines.map((line, i) => (
        <div key={i} className="tile-sub truncate">{line}</div>
      ))}
    </div>
  );
}

export function TelemetryBar({ router, activeRouter, interfaces = [] }) {
  const { t, lang } = useI18n();
  const [modalOpen, setModalOpen] = useState(false);
  const [availableIfaces, setAvailableIfaces] = useState([]);
  const [selectedIfaces, setSelectedIfaces] = useState([]);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Rolling history for the sparklines, fed from the telemetry socket itself.
  const [rxHistory, setRxHistory] = useState([]);
  const [txHistory, setTxHistory] = useState([]);
  const [cpuHistory, setCpuHistory] = useState([]);
  const [memHistory, setMemHistory] = useState([]);
  const [tempHistory, setTempHistory] = useState([]);
  const [tempThreshold, setTempThreshold] = useState(null);

  useEffect(() => {
    if (!router) return;
    setRxHistory(prev => pushHistory(prev, router.wan_rx_bps));
    setTxHistory(prev => pushHistory(prev, router.wan_tx_bps));
    setCpuHistory(prev => pushHistory(prev, router.cpu_load));
    if (router.total_memory_mb) {
      const usedPct = ((router.total_memory_mb - router.free_memory_mb) / router.total_memory_mb) * 100;
      setMemHistory(prev => pushHistory(prev, usedPct));
    }
    if (router.temperature != null) {
      setTempHistory(prev => pushHistory(prev, router.temperature));
    }
  }, [router]);

  // Warning threshold is configured in Settings; used to colour the temperature.
  useEffect(() => {
    api.getSettings()
      .then(res => {
        const raw = res?.data?.temp_warning_threshold;
        if (raw) setTempThreshold(Number(raw));
      })
      .catch(() => {});
  }, []);

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

  const memPct = router.total_memory_mb
    ? Math.round(((router.total_memory_mb - router.free_memory_mb) / router.total_memory_mb) * 100)
    : null;

  // Temperature is judged against the user's configured warning threshold
  // rather than a hard-coded number, so it matches the alerting behaviour.
  const temp = router.temperature;
  const warnAt = tempThreshold || 80;
  const tempColor = temp == null
    ? 'var(--text-muted)'
    : (temp >= warnAt ? 'var(--color-danger)'
      : (temp >= warnAt - 8 ? 'var(--color-warning)' : 'var(--color-success)'));
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
        gridTemplateColumns: 'repeat(auto-fit, minmax(148px, 1fr))',
        gap: 10,
        marginBottom: 18,
        alignItems: 'stretch'
      }}>
        <Tile
          icon={<ArrowDown size={15} />}
          tone="var(--color-success)"
          label={t('total_rx')}
          value={formatSpeed(router.wan_rx_bps)}
          sub={monitoredShort}
          history={rxHistory}
          onClick={openConfigModal}
          title={t('configure_interfaces_hint')}
        />

        <Tile
          icon={<ArrowUp size={15} />}
          tone="var(--color-primary)"
          label={t('total_tx')}
          value={formatSpeed(router.wan_tx_bps)}
          sub={monitoredShort}
          history={txHistory}
          onClick={openConfigModal}
          title={t('configure_interfaces_hint')}
        />

        <Tile
          icon={<Cpu size={15} />}
          tone={cpuColor}
          label={t('cpu')}
          value={`${cpuLoad}%`}
          sub={router.board_name || ''}
          history={cpuHistory}
          historyMax={100}
        />

        <Tile
          icon={<HardDrive size={15} />}
          tone="var(--color-primary)"
          label={t('ram_free')}
          value={`${Math.round(router.free_memory_mb || 0)} MB`}
          sub={memPct !== null ? `${memPct}% ${t('used_label')}` : ''}
          history={memHistory}
          historyMax={100}
        />

        <Tile
          icon={<Thermometer size={15} />}
          tone={tempColor}
          label={t('temp')}
          value={router.temperature != null ? `${router.temperature}°C` : '—'}
          sub={tempThreshold ? `${t('threshold_label')} ${tempThreshold}°C` : ''}
          history={tempHistory}
        />

        <Tile
          icon={<Users size={15} />}
          tone="var(--color-primary)"
          label={t('clients_label')}
          value={String(router.active_clients ?? 0)}
          sub={t('online')}
        />

        <Tile
          icon={<Globe size={15} />}
          tone="var(--text-secondary)"
          label={t('wan_ip')}
          value={router.wan_ip || '—'}
          valueSize="var(--fs-sm)"
          title={router.isp
            ? `${t('isp_label')}: ${router.isp}${router.asn ? ` (${router.asn})` : ''}`
            : undefined}
          // Three facts about the same link, most specific first: the address on
          // the interface, the address the internet actually sees (they differ
          // under carrier-grade NAT), and who the link belongs to.
          sub={[
            router.public_ip && router.public_ip !== router.wan_ip ? `↗ ${router.public_ip}` : null,
            router.isp || null,
            !router.public_ip && !router.isp ? (router.version || '') : null
          ]}
        />

        <Tile
          icon={<Clock size={15} />}
          tone="var(--text-secondary)"
          label={t('uptime')}
          value={formatUptime(router.uptime, lang)}
          valueSize="var(--fs-md)"
          sub={router.board_name || ''}
        />
      </div>

      {/* Interface Configuration Modal */}
      {modalOpen && (
        <div className="modal-backdrop" onClick={() => setModalOpen(false)}>
          <div className="modal-card modal-sm" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <div className="panel-title">
                <Network size={18} />
                {t('gateway_ifaces_title')}
              </div>
              <button className="btn-icon" onClick={() => setModalOpen(false)} style={{ width: 28, height: 28 }}>
                <X size={16} />
              </button>
            </div>

            <div className="modal-body">
              <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', marginBottom: 14, lineHeight: 1.4 }}>
                {t('gateway_ifaces_desc')}
              </p>

              {/* Quick Preset Buttons */}
              <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                <button type="button" className="btn btn-secondary btn-sm" onClick={handleSelectWanOnly}>
                  {t('wan_only')}
                </button>
                <button type="button" className="btn btn-secondary btn-sm" onClick={handleSelectAll}>
                  {t('select_all')}
                </button>
                <button type="button" className="btn btn-ghost btn-sm" onClick={handleClearAll}>
                  {t('clear_all')}
                </button>
              </div>

              {/* Interface Checkboxes List */}
              <div className="list-box" style={{ maxHeight: 240 }}>
                {availableIfaces.length === 0 ? (
                  <div className="empty-note">{t('loading_interfaces')}</div>
                ) : (
                  availableIfaces.map(iface => {
                    const isChecked = selectedIfaces.includes(iface.name);
                    return (
                      <div
                        key={iface.name}
                        className={`list-row${isChecked ? ' is-selected' : ''}`}
                        onClick={() => handleToggleIface(iface.name)}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
                          <input
                            type="checkbox"
                            checked={isChecked}
                            readOnly
                            style={{ cursor: 'pointer', pointerEvents: 'none' }}
                          />
                          <span style={{
                            width: 8,
                            height: 8,
                            borderRadius: 'var(--radius-full)',
                            background: iface.running ? 'var(--color-success)' : 'var(--text-muted)',
                            flexShrink: 0
                          }} />
                          <span className="truncate" style={{ fontWeight: isChecked ? 700 : 500 }}>{iface.name}</span>
                        </div>
                        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', flexShrink: 0 }}>
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
