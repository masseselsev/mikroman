import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { formatBytes } from '../utils/formatters';
import { StatTile } from './StatTile';
import { OverviewTab } from './analytics/OverviewTab';
import { UsersTab } from './analytics/UsersTab';
import { DevicesTab } from './analytics/DevicesTab';
import {
  Calendar,
  Clock,
  ArrowDown,
  ArrowUp,
  BarChart2,
  Users,
  Smartphone,
  Layers,
  X,
  Check,
  Activity,
  AlertTriangle
} from 'lucide-react';

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

    // Parked infrastructure stays out unless asked for, as everywhere else.
    const matchHidden = showHidden || !d.is_hidden;

    return matchSearch && matchUser && matchHidden;
  });

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
            {/* The full arithmetic, spelled out. Two things depend on this.
                One: a reader has no way to tell "half the traffic went missing"
                from "half this range is older than the feature" without seeing
                both volumes. Two: the percentage describes a *sub-window* of the
                range, so its attributed figure is necessarily smaller than the
                user and device tables further down — and unless the split is
                shown adding back up to that total, the difference reads as a
                counting bug. measured + earlier === the breakdown's total. */}
            {health.pre_accounting_bytes > 0 && (
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginTop: 4, lineHeight: 1.5 }}>
                <div>
                  {t('acct_measured_from', { date: dayAfter(health.accounting_started) })}:{' '}
                  <span className="font-mono">{formatBytes(health.measured_accounted_bytes)}</span>
                  {' / '}
                  <span className="font-mono">{formatBytes(health.measured_bytes)}</span>
                </div>
                <div>
                  {t('acct_before_start')}:{' '}
                  <span className="font-mono">{formatBytes(health.pre_accounting_accounted_bytes)}</span>
                  {' / '}
                  <span className="font-mono">{formatBytes(health.pre_accounting_bytes)}</span>
                </div>
                <div>
                  {t('acct_range_total')}:{' '}
                  <span className="font-mono">{formatBytes(health.accounted_bytes)}</span>
                  {' / '}
                  <span className="font-mono">{formatBytes(health.gateway_bytes)}</span>
                  {' — '}{t('acct_matches_tables')}
                </div>
              </div>
            )}
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

      {/* Range totals. Deliberately compact: these are a reference strip, not
          the content of the page, and at full card size they pushed the actual
          breakdown below the fold on a laptop. */}
      <div className="stat-strip">
        <StatTile
          icon={<Layers size={16} />}
          tone="var(--color-primary)"
          tint="rgba(11, 114, 201, 0.15)"
          label={t("total_combined")}
          value={formatBytes(gateway.total_bytes)}
          sub={gateway.monitored_interfaces.length > 0 ? `${t("interfaces_label")}: ${gateway.monitored_interfaces.join(", ")}` : t("all_interfaces")}
        />

        <StatTile
          icon={<ArrowDown size={16} />}
          tone="var(--color-success)"
          tint="rgba(46, 204, 113, 0.15)"
          label={t("total_download")}
          value={formatBytes(gateway.total_bytes_in)}
          valueColor="var(--color-success)"
          sub={gateway.total_bytes > 0 ? `${roundPct(gateway.total_bytes_in / gateway.total_bytes * 100)}% ${t("of_total")}` : "0%"}
        />

        <StatTile
          icon={<ArrowUp size={16} />}
          tone="#3498db"
          tint="rgba(52, 152, 219, 0.15)"
          label={t("total_upload")}
          value={formatBytes(gateway.total_bytes_out)}
          valueColor="#3498db"
          sub={gateway.total_bytes > 0 ? `${roundPct(gateway.total_bytes_out / gateway.total_bytes * 100)}% ${t("of_total")}` : "0%"}
        />

        <StatTile
          icon={<Users size={16} />}
          tone="#9b59b6"
          tint="rgba(155, 89, 182, 0.15)"
          label={t("active_profiles_devices")}
          value={<>{users.length}<span className="stat-tile-unit">{t("users_short")}</span>{" · "}{devices.length}<span className="stat-tile-unit">{t("devs_short")}</span></>}
          sub={t("shaped_via_ros")}
        />
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
            <OverviewTab
              gateway={gateway}
              timeline={timeline}
              users={users}
              devices={devices}
            />
          )}

          {/* TAB 2: BY USERS */}
          {breakdownTab === 'users' && (
            <UsersTab users={users} userSort={userSort} toggleUserSort={toggleUserSort} />
          )}

          {/* TAB 3: BY DEVICES */}
          {breakdownTab === 'devices' && (
            <DevicesTab
              users={users}
              filteredDevices={filteredDevices}
              deviceSort={deviceSort}
              toggleDeviceSort={toggleDeviceSort}
              searchTerm={searchTerm}
              setSearchTerm={setSearchTerm}
              userFilter={userFilter}
              setUserFilter={setUserFilter}
              showHidden={showHidden}
              setShowHidden={setShowHidden}
            />
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

/**
 * The first fully-measured day, given the day accounting was switched on.
 *
 * The switch-on day itself is partial — gateway counters ran from midnight,
 * device counters only from the moment the rules went up — so coverage is
 * measured from the day after it. Labelling the banner with the switch-on date
 * would point at a day the figure deliberately excludes.
 */
function dayAfter(isoDate) {
  if (!isoDate) return '';
  const d = new Date(`${isoDate}T00:00:00Z`);
  if (isNaN(d.getTime())) return isoDate;
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}
