import React, { useState, useEffect, useRef } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { formatBytes, formatSpeed } from '../utils/formatters';
import { 
  Activity, Cpu, HardDrive, Thermometer, Zap, Network, 
  ArrowDown, ArrowUp, RefreshCw, Check, Clock, Layers, Sparkles, Sliders, X 
} from 'lucide-react';

const RANGES = ['1h', '6h', '24h', '7d', '30d'];

function formatTimeTick(dateStr, range) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return '';
  const hh = d.getHours().toString().padStart(2, '0');
  const mm = d.getMinutes().toString().padStart(2, '0');
  if (range === '7d' || range === '30d') {
    const month = (d.getMonth() + 1).toString().padStart(2, '0');
    const day = d.getDate().toString().padStart(2, '0');
    return `${month}/${day} ${hh}:${mm}`;
  }
  return `${hh}:${mm}`;
}

function formatTimeTooltip(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// Helper to generate smooth SVG path from data points
function generateSvgPath(points, valueKey, width, height, minVal = 0, maxVal = 100, padX = 66, padY = 16, padBottom = 26) {
  if (!points || points.length < 2) return '';
  const innerWidth = width - padX - 16;
  const innerHeight = height - padY - padBottom;
  const effectiveMax = maxVal > minVal ? maxVal : minVal + 1;

  const coords = points.map((p, i) => {
    const x = padX + (i / (points.length - 1)) * innerWidth;
    const rawVal = p[valueKey] !== null && p[valueKey] !== undefined ? p[valueKey] : minVal;
    const clamped = Math.max(minVal, Math.min(effectiveMax, rawVal));
    const y = height - padBottom - ((clamped - minVal) / (effectiveMax - minVal)) * innerHeight;
    return { x, y };
  });

  let d = `M ${coords[0].x},${coords[0].y}`;
  for (let i = 0; i < coords.length - 1; i++) {
    const curr = coords[i];
    const next = coords[i + 1];
    const mx = (curr.x + next.x) / 2;
    d += ` C ${mx},${curr.y} ${mx},${next.y} ${next.x},${next.y}`;
  }
  return d;
}

// Generate closed area path for gradient fills
function generateSvgArea(points, valueKey, width, height, minVal = 0, maxVal = 100, padX = 66, padY = 16, padBottom = 26) {
  const linePath = generateSvgPath(points, valueKey, width, height, minVal, maxVal, padX, padY, padBottom);
  if (!linePath) return '';
  const lastX = width - 16;
  const firstX = padX;
  const bottomY = height - padBottom;
  return `${linePath} L ${lastX},${bottomY} L ${firstX},${bottomY} Z`;
}

function ChartCard({
  title,
  subtitle,
  icon: Icon,
  iconColor,
  headerRight,
  points,
  range,
  series, // Array of { key, color, label, formatVal, gradientId, strokeWidth }
  yMin = 0,
  yMax = 100,
  yTicks = [], // Array of { val, label }
  thresholdLine = null, // { val, color, label }
  emptyMessage = null
}) {
  const { t } = useI18n();
  const [hoverIndex, setHoverIndex] = useState(null);
  const containerRef = useRef(null);

  const svgWidth = 500;
  const svgHeight = 180;
  const padX = 66;
  const padY = 16;
  const padBottom = 28;
  const innerWidth = svgWidth - padX - 16;
  const innerHeight = svgHeight - padY - padBottom;

  const handlePointerMove = (e) => {
    if (!points || points.length < 2 || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const clientX = e.clientX ?? (e.touches && e.touches[0]?.clientX);
    if (clientX === undefined) return;
    const relativeX = (clientX - rect.left) / rect.width; // 0 to 1
    const svgRelX = relativeX * svgWidth;
    const clampedSvgX = Math.max(padX, Math.min(svgWidth - 16, svgRelX));
    const ratio = (clampedSvgX - padX) / innerWidth;
    const idx = Math.round(ratio * (points.length - 1));
    setHoverIndex(Math.max(0, Math.min(points.length - 1, idx)));
  };

  const handlePointerLeave = () => {
    setHoverIndex(null);
  };

  // Generate 4-5 X-axis time ticks
  const xTicks = [];
  if (points && points.length >= 2) {
    const tickCount = 4;
    for (let i = 0; i < tickCount; i++) {
      const idx = Math.round((i / (tickCount - 1)) * (points.length - 1));
      const pt = points[idx];
      if (pt) {
        const x = padX + (idx / (points.length - 1)) * innerWidth;
        xTicks.push({
          x,
          label: formatTimeTick(pt.timestamp, range),
          anchor: i === 0 ? 'start' : i === tickCount - 1 ? 'end' : 'middle'
        });
      }
    }
  }

  // Active hover point coordinates
  const activePoint = hoverIndex !== null && points ? points[hoverIndex] : null;
  const hoverX = activePoint && points.length > 1
    ? padX + (hoverIndex / (points.length - 1)) * innerWidth
    : null;

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Icon size={18} style={{ color: iconColor }} />
          <div>
            <div style={{ fontWeight: 700, fontSize: 'var(--fs-md)' }}>{title}</div>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>{subtitle}</div>
          </div>
        </div>
        {headerRight}
      </div>

      {/* SVG Canvas Container */}
      <div 
        ref={containerRef}
        onMouseMove={handlePointerMove}
        onMouseLeave={handlePointerLeave}
        onTouchMove={handlePointerMove}
        onTouchEnd={handlePointerLeave}
        style={{ 
          width: '100%', 
          height: 180, 
          position: 'relative', 
          background: 'var(--bg-secondary)', 
          borderRadius: 'var(--radius-sm)', 
          overflow: 'hidden',
          cursor: 'crosshair',
          userSelect: 'none'
        }}
      >
        {(!points || points.length === 0) ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)', fontSize: 'var(--fs-sm)', padding: 20, textAlign: 'center' }}>
            {emptyMessage || t('no_metrics_yet')}
          </div>
        ) : (
          <svg viewBox={`0 0 ${svgWidth} ${svgHeight}`} preserveAspectRatio="none" style={{ width: '100%', height: '100%' }}>
            <defs>
              {series.map(s => (
                <linearGradient key={s.key} id={s.gradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={s.color} stopOpacity="0.4" />
                  <stop offset="100%" stopColor={s.color} stopOpacity="0.0" />
                </linearGradient>
              ))}
            </defs>

            {/* Horizontal Grid lines and Y-Axis Ticks */}
            {yTicks.map((yt, idx) => {
              const clamped = Math.max(yMin, Math.min(yMax, yt.val));
              const y = svgHeight - padBottom - ((clamped - yMin) / (yMax - yMin || 1)) * innerHeight;
              return (
                <g key={idx}>
                  <line 
                    x1={padX} 
                    y1={y} 
                    x2={svgWidth - 16} 
                    y2={y} 
                    stroke="var(--border-color)" 
                    strokeDasharray="3,3" 
                    opacity="0.6" 
                  />
                  <text 
                    x={padX - 6} 
                    y={y + 3.5} 
                    textAnchor="end" 
                    fontSize="9.5" 
                    fill="var(--text-muted)" 
                    fontFamily="monospace"
                  >
                    {yt.label}
                  </text>
                </g>
              );
            })}

            {/* Optional Threshold line (e.g. CPU 90%) */}
            {thresholdLine && (
              <g>
                {(() => {
                  const y = svgHeight - padBottom - ((thresholdLine.val - yMin) / (yMax - yMin || 1)) * innerHeight;
                  return (
                    <>
                      <line 
                        x1={padX} 
                        y1={y} 
                        x2={svgWidth - 16} 
                        y2={y} 
                        stroke={thresholdLine.color || 'var(--color-danger)'} 
                        strokeDasharray="4,4" 
                        opacity="0.8" 
                      />
                      <text 
                        x={svgWidth - 20} 
                        y={y - 3} 
                        textAnchor="end" 
                        fontSize="8.5" 
                        fill={thresholdLine.color || 'var(--color-danger)'} 
                        fontWeight="bold"
                      >
                        {thresholdLine.label}
                      </text>
                    </>
                  );
                })()}
              </g>
            )}

            {/* X-Axis bottom boundary line */}
            <line 
              x1={padX} 
              y1={svgHeight - padBottom} 
              x2={svgWidth - 16} 
              y2={svgHeight - padBottom} 
              stroke="var(--border-color)" 
            />

            {/* X-Axis Time Ticks */}
            {xTicks.map((xt, idx) => (
              <g key={idx}>
                <line 
                  x1={xt.x} 
                  y1={svgHeight - padBottom} 
                  x2={xt.x} 
                  y2={svgHeight - padBottom + 4} 
                  stroke="var(--border-color)" 
                />
                <text 
                  x={xt.x} 
                  y={svgHeight - padBottom + 16} 
                  textAnchor={xt.anchor} 
                  fontSize="9.5" 
                  fill="var(--text-muted)" 
                  fontFamily="monospace"
                >
                  {xt.label}
                </text>
              </g>
            ))}

            {/* Data Series (Areas and Lines) */}
            {series.map(s => (
              <g key={s.key}>
                <path 
                  d={generateSvgArea(points, s.key, svgWidth, svgHeight, yMin, yMax, padX, padY, padBottom)} 
                  fill={`url(#${s.gradientId})`} 
                />
                <path 
                  d={generateSvgPath(points, s.key, svgWidth, svgHeight, yMin, yMax, padX, padY, padBottom)} 
                  fill="none" 
                  stroke={s.color} 
                  strokeWidth={s.strokeWidth || 2.5} 
                  strokeLinecap="round" 
                />
              </g>
            ))}

            {/* Active Hover Crosshair Line & Dots */}
            {activePoint && hoverX !== null && (
              <g>
                <line 
                  x1={hoverX} 
                  y1={padY} 
                  x2={hoverX} 
                  y2={svgHeight - padBottom} 
                  stroke="var(--color-primary)" 
                  strokeWidth="1.2" 
                  strokeDasharray="2,2" 
                  opacity="0.85" 
                />

                {series.map(s => {
                  const val = activePoint[s.key] ?? yMin;
                  const clamped = Math.max(yMin, Math.min(yMax, val));
                  const y = svgHeight - padBottom - ((clamped - yMin) / (yMax - yMin || 1)) * innerHeight;
                  return (
                    <circle 
                      key={s.key} 
                      cx={hoverX} 
                      cy={y} 
                      r="4.5" 
                      fill={s.color} 
                      stroke="var(--bg-primary)" 
                      strokeWidth="2" 
                    />
                  );
                })}
              </g>
            )}
          </svg>
        )}

        {/* Floating Tooltip Box */}
        {activePoint && (
          <div 
            style={{
              position: 'absolute',
              top: 10,
              left: hoverX !== null && hoverX > svgWidth * 0.65 ? 'auto' : `${(hoverX / svgWidth) * 100 + 3}%`,
              right: hoverX !== null && hoverX > svgWidth * 0.65 ? `${100 - (hoverX / svgWidth) * 100 + 3}%` : 'auto',
              background: 'rgba(15, 23, 42, 0.92)',
              border: '1px solid var(--border-color)',
              backdropFilter: 'blur(8px)',
              padding: '6px 10px',
              borderRadius: 'var(--radius-sm)',
              fontSize: 'var(--fs-xs)',
              boxShadow: '0 4px 14px rgba(0,0,0,0.4)',
              pointerEvents: 'none',
              zIndex: 10,
              display: 'flex',
              flexDirection: 'column',
              gap: 3,
              minWidth: 120
            }}
          >
            <div style={{ color: 'var(--text-muted)', fontSize: 'var(--fs-2xs)', borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: 2 }}>
              🕒 {formatTimeTooltip(activePoint.timestamp)}
            </div>
            {series.map(s => (
              <div key={s.key} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}>
                <span style={{ color: s.color, fontWeight: 600 }}>{s.label}:</span>
                <span className="font-mono" style={{ fontWeight: 700, color: 'var(--text-primary)' }}>
                  {s.formatVal ? s.formatVal(activePoint[s.key], activePoint) : activePoint[s.key]}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function MetricCharts({ activeRouterId }) {
  const { t } = useI18n();
  const [range, setRange] = useState('1h');
  const [healthMetric, setHealthMetric] = useState('temp'); // 'temp' | 'voltage'
  const [systemMetrics, setSystemMetrics] = useState(null);
  const [ifaceMetrics, setIfaceMetrics] = useState(null);
  const [availableIfaces, setAvailableIfaces] = useState([]);
  const [selectedIfaces, setSelectedIfaces] = useState([]);
  const [loading, setLoading] = useState(true);
  const [savingConfig, setSavingConfig] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [tempThreshold, setTempThreshold] = useState(80);
  const [showThresholdInput, setShowThresholdInput] = useState(false);
  const [inputThreshold, setInputThreshold] = useState('80');

  // Load warning threshold from settings on mount
  useEffect(() => {
    api.getSettings().then(res => {
      if (res.data?.temp_warning_threshold) {
        const val = parseInt(res.data.temp_warning_threshold, 10);
        if (!isNaN(val)) {
          setTempThreshold(val);
          setInputThreshold(String(val));
        }
      }
    }).catch(() => {});
  }, []);

  const handleSaveTempThreshold = async (newVal) => {
    const valNum = parseInt(newVal, 10);
    if (!isNaN(valNum) && valNum >= 40 && valNum <= 110) {
      setTempThreshold(valNum);
      setInputThreshold(String(valNum));
      try {
        await api.saveSettings({ temp_warning_threshold: String(valNum) });
      } catch (e) {
        console.debug('Failed to save temp threshold', e);
      }
    }
    setShowThresholdInput(false);
  };

  useEffect(() => {
    loadInterfacesAndConfig();
  }, [activeRouterId]);

  useEffect(() => {
    loadMetrics();
    const interval = setInterval(loadMetrics, 10000);
    return () => clearInterval(interval);
  }, [range, activeRouterId, selectedIfaces]);

  const loadInterfacesAndConfig = async () => {
    try {
      const [ifacesRes, configRes] = await Promise.all([
        api.getAvailableInterfaces(activeRouterId),
        api.getMonitoredInterfacesConfig(activeRouterId)
      ]);

      const ifaces = ifacesRes.data || [];
      setAvailableIfaces(ifaces);

      const saved = configRes.data?.selected_interfaces || [];
      if (saved.length > 0) {
        setSelectedIfaces(saved);
      } else {
        const defaults = ifaces.filter(i => i.running && !i.disabled).map(i => i.name).slice(0, 2);
        setSelectedIfaces(defaults.length > 0 ? defaults : (ifaces[0]?.name ? [ifaces[0].name] : []));
      }
    } catch (e) {
      console.debug('Failed to load interfaces config', e);
    }
  };

  const loadMetrics = async () => {
    try {
      const [sysRes, ifaceRes] = await Promise.all([
        api.getSystemMetrics(range, activeRouterId),
        api.getInterfaceMetrics(range, selectedIfaces, activeRouterId)
      ]);
      setSystemMetrics(sysRes.data);
      setIfaceMetrics(ifaceRes.data);
    } catch (e) {
      console.debug('Failed to load metrics', e);
    } finally {
      setLoading(false);
    }
  };

  const handleToggleIface = (name) => {
    setSelectedIfaces(prev => 
      prev.includes(name) ? prev.filter(x => x !== name) : [...prev, name]
    );
  };

  const handleSaveDefaultIfaces = async () => {
    setSavingConfig(true);
    setSaveSuccess(false);
    try {
      await api.saveMonitoredInterfacesConfig(activeRouterId, selectedIfaces);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2500);
    } catch (e) {
      console.debug('Failed to save default interfaces', e);
    } finally {
      setSavingConfig(false);
    }
  };

  const sysPoints = systemMetrics?.points || [];
  const ifacePoints = ifaceMetrics?.points || [];

  // Dynamic calculations for Y scales
  const maxRx = Math.max(...ifacePoints.map(p => p.rx_rate_bps || 0), 100000); // at least 100 Kbps
  const maxTx = Math.max(...ifacePoints.map(p => p.tx_rate_bps || 0), 100000);
  const maxBps = Math.max(maxRx, maxTx) * 1.15;

  const hasTemp = sysPoints.some(p => p.temperature !== null && p.temperature !== undefined) || (systemMetrics?.current_temp !== null && systemMetrics?.current_temp !== undefined);
  const hasVolt = sysPoints.some(p => p.voltage !== null && p.voltage !== undefined) || (systemMetrics?.current_voltage !== null && systemMetrics?.current_voltage !== undefined);

  // Dynamic temperature scaling based on active readings
  const validTemps = sysPoints.map(p => p.temperature).filter(t => t !== null && t !== undefined && t > 0);
  if (systemMetrics?.current_temp !== null && systemMetrics?.current_temp !== undefined) {
    validTemps.push(systemMetrics.current_temp);
  }
  const rawMinTemp = validTemps.length > 0 ? Math.min(...validTemps) : 65;
  const rawMaxTemp = validTemps.length > 0 ? Math.max(...validTemps) : 75;

  // Zoomed-in dynamic scale with at least 8°C spread
  const tempSpread = Math.max(8, (rawMaxTemp - rawMinTemp) + 4);
  const tempMid = (rawMaxTemp + rawMinTemp) / 2;
  const dynMinTemp = Math.max(0, Math.floor(tempMid - tempSpread / 2));
  const dynMaxTemp = Math.max(tempThreshold + 2, Math.ceil(tempMid + tempSpread / 2));
  const dynMidTemp = Math.round((dynMinTemp + dynMaxTemp) / 2);

  const voltValues = sysPoints.map(p => p.voltage).filter(v => v !== null && v !== undefined);
  const maxVolt = voltValues.length > 0 ? Math.max(Math.max(...voltValues) * 1.15, 15) : 28;
  const minVolt = voltValues.length > 0 ? Math.max(0, Math.min(...voltValues) * 0.85) : 0;

  const isTempAlert = (systemMetrics?.current_temp || 0) >= tempThreshold;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      {/* Top Controls: Range & Interface Selector */}
      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Activity size={20} style={{ color: 'var(--color-primary)' }} />
            <div>
              <h3 style={{ fontSize: 'var(--fs-lg)', fontWeight: 700 }}>{t('metrics_title')}</h3>
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
                Live & Historical telemetry for active MikroTik router
              </div>
            </div>
          </div>

          {/* Time Range Selector */}
          <div className="range-group">
            <Clock size={13} />
            {RANGES.map(r => (
              <button
                key={r}
                className={`range-btn ${range === r ? 'active' : ''}`}
                onClick={() => setRange(r)}
              >
                {t(`range_${r}`)}
              </button>
            ))}
          </div>
        </div>

        {/* Monitored Interfaces Multi-select Bar */}
        <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: 12, display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-secondary)' }}>
              <Layers size={15} />
              <span>{t('select_interfaces')}:</span>
            </div>

            {availableIfaces.map(iface => {
              const isSelected = selectedIfaces.includes(iface.name);
              return (
                <button
                  key={iface.name}
                  type="button"
                  onClick={() => handleToggleIface(iface.name)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    padding: '4px 10px',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: 'var(--fs-xs)',
                    fontWeight: 600,
                    cursor: 'pointer',
                    transition: 'all 0.15s ease',
                    background: isSelected ? 'var(--color-primary-glow)' : 'var(--bg-secondary)',
                    color: isSelected ? 'var(--color-primary)' : 'var(--text-secondary)',
                    border: `1px solid ${isSelected ? 'var(--color-primary)' : 'var(--border-color)'}`
                  }}
                >
                  <span style={{
                    width: 6,
                    height: 6,
                    borderRadius: 'var(--radius-full)',
                    background: iface.running ? 'var(--color-success)' : 'var(--text-muted)'
                  }} />
                  <span>{iface.name}</span>
                  {isSelected && <Check size={12} />}
                </button>
              );
            })}
          </div>

          <button
            className={`btn btn-sm ${saveSuccess ? 'btn-primary' : 'btn-secondary'}`}
            style={{ fontSize: 'var(--fs-xs)', padding: '4px 10px' }}
            onClick={handleSaveDefaultIfaces}
            disabled={savingConfig}
          >
            {saveSuccess ? (
              <>
                <Check size={13} />
                <span>{t('saved_ifaces_success')}</span>
              </>
            ) : (
              <span>{t('save_default_ifaces')}</span>
            )}
          </button>
        </div>
      </div>

      {/* Main Grid of 4 Interactive Charts */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(460px, 1fr))', gap: 16 }}>
        
        {/* CHART 1: Interface Bandwidth (RX / TX) */}
        <ChartCard
          title={t('interface_bandwidth')}
          subtitle={selectedIfaces.length > 0 ? selectedIfaces.join(' + ') : 'No interfaces selected'}
          icon={Network}
          iconColor="var(--color-primary)"
          points={ifacePoints}
          range={range}
          emptyMessage={selectedIfaces.length === 0 ? "No interfaces selected. Click interface buttons above to monitor traffic." : null}
          headerRight={
            selectedIfaces.length > 0 ? (
              <div style={{ display: 'flex', gap: 12, fontSize: 'var(--fs-sm)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#10b981', fontWeight: 700 }}>
                  <ArrowDown size={14} />
                  <span>{formatSpeed(ifaceMetrics?.current_rx_bps || 0)}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#ec4899', fontWeight: 700 }}>
                  <ArrowUp size={14} />
                  <span>{formatSpeed(ifaceMetrics?.current_tx_bps || 0)}</span>
                </div>
              </div>
            ) : null
          }
          yMin={0}
          yMax={maxBps}
          yTicks={[
            { val: maxBps, label: formatSpeed(maxBps) },
            { val: maxBps / 2, label: formatSpeed(maxBps / 2) },
            { val: 0, label: '0' }
          ]}
          series={[
            {
              key: 'rx_rate_bps',
              color: '#10b981',
              label: 'RX (Down)',
              gradientId: 'rxGrad',
              formatVal: (val) => formatSpeed(val || 0)
            },
            {
              key: 'tx_rate_bps',
              color: '#ec4899',
              label: 'TX (Up)',
              gradientId: 'txGrad',
              formatVal: (val) => formatSpeed(val || 0)
            }
          ]}
        />

        {/* CHART 2: CPU Load (%) */}
        <ChartCard
          title={t('cpu_history')}
          subtitle="0% - 100% Processor Utilization"
          icon={Cpu}
          iconColor="var(--color-primary)"
          points={sysPoints}
          range={range}
          headerRight={
            <div style={{ fontSize: 'var(--fs-lg)', fontWeight: 800, color: 'var(--color-primary)' }} className="font-mono">
              {systemMetrics?.current_cpu ?? 0}%
            </div>
          }
          yMin={0}
          yMax={100}
          yTicks={[
            { val: 100, label: '100%' },
            { val: 50, label: '50%' },
            { val: 0, label: '0%' }
          ]}
          thresholdLine={{ val: 90, color: 'var(--color-danger)', label: '90% Max' }}
          series={[
            {
              key: 'cpu_load',
              color: '#0b72c9',
              label: 'CPU Load',
              gradientId: 'cpuGrad',
              formatVal: (val) => `${val ?? 0}%`
            }
          ]}
        />

        {/* CHART 3: RAM Usage (%) */}
        <ChartCard
          title={t('ram_history')}
          subtitle="Memory Allocation & Footprint"
          icon={HardDrive}
          iconColor="#8b5cf6"
          points={sysPoints}
          range={range}
          headerRight={
            <div style={{ fontSize: 'var(--fs-lg)', fontWeight: 800, color: '#8b5cf6' }} className="font-mono">
              {systemMetrics?.current_ram_pct ?? 0}%
            </div>
          }
          yMin={0}
          yMax={100}
          yTicks={[
            { val: 100, label: '100%' },
            { val: 50, label: '50%' },
            { val: 0, label: '0%' }
          ]}
          series={[
            {
              key: 'memory_usage_pct',
              color: '#8b5cf6',
              label: 'RAM Usage',
              gradientId: 'ramGrad',
              formatVal: (val, pt) => `${val ?? 0}% (${pt?.memory_used_mb ?? 0} MB / ${pt?.memory_total_mb ?? 0} MB)`
            }
          ]}
        />

        {/* CHART 4: Temperature / Voltage (Dynamic Scaling & Warning Threshold) */}
        <ChartCard
          title={healthMetric === 'voltage' ? (t('voltage_history') || 'Board Voltage (V)') : (t('temp_history') || 'Board Temperature (°C)')}
          subtitle={
            healthMetric === 'voltage' 
              ? (hasVolt ? "Input Power Supply Telemetry" : "Voltage sensor not present on this hardware")
              : (hasTemp ? `Dynamic Scale (${dynMinTemp}°C – ${dynMaxTemp}°C)` : "Temperature sensor not present on this hardware")
          }
          icon={healthMetric === 'voltage' ? Zap : Thermometer}
          iconColor={healthMetric === 'voltage' ? '#06b6d4' : (isTempAlert ? '#ef4444' : '#f59e0b')}
          points={sysPoints}
          range={range}
          emptyMessage={
            healthMetric === 'voltage' && !hasVolt
              ? "Voltage sensor is not physically present on this router model."
              : (healthMetric === 'temp' && !hasTemp ? "Temperature sensor is not available on this router model." : null)
          }
          thresholdLine={
            healthMetric === 'temp' && hasTemp
              ? { val: tempThreshold, color: '#ef4444', label: `${tempThreshold}°C Warning` }
              : null
          }
          headerRight={
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {/* Temperature Alert Threshold Quick Setting */}
              {healthMetric === 'temp' && (
                showThresholdInput ? (
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    background: 'var(--bg-card)',
                    padding: '2px 8px',
                    borderRadius: 'var(--radius-md)',
                    border: '1px solid var(--color-primary)',
                    boxShadow: '0 0 10px rgba(11, 114, 201, 0.25)'
                  }}>
                    <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 600, color: 'var(--text-muted)' }}>Alert:</span>
                    <input
                      type="number"
                      value={inputThreshold}
                      onChange={e => setInputThreshold(e.target.value)}
                      style={{
                        width: 44,
                        height: 24,
                        fontSize: 'var(--fs-sm)',
                        textAlign: 'center',
                        borderRadius: 'var(--radius-xs)',
                        border: '1px solid var(--border-color)',
                        background: 'var(--bg-secondary)',
                        color: 'var(--text-primary)',
                        fontWeight: 700,
                        padding: '0 2px'
                      }}
                      min="40"
                      max="110"
                    />
                    <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, color: 'var(--text-secondary)' }}>°C</span>
                    <button
                      type="button"
                      className="btn btn-sm btn-primary"
                      style={{ padding: '2px 8px', height: 24, fontSize: 'var(--fs-xs)', display: 'flex', alignItems: 'center', gap: 3 }}
                      onClick={() => handleSaveTempThreshold(inputThreshold)}
                      title="Save threshold"
                    >
                      <Check size={12} />
                      <span>{t('save')}</span>
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost"
                      style={{ padding: '2px 6px', height: 24, fontSize: 'var(--fs-xs)' }}
                      onClick={() => setShowThresholdInput(false)}
                      title="Cancel"
                    >
                      <X size={12} />
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setShowThresholdInput(true)}
                    style={{
                      fontSize: 'var(--fs-xs)',
                      fontWeight: 600,
                      color: 'var(--text-secondary)',
                      background: 'var(--bg-secondary)',
                      padding: '4px 8px',
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border-color)',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 5,
                      transition: 'all 0.15s ease'
                    }}
                    title="Click to change Temperature Warning Threshold"
                  >
                    <Sliders size={12} style={{ color: 'var(--color-warning, #f59e0b)' }} />
                    <span>{t('temp_threshold_label') || 'Alert'}: <strong style={{ color: 'var(--text-primary)' }}>{tempThreshold}°C</strong></span>
                  </button>
                )
              )}
              {/* Either / Or Switcher Pills */}
              {/* Same segmented control as every other either/or choice. The
                  active pill keeps its metric's own colour, since temperature
                  and voltage are read as different quantities. */}
              <div className="range-group">
                <button
                  type="button"
                  className={`range-btn${healthMetric === 'temp' ? ' active' : ''}`}
                  onClick={() => setHealthMetric('temp')}
                  style={healthMetric === 'temp' ? {
                    background: isTempAlert ? 'var(--color-danger)' : 'var(--color-warning)',
                    borderColor: isTempAlert ? 'var(--color-danger)' : 'var(--color-warning)'
                  } : undefined}
                  title="Switch to Temperature View"
                >
                  <Thermometer size={12} style={{ verticalAlign: '-2px', marginRight: 4 }} />
                  {systemMetrics?.current_temp !== null && systemMetrics?.current_temp !== undefined ? `${systemMetrics.current_temp}°C` : (t('temp') || 'Temp')}
                </button>
                <button
                  type="button"
                  className={`range-btn${healthMetric === 'voltage' ? ' active' : ''}`}
                  onClick={() => setHealthMetric('voltage')}
                  style={healthMetric === 'voltage' ? {
                    background: 'var(--color-info)',
                    borderColor: 'var(--color-info)'
                  } : undefined}
                  title="Switch to Voltage View"
                >
                  <Zap size={12} style={{ verticalAlign: '-2px', marginRight: 4 }} />
                  {systemMetrics?.current_voltage !== null && systemMetrics?.current_voltage !== undefined ? `${systemMetrics.current_voltage}V` : (t('voltage') || 'Volt')}
                </button>
              </div>
            </div>
          }
          yMin={healthMetric === 'voltage' ? minVolt : dynMinTemp}
          yMax={healthMetric === 'voltage' ? maxVolt : dynMaxTemp}
          yTicks={
            healthMetric === 'voltage' ? [
              { val: maxVolt, label: `${maxVolt.toFixed(1)}V` },
              { val: (maxVolt + minVolt) / 2, label: `${((maxVolt + minVolt) / 2).toFixed(1)}V` },
              { val: minVolt, label: `${minVolt.toFixed(1)}V` }
            ] : [
              { val: dynMaxTemp, label: `${dynMaxTemp}°C` },
              { val: dynMidTemp, label: `${dynMidTemp}°C` },
              { val: dynMinTemp, label: `${dynMinTemp}°C` }
            ]
          }
          series={
            healthMetric === 'voltage' ? [
              {
                key: 'voltage',
                color: '#06b6d4',
                label: 'Voltage',
                gradientId: 'voltGrad',
                formatVal: (val) => val !== null && val !== undefined ? `${val} V` : 'N/A'
              }
            ] : [
              {
                key: 'temperature',
                color: isTempAlert ? '#ef4444' : '#f59e0b',
                label: 'Temperature',
                gradientId: 'tempGrad',
                formatVal: (val) => val !== null && val !== undefined ? `${val}°C` : 'N/A'
              }
            ]
          }
        />

      </div>
    </div>
  );
}
