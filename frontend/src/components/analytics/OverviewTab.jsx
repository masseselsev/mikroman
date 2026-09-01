import React from 'react';
import { useI18n } from '../../context/I18nContext';
import { formatBytes } from '../../utils/formatters';
import { DonutChart, DONUT_PALETTE, DONUT_OTHER_COLOR } from '../DonutChart';
import {
  Calendar,
  Server,
  Smartphone,
  Users,
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
 * Breakdown tab 1: who used the range, and when.
 *
 * Two donuts answering "who" - by profile and by device - over a daily timeline
 * answering "when", with the router's own volume called out above them because
 * it belongs to the same total but to nobody in the household. Reads only from
 * the analytics response; the sorting and filtering state belongs to the
 * sibling tabs, so this one takes no callbacks.
 */
export function OverviewTab({
  gateway,
  timeline,
  users,
  devices,
  routerSelf,
  unassigned,
  unaccountedBytes = 0,
  overAccountedBytes = 0,
}) {
  const { t } = useI18n();

  // Biggest few by volume, the rest folded into one "Other" slice.
  const deviceSegments = toDonutSegments(
    devices, d => d.custom_name || d.hostname || d.mac_address, t('donut_other')
  );

  // The "by user" donut is drawn against the gateway total, so it has to
  // account for every byte of it — otherwise the ring visibly falls short of
  // the figure printed in its own centre and the whole page looks like it is
  // losing traffic. Three things are part of the total but belong to no
  // profile, and each gets its own slice:
  //   • devices nobody has claimed yet,
  //   • what the router moved for itself (DNS, NTP, updates, containers),
  //   • whatever the WAN measured that no counter could attribute.
  const remainderSegments = [
    { label: t('donut_unassigned'), value: unassigned?.total_bytes || 0, color: '#f59e0b' },
    { label: t('router_self_traffic'), value: routerSelf?.total_bytes || 0, color: '#a855f7' },
    { label: t('donut_unaccounted'), value: unaccountedBytes || 0, color: DONUT_OTHER_COLOR },
  ].filter(s => s.value > 0);

  const userSegments = [
    ...toDonutSegments(users, u => u.user_name, t('donut_other')),
    ...remainderSegments,
  ];

  // Bar scale for the daily timeline. Floored at 1 MB so a near-idle day does
  // not render as a full-height bar out of a rounding artefact.
  const maxDailyBytes = Math.max(...timeline.map(p => p.total_bytes), 1024 * 1024);

  return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        {/* What the router moved for itself. Shown only when it was actually
            measured, so an install without the input/output rules yet does not
            get a permanent zero implying the router uses nothing. */}
        {routerSelf?.total_bytes > 0 && (
          <div className="router-self-row" title={t('router_self_hint')}>
            <Server size={14} />
            <span className="router-self-label">{t('router_self_traffic')}</span>
            <span className="font-mono router-self-value">{formatBytes(routerSelf.total_bytes)}</span>
            <span className="router-self-pct">{routerSelf.pct_of_total}%</span>
            <span className="router-self-note">{t('router_self_hint')}</span>
          </div>
        )}

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
            {/* Per-device counters match the forward chain by address with no
                WAN constraint, so traffic between two local subnets is counted
                for both ends without ever crossing the gateway. When that
                happens the attributed sum exceeds the WAN total and the ring
                cannot be drawn to scale — say so rather than silently
                rescaling and implying the total is something it is not. */}
            {overAccountedBytes > 0 && (
              <div style={{ marginTop: 8, fontSize: 'var(--fs-2xs)', color: 'var(--color-warning)' }}>
                {t('donut_over_accounted', { bytes: formatBytes(overAccountedBytes) })}
              </div>
            )}
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
  );
}
