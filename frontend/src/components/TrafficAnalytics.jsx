import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { formatBytes } from '../utils/formatters';
import { DonutChart, DONUT_PALETTE, DONUT_OTHER_COLOR } from './DonutChart';
import {
  Calendar,
  Clock,
  ArrowDown,
  ArrowUp,
  BarChart2,
  Users,
  Smartphone,
  Layers,
  Search,
  Settings,
  X,
  Check,
  Zap,
  Activity,
  Filter,
  EyeOff,
  AlertTriangle
} from 'lucide-react';

/**
 * Turn per-row totals into donut segments: the biggest `topN` by volume get
 * their own colour, everything else is folded into a single muted "Other" slice
 * so the chart never sprouts a dozen hairline wedges.
 */
function toDonutSegments(rows, labelOf, otherLabel, topN = 6) {
  const sorted = rows
    .map(r => ({ label: labelOf(r), value: r.total_bytes || 0 }))
    .filter(s => s.value > 0)
    .sort((a, b) => b.value - a.value);
  const head = sorted.slice(0, topN).map((s, i) => ({
    ...s,
    color: DONUT_PALETTE[i % DONUT_PALETTE.length],
  }));
  const tail = sorted.slice(topN);
  if (tail.length) {
    head.push({
      label: `${otherLabel} (${tail.length})`,
      value: tail.reduce((sum, s) => sum + s.value, 0),
      color: DONUT_OTHER_COLOR,
    });
  }
  return head;
}

/**
 * Share of total traffic as a bar plus its number. A bare percentage forces the
 * reader to compare figures mentally; the bar makes the ranking pre-attentive.
 */
function ShareBar({ pct }) {
  const value = Number(pct) || 0;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
      <div style={{
        flex: 1,
        height: 5,
        minWidth: 40,
        background: 'var(--bg-secondary)',
        borderRadius: 'var(--radius-xs)',
        overflow: 'hidden'
      }}>
        <div style={{
          width: `${Math.min(Math.max(value, 0), 100)}%`,
          height: '100%',
          background: 'var(--color-primary)',
          borderRadius: 'var(--radius-xs)'
        }} />
      </div>
      <span className="font-mono" style={{
        fontSize: 'var(--fs-xs)',
        fontWeight: 700,
        color: value > 0 ? 'var(--color-primary)' : 'var(--text-muted)',
        minWidth: 42,
        textAlign: 'right'
      }}>
        {value.toFixed(1)}%
      </span>
    </div>
  );
}

/**
 * Clickable table header that reports and toggles the active sort.
 */
function SortHeader({ label, field, sort, onSort, align = 'left' }) {
  const active = sort.field === field;
  return (
    <th
      onClick={() => onSort(field)}
      style={{
        padding: '8px 12px',
        cursor: 'pointer',
        userSelect: 'none',
        textAlign: align,
        color: active ? 'var(--color-primary)' : undefined,
        whiteSpace: 'nowrap'
      }}
      title={label}
    >
      {label}
      <span style={{ opacity: active ? 1 : 0.25, marginLeft: 4 }}>
        {active && sort.dir === 'asc' ? '▲' : '▼'}
      </span>
    </th>
  );
}

/** Sort a copy of `rows` by the active field, numbers and strings alike. */
function sortRows(rows, sort) {
  const sorted = [...rows];
  sorted.sort((a, b) => {
    const av = a[sort.field];
    const bv = b[sort.field];
    if (typeof av === 'string' || typeof bv === 'string') {
      const cmp = String(av ?? '').localeCompare(String(bv ?? ''));
      return sort.dir === 'asc' ? cmp : -cmp;
    }
    const cmp = (Number(av) || 0) - (Number(bv) || 0);
    return sort.dir === 'asc' ? cmp : -cmp;
  });
  return sorted;
}

const PRESETS = [
  { id: 'today', labelKey: 'range_today' },
  { id: 'yesterday', labelKey: 'range_yesterday' },
  { id: '7d', labelKey: 'range_7d' },
  { id: '30d', labelKey: 'range_30d' },
  { id: 'billing_current', labelKey: 'range_billing_current' },
  { id: 'billing_previous', labelKey: 'range_billing_previous' },
  { id: 'custom', labelKey: 'range_custom' },
];

// Per-browser dismissal of the coverage notice. Keyed by a signature of the
// gap's severity, so a worse gap - or a different kind - shows again rather than
// staying silenced forever on data we cannot reconstruct.
const COVERAGE_DISMISS_KEY = 'mm_coverage_notice_dismissed';

export function TrafficAnalytics({ activeRouter }) {
  const { t } = useI18n();
  const [preset, setPreset] = useState('7d');
  const [coverageDismissed, setCoverageDismissed] = useState(() => {
    try { return localStorage.getItem(COVERAGE_DISMISS_KEY) || ''; } catch { return ''; }
  });
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [breakdownTab, setBreakdownTab] = useState('overview'); // 'overview' | 'users' | 'devices'
  const [searchTerm, setSearchTerm] = useState('');
  const [userFilter, setUserFilter] = useState('all');
  const [showHidden, setShowHidden] = useState(false);
  // Table sort state. Default to heaviest consumer first, which is the
  // question these tables are usually opened to answer.
  const [userSort, setUserSort] = useState({ field: 'total_bytes', dir: 'desc' });
  const [deviceSort, setDeviceSort] = useState({ field: 'total_bytes', dir: 'desc' });


  // Billing Cycle Settings Modal
  const [billingModalOpen, setBillingModalOpen] = useState(false);
  const [anchorDay, setAnchorDay] = useState(1);
  const [isSavingBilling, setIsSavingBilling] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Load billing cycle anchor day
  useEffect(() => {
    api.getBillingCycleConfig()
      .then(res => {
        if (res?.data?.anchor_day) {
          setAnchorDay(res.data.anchor_day);
        }
      })
      .catch(() => {});
  }, []);

  // Fetch traffic analytics data
  const loadAnalytics = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await api.getTrafficAnalytics({
        preset,
        startDate: preset === 'custom' ? customStart : null,
        endDate: preset === 'custom' ? customEnd : null,
        routerId: activeRouter?.id || null
      });
      if (res?.data) {
        setData(res.data);
      }
    } catch (err) {
      console.error('Failed to load traffic analytics:', err);
    } finally {
      if (!silent) setLoading(false);
    }
  };

  useEffect(() => {
    if (preset !== 'custom' || (customStart && customEnd)) {
      loadAnalytics(false);
    }

    // Auto-refresh analytics data every 8 seconds for live bandwidth consumption updates
    const interval = setInterval(() => {
      if (preset !== 'custom' || (customStart && customEnd)) {
        loadAnalytics(true);
      }
    }, 8000);
    return () => clearInterval(interval);
  }, [preset, activeRouter?.id, customStart, customEnd]);

  const handleSaveBillingCycle = async () => {
    setIsSavingBilling(true);
    try {
      await api.saveBillingCycleConfig(anchorDay);
      setSaveSuccess(true);
      setTimeout(() => {
        setBillingModalOpen(false);
        setSaveSuccess(false);
        loadAnalytics();
      }, 500);
    } catch (err) {
      console.error('Failed to save billing cycle anchor:', err);
    } finally {
      setIsSavingBilling(false);
    }
  };

  // Clicking the active column flips direction; a new column starts descending,
  // because "who used the most" is the usual first question.
  const makeSortToggle = (setSort) => (field) =>
    setSort(prev => (prev.field === field
      ? { field, dir: prev.dir === 'desc' ? 'asc' : 'desc' }
      : { field, dir: 'desc' }));
  const toggleUserSort = makeSortToggle(setUserSort);
  const toggleDeviceSort = makeSortToggle(setDeviceSort);

  const gateway = data?.gateway || { total_bytes_in: 0, total_bytes_out: 0, total_bytes: 0, monitored_interfaces: [] };
  // Cross-check between WAN-measured gateway volume and per-device accounted volume.
  const health = data?.accounting_health || { status: 'no_data', coverage_pct: 0, message: null };
  const coverageSig = `${health.status}:${Math.round(health.coverage_pct || 0)}`;
  const showCoverageNotice =
    health.status !== 'ok' && health.status !== 'no_data' && coverageSig !== coverageDismissed;
  const dismissCoverageNotice = () => {
    try { localStorage.setItem(COVERAGE_DISMISS_KEY, coverageSig); } catch { /* private mode */ }
    setCoverageDismissed(coverageSig);
  };
  const users = data?.users || [];
  const devices = data?.devices || [];
  const timeline = data?.timeline || [];

  // Pie segments for the current range - biggest few by volume, the rest in one
  // "Other" slice.
  const userSegments = toDonutSegments(users, u => u.user_name, t('donut_other'));
  const deviceSegments = toDonutSegments(
    devices, d => d.custom_name || d.hostname || d.mac_address, t('donut_other')
  );

  // Filter devices
  const filteredDevices = devices.filter(d => {
    const matchSearch = searchTerm === '' ||
      (d.custom_name && d.custom_name.toLowerCase().includes(searchTerm.toLowerCase())) ||
      (d.hostname && d.hostname.toLowerCase().includes(searchTerm.toLowerCase())) ||
      (d.ip_address && d.ip_address.includes(searchTerm)) ||
      (d.mac_address && d.mac_address.toLowerCase().includes(searchTerm.toLowerCase())) ||
      (d.vendor && d.vendor.toLowerCase().includes(searchTerm.toLowerCase()));

    const matchUser = userFilter === 'all' ||
      (userFilter === 'unassigned' && !d.user_id) ||
      (String(d.user_id) === String(userFilter));

    return matchSearch && matchUser;
  });

  const maxDailyBytes = Math.max(...timeline.map(p => p.total_bytes), 1024 * 1024);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Header & Date Controls */}
      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{
              background: 'rgba(11, 114, 201, 0.15)',
              color: 'var(--color-primary)',
              width: 40,
              height: 40,
              borderRadius: 'var(--radius-md)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}>
              <BarChart2 size={22} />
            </div>
            <div>
              <h2 style={{ fontSize: 'var(--fs-xl)', fontWeight: 800 }}>{t('analytics_title')}</h2>
              <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)' }}>
                {t('analytics_subtitle')}
              </div>
            </div>
          </div>

          {/* Billing Cycle Setting Button */}
          <button
            className="btn btn-secondary btn-sm"
            style={{ fontSize: 'var(--fs-sm)', display: 'flex', alignItems: 'center', gap: 6 }}
            onClick={() => setBillingModalOpen(true)}
          >
            <Calendar size={14} style={{ color: 'var(--color-primary)' }} />
            {t('billing_cycle')}: <strong style={{ color: 'var(--text-primary)' }}>Day {anchorDay}</strong>
          </button>
        </div>

        {/* Range Preset Buttons */}
        <div className="range-group">
          <Clock size={13} />
          {PRESETS.map(p => (
            <button
              key={p.id}
              className={`range-btn ${preset === p.id ? 'active' : ''}`}
              onClick={() => setPreset(p.id)}
            >
              {t(p.labelKey)}
            </button>
          ))}

          {/* Custom Date Inputs */}
          {preset === 'custom' && (
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginLeft: 8 }}>
              <input
                type="date"
                className="form-input"
                style={{ height: 28, fontSize: 'var(--fs-xs)', padding: '2px 6px', width: 130 }}
                value={customStart}
                onChange={e => setCustomStart(e.target.value)}
              />
              <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>→</span>
              <input
                type="date"
                className="form-input"
                style={{ height: 28, fontSize: 'var(--fs-xs)', padding: '2px 6px', width: 130 }}
                value={customEnd}
                onChange={e => setCustomEnd(e.target.value)}
              />
              <button
                className="btn btn-primary btn-sm"
                style={{ height: 28, fontSize: 'var(--fs-xs)', padding: '0 10px' }}
                onClick={loadAnalytics}
              >
                {t('apply_dates')}
              </button>
            </div>
          )}

          {data && (
            <span style={{ marginLeft: 'auto', fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', paddingRight: 4 }}>
              {data.start_date} → {data.end_date}
            </span>
          )}
        </div>
      </div>

      {/* Gateway Executive Summary Cards */}
      {/* The ISP billing-cycle allowance used to have its own panel here; it now
          lives in the always-on quota strip under the header, on every page. */}

      {/* Accounting coverage notice. The gateway total is measured at the WAN
          interface; the per-user/per-device breakdown is measured per device.
          When the two disagree the breakdown is incomplete and must say so
          rather than quietly showing plausible-looking zeros. */}
      {showCoverageNotice && (
        <div
          className="card"
          style={{
            padding: '12px 16px',
            display: 'flex',
            alignItems: 'flex-start',
            gap: 12,
            borderLeft: `3px solid ${health.status === 'degraded' ? 'var(--color-danger, #e74c3c)' : 'var(--color-warning, #f39c12)'}`
          }}
        >
          <AlertTriangle
            size={18}
            style={{
              flexShrink: 0,
              marginTop: 2,
              color: health.status === 'degraded' ? 'var(--color-danger, #e74c3c)' : 'var(--color-warning, #f39c12)'
            }}
          />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)', color: 'var(--text-primary)' }}>
              {health.status === 'degraded' ? t('acct_degraded_title') : t('acct_partial_title')}
              <span className="font-mono" style={{ fontWeight: 600, color: 'var(--text-muted)', marginLeft: 8 }}>
                {t('acct_coverage')}: {roundPct(health.coverage_pct)}%
              </span>
            </div>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', marginTop: 2 }}>
              {health.message}
            </div>
            {health.status === 'partial' && (
              <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', marginTop: 4 }}>
                {t('acct_partial_help')}
              </div>
            )}
          </div>
          <button
            type="button"
            className="btn-icon"
            style={{ width: 22, height: 22, flexShrink: 0 }}
            onClick={dismissCoverageNotice}
            title={t('acct_dismiss_hint')}
          >
            <X size={13} />
          </button>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14 }}>
        {/* Total Consumed */}
        <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{
            background: 'rgba(11, 114, 201, 0.15)',
            color: 'var(--color-primary)',
            width: 44,
            height: 44,
            borderRadius: 'var(--radius-md)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0
          }}>
            <Layers size={22} />
          </div>
          <div>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', fontWeight: 600 }}>{t('total_combined')}</div>
            <div className="font-mono" style={{ fontSize: 'var(--fs-2xl)', fontWeight: 800, color: 'var(--text-primary)' }}>
              {formatBytes(gateway.total_bytes)}
            </div>
            <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
              {gateway.monitored_interfaces.length > 0 ? `${t('interfaces_label')}: ${gateway.monitored_interfaces.join(', ')}` : t('all_interfaces')}
            </div>
          </div>
        </div>

        {/* Total Download (RX) */}
        <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{
            background: 'rgba(46, 204, 113, 0.15)',
            color: 'var(--color-success)',
            width: 44,
            height: 44,
            borderRadius: 'var(--radius-md)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0
          }}>
            <ArrowDown size={22} />
          </div>
          <div>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', fontWeight: 600 }}>{t('total_download')}</div>
            <div className="font-mono" style={{ fontSize: 'var(--fs-2xl)', fontWeight: 800, color: 'var(--color-success)' }}>
              {formatBytes(gateway.total_bytes_in)}
            </div>
            <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
              {gateway.total_bytes > 0 ? `${roundPct(gateway.total_bytes_in / gateway.total_bytes * 100)}% ${t('of_total')}` : '0%'}
            </div>
          </div>
        </div>

        {/* Total Upload (TX) */}
        <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{
            background: 'rgba(52, 152, 219, 0.15)',
            color: '#3498db',
            width: 44,
            height: 44,
            borderRadius: 'var(--radius-md)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0
          }}>
            <ArrowUp size={22} />
          </div>
          <div>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', fontWeight: 600 }}>{t('total_upload')}</div>
            <div className="font-mono" style={{ fontSize: 'var(--fs-2xl)', fontWeight: 800, color: '#3498db' }}>
              {formatBytes(gateway.total_bytes_out)}
            </div>
            <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
              {gateway.total_bytes > 0 ? `${roundPct(gateway.total_bytes_out / gateway.total_bytes * 100)}% ${t('of_total')}` : '0%'}
            </div>
          </div>
        </div>

        {/* Active Profiles & Devices */}
        <div className="card" style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{
            background: 'rgba(155, 89, 182, 0.15)',
            color: '#9b59b6',
            width: 44,
            height: 44,
            borderRadius: 'var(--radius-md)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0
          }}>
            <Users size={22} />
          </div>
          <div>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', fontWeight: 600 }}>{t('active_profiles_devices')}</div>
            <div className="font-mono" style={{ fontSize: 'var(--fs-xl)', fontWeight: 800 }}>
              {users.length} <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', fontWeight: 500 }}>{t('users_short')}</span> • {devices.length} <span style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', fontWeight: 500 }}>{t('devs_short')}</span>
            </div>
            <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
              {t('shaped_via_ros')}
            </div>
          </div>
        </div>
      </div>

      {/* Breakdown View Tabs */}
      <div className="card panel-flush">
        <div style={{
          display: 'flex',
          borderBottom: '1px solid var(--border-color)',
          background: 'var(--bg-secondary)',
          padding: '4px 12px 0 12px'
        }}>
          <button
            className={`nav-tab ${breakdownTab === 'overview' ? 'active' : ''}`}
            onClick={() => setBreakdownTab('overview')}
            style={{ fontSize: 'var(--fs-sm)', padding: '10px 18px', display: 'flex', alignItems: 'center', gap: 6 }}
          >
            <Activity size={15} />
            {t('breakdown_overview')}
          </button>
          <button
            className={`nav-tab ${breakdownTab === 'users' ? 'active' : ''}`}
            onClick={() => setBreakdownTab('users')}
            style={{ fontSize: 'var(--fs-sm)', padding: '10px 18px', display: 'flex', alignItems: 'center', gap: 6 }}
          >
            <Users size={15} />
            {t('breakdown_users')} ({users.length})
          </button>
          <button
            className={`nav-tab ${breakdownTab === 'devices' ? 'active' : ''}`}
            onClick={() => setBreakdownTab('devices')}
            style={{ fontSize: 'var(--fs-sm)', padding: '10px 18px', display: 'flex', alignItems: 'center', gap: 6 }}
          >
            <Smartphone size={15} />
            {t('breakdown_devices')} ({devices.length})
          </button>
        </div>

        <div style={{ padding: 20 }}>
          {/* TAB 1: OVERVIEW */}
          {breakdownTab === 'overview' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
              {/* Consumption share for the selected range, as pie charts. */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 20 }}>
                <div>
                  <h4 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Users size={16} style={{ color: 'var(--color-primary)' }} />
                    {t('share_by_user')}
                  </h4>
                  <DonutChart
                    segments={userSegments}
                    centerLabel={formatBytes(gateway.total_bytes)}
                    centerSub={t('range_total_short')}
                    formatValue={formatBytes}
                  />
                </div>
                <div>
                  <h4 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Smartphone size={16} style={{ color: 'var(--color-primary)' }} />
                    {t('share_by_device')}
                  </h4>
                  <DonutChart
                    segments={deviceSegments}
                    centerLabel={String(devices.length)}
                    centerSub={t('devs_short')}
                    formatValue={formatBytes}
                  />
                </div>
              </div>

              {/* Daily Timeline Visual */}
              <div>
                <h4 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Calendar size={16} style={{ color: 'var(--color-primary)' }} />
                  {t('traffic_timeline')} ({t('days_count', { count: timeline.length })})
                </h4>

                <div style={{
                  display: 'flex',
                  alignItems: 'flex-end',
                  gap: 6,
                  height: 140,
                  padding: '12px 10px',
                  background: 'var(--bg-secondary)',
                  borderRadius: 'var(--radius-md)',
                  border: '1px solid var(--border-color)',
                  overflowX: 'auto'
                }}>
                  {timeline.map(pt => {
                    const heightPct = Math.max((pt.total_bytes / maxDailyBytes) * 100, 4);
                    const rxPct = pt.total_bytes > 0 ? (pt.bytes_in / pt.total_bytes) * 100 : 50;
                    return (
                      <div
                        key={pt.record_date}
                        style={{
                          flex: 1,
                          minWidth: 28,
                          maxWidth: 60,
                          height: '100%',
                          display: 'flex',
                          flexDirection: 'column',
                          justifyContent: 'flex-end',
                          alignItems: 'center',
                          gap: 4
                        }}
                        title={`${pt.record_date}\n${t('table_total')}: ${formatBytes(pt.total_bytes)}\nDown: ${formatBytes(pt.bytes_in)}\nUp: ${formatBytes(pt.bytes_out)}`}
                      >
                        <div style={{
                          width: '100%',
                          height: `${heightPct}%`,
                          borderRadius: 'var(--radius-xs)',
                          overflow: 'hidden',
                          display: 'flex',
                          flexDirection: 'column',
                          background: 'var(--bg-input)'
                        }}>
                          <div style={{ height: `${rxPct}%`, background: 'var(--color-success)' }} />
                          <div style={{ flex: 1, background: '#3498db' }} />
                        </div>
                        <span style={{ fontSize: 'var(--fs-3xs)', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                          {String(pt.record_date).slice(-5)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* User Consumption Share Bars */}
              <div>
                <h4 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <Users size={16} style={{ color: 'var(--color-primary)' }} />
                  {t('distribution_title')}
                </h4>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {users.map(u => (
                    <div key={u.user_id} style={{
                      padding: '10px 14px',
                      background: 'var(--bg-secondary)',
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border-color)'
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span style={{ fontWeight: 700, fontSize: 'var(--fs-md)' }}>{u.user_name}</span>
                          <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>({u.device_count} {t('devs_short')})</span>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                          <span className="font-mono" style={{ fontSize: 'var(--fs-sm)', fontWeight: 700 }}>
                            {formatBytes(u.total_bytes)}
                          </span>
                          <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, color: 'var(--color-primary)', minWidth: 40, textAlign: 'right' }}>
                            {u.pct_of_total}%
                          </span>
                        </div>
                      </div>
                      <div style={{ height: 6, background: 'var(--bg-input)', borderRadius: 'var(--radius-xs)', overflow: 'hidden' }}>
                        <div style={{
                          width: `${Math.min(u.pct_of_total, 100)}%`,
                          height: '100%',
                          background: 'var(--color-primary)',
                          borderRadius: 'var(--radius-xs)',
                          transition: 'width 0.4s ease'
                        }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: BY USERS */}
          {breakdownTab === 'users' && (
            <div style={{ overflowX: 'auto' }}>
              <table className="table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border-color)', textAlign: 'left', fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
                    <SortHeader label={t('table_user')} field="user_name" sort={userSort} onSort={toggleUserSort} />
                    {/* device count for this user - not the "Unassigned Devices" tab label */}
                    <SortHeader label={t('table_devices')} field="device_count" sort={userSort} onSort={toggleUserSort} />
                    <SortHeader label={`${t('total_download')} (RX)`} field="bytes_in" sort={userSort} onSort={toggleUserSort} />
                    <SortHeader label={`${t('total_upload')} (TX)`} field="bytes_out" sort={userSort} onSort={toggleUserSort} />
                    <SortHeader label={t('total_combined')} field="total_bytes" sort={userSort} onSort={toggleUserSort} />
                    <SortHeader label={t('share_of_traffic')} field="pct_of_total" sort={userSort} onSort={toggleUserSort} />
                  </tr>
                </thead>
                <tbody>
                  {sortRows(users, userSort).map(u => (
                    <tr key={u.user_id} style={{ borderBottom: '1px solid var(--border-color)', fontSize: 'var(--fs-sm)' }}>
                      <td style={{ padding: '10px 12px', fontWeight: 700 }}>{u.user_name}</td>
                      <td style={{ padding: '10px 12px', color: 'var(--text-muted)' }}>{u.device_count}</td>
                      <td style={{ padding: '10px 12px', color: 'var(--color-success)', fontWeight: 600 }} className="font-mono">
                        {formatBytes(u.bytes_in)}
                      </td>
                      <td style={{ padding: '10px 12px', color: '#3498db', fontWeight: 600 }} className="font-mono">
                        {formatBytes(u.bytes_out)}
                      </td>
                      <td style={{ padding: '10px 12px', fontWeight: 800 }} className="font-mono">
                        {formatBytes(u.total_bytes)}
                      </td>
                      <td style={{ padding: '10px 12px', minWidth: 130 }}>
                        <ShareBar pct={u.pct_of_total} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* TAB 3: BY DEVICES */}
          {breakdownTab === 'devices' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {/* Search & User Filters */}
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <div style={{ flex: 1, minWidth: 220, position: 'relative' }}>
                  <Search size={14} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
                  <input
                    type="text"
                    className="form-input"
                    placeholder={t('search_devices_placeholder')}
                    value={searchTerm}
                    onChange={e => setSearchTerm(e.target.value)}
                    style={{ paddingLeft: 32, paddingTop: 6, paddingBottom: 6, fontSize: 'var(--fs-sm)' }}
                  />
                </div>

                <select
                  className="form-select"
                  value={userFilter}
                  onChange={e => setUserFilter(e.target.value)}
                  style={{ width: 180, paddingTop: 6, paddingBottom: 6, fontSize: 'var(--fs-sm)' }}
                >
                  <option value="all">{t('all_users_filter')}</option>
                  <option value="unassigned">{t('unassigned_traffic')}</option>
                  {users.map(u => (
                    <option key={u.user_id} value={u.user_id}>{u.user_name}</option>
                  ))}
                </select>
              </div>

              {/* Devices Table */}
              <div style={{ overflowX: 'auto' }}>
                <table className="table" style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border-color)', textAlign: 'left', fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
                      <SortHeader label={t('table_device')} field="custom_name" sort={deviceSort} onSort={toggleDeviceSort} />
                      <SortHeader label={t('table_ip_mac')} field="ip_address" sort={deviceSort} onSort={toggleDeviceSort} />
                      <SortHeader label={t('table_user')} field="user_name" sort={deviceSort} onSort={toggleDeviceSort} />
                      <SortHeader label={t('download_rx')} field="bytes_in" sort={deviceSort} onSort={toggleDeviceSort} />
                      <SortHeader label={t('upload_tx')} field="bytes_out" sort={deviceSort} onSort={toggleDeviceSort} />
                      <SortHeader label={t('table_total')} field="total_bytes" sort={deviceSort} onSort={toggleDeviceSort} />
                      <SortHeader label={t('table_share')} field="pct_of_total" sort={deviceSort} onSort={toggleDeviceSort} />
                      <SortHeader label={t('table_speed_limit')} field="speed_limit" sort={deviceSort} onSort={toggleDeviceSort} />
                    </tr>
                  </thead>
                  <tbody>
                    {filteredDevices.length === 0 ? (
                      <tr>
                        <td colSpan={8} style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>
                          {t('no_devices_matching')}
                        </td>
                      </tr>
                    ) : (
                      sortRows(filteredDevices, deviceSort).map(d => (
                        <tr key={d.device_id} style={{ borderBottom: '1px solid var(--border-color)', fontSize: 'var(--fs-sm)' }}>
                          <td style={{ padding: '10px 12px' }}>
                            <div style={{ fontWeight: 700 }}>{d.custom_name || d.hostname || `${t('table_device')} ${d.device_id}`}</div>
                            <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>{d.vendor || t('unknown_vendor')}</div>
                          </td>
                          <td style={{ padding: '10px 12px', fontSize: 'var(--fs-xs)' }} className="font-mono">
                            <div>{d.ip_address || '—'}</div>
                            <div style={{ color: 'var(--text-muted)' }}>{d.mac_address}</div>
                          </td>
                          <td style={{ padding: '10px 12px' }}>
                            {d.user_name ? (
                              <span style={{ padding: '2px 6px', borderRadius: 'var(--radius-xs)', background: 'var(--bg-secondary)', fontSize: 'var(--fs-xs)', fontWeight: 600 }}>
                                {d.user_name}
                              </span>
                            ) : (
                              <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>{t('unassigned_label')}</span>
                            )}
                          </td>
                          <td style={{ padding: '10px 12px', color: 'var(--color-success)', fontWeight: 600 }} className="font-mono">
                            {formatBytes(d.bytes_in)}
                          </td>
                          <td style={{ padding: '10px 12px', color: '#3498db', fontWeight: 600 }} className="font-mono">
                            {formatBytes(d.bytes_out)}
                          </td>
                          <td style={{ padding: '10px 12px', fontWeight: 800 }} className="font-mono">
                            {formatBytes(d.total_bytes)}
                          </td>
                          <td style={{ padding: '10px 12px', minWidth: 130 }}>
                            <ShareBar pct={d.pct_of_total} />
                          </td>
                          <td style={{ padding: '10px 12px' }}>
                            <span style={{ fontSize: 'var(--fs-xs)', color: d.speed_limit !== 'default' ? 'var(--color-warning)' : 'var(--text-muted)' }}>
                              {d.speed_limit === 'default' ? t('inherit_user') : d.speed_limit}
                            </span>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Billing Cycle Setting Modal */}
      {billingModalOpen && (
        <div className="modal-backdrop" onClick={() => setBillingModalOpen(false)}>
          <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 440 }}>
            <div className="modal-header">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700 }}>
                <Calendar size={18} style={{ color: 'var(--color-primary)' }} />
                {t('billing_cycle')}
              </div>
              <button className="btn-icon" onClick={() => setBillingModalOpen(false)} style={{ width: 28, height: 28 }}>
                <X size={16} />
              </button>
            </div>

            <div className="modal-body">
              <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', marginBottom: 14 }}>
                {t('billing_anchor_desc')}
              </p>

              <div className="form-group">
                <label className="form-label">{t('billing_anchor_day')} (1 - 31)</label>
                <input
                  type="number"
                  min="1"
                  max="31"
                  className="form-input font-mono"
                  value={anchorDay}
                  onChange={e => setAnchorDay(Math.max(1, minVal(Number(e.target.value), 31)))}
                  style={{ height: 38, fontSize: 'var(--fs-lg)' }}
                />
              </div>
            </div>

            <div className="modal-footer">
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setBillingModalOpen(false)}>
                {t('cancel')}
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleSaveBillingCycle}
                disabled={isSavingBilling}
                style={{ display: 'flex', alignItems: 'center', gap: 6 }}
              >
                {saveSuccess ? <Check size={14} /> : null}
                {t('save')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function minVal(v, max) {
  return isNaN(v) ? 1 : Math.min(v, max);
}

function roundPct(val) {
  return isNaN(val) ? '0' : val.toFixed(1);
}
