import React, { useEffect, useState, useCallback } from 'react';
import { api } from '../api/client';
import { useI18n } from '../context/I18nContext';
import { formatBytes } from '../utils/formatters';
import { Gauge, ExternalLink, Check, AlertTriangle } from 'lucide-react';

/**
 * A slim band under the header tiles, on every page, showing consumption against
 * the ISP billing-cycle allowance - but only when an allowance is actually set.
 *
 * It carries three things the operator wants at a glance without opening the
 * analytics tab:
 *   - used / limit and how many days are left in the cycle;
 *   - whether the current rate of spending lands inside the limit ("on track"),
 *     from a conservative cycle-so-far projection;
 *   - the same projection as a percentage, plus an "at current pace" figure that
 *     reacts faster to a recent binge.
 *
 * Clicking anywhere but the portal button opens Settings, where the limit and
 * the portal link are configured. If a portal URL is set, its button links
 * straight to the ISP's own usage page (or the modem's).
 */
export function QuotaStrip({ activeRouterId, onOpenSettings }) {
  const { t } = useI18n();
  const [q, setQ] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await api.getQuota(activeRouterId || null);
      setQ(res?.data || null);
    } catch {
      // A quota read failing is not worth a visible error on every page.
      setQ(null);
    }
  }, [activeRouterId]);

  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [load]);

  if (!q || !q.enabled) return null;

  const usedPct = Math.min(100, Math.max(0, q.used_pct || 0));
  const projPct = q.projected_pct_linear || 0;
  const pacePct = q.projected_pct_at_pace || 0;
  const onTrack = q.on_track;

  // What the "at pace" number currently rests on, for its tooltip.
  const paceTitle = {
    blended: t('quota_pace_blended'),
    recent: t('quota_pace_recent'),
    sparse: t('quota_pace_sparse'),
  }[q.pace_basis] || t('quota_pace_recent');

  // The bar is coloured by where the projection lands, not by today's usage:
  // 40% used but heading for 130% is the situation worth flagging early.
  const accent = !onTrack
    ? 'var(--color-danger)'
    : projPct >= 85
      ? 'var(--color-warning)'
      : 'var(--color-success)';

  // Everything the retired analytics panel showed, folded into the hover:
  // cycle window, what is left, the daily budget to stay inside it, and last
  // cycle's average as the yardstick.
  const fullTitle = [
    t('quota_strip_hint'),
    q.cycle_start && q.cycle_end ? `${q.cycle_start} → ${q.cycle_end}` : null,
    `${t('quota_remaining')}: ${formatBytes(q.remaining_bytes)} · ${t('quota_daily_budget')}: ${formatBytes(q.projected_daily_budget)}${t('quota_per_day')}`,
    q.prev_cycle_bytes_per_day > 0
      ? `${t('quota_last_cycle_avg')}: ${formatBytes(q.prev_cycle_bytes_per_day)}${t('quota_per_day')}`
      : null,
  ].filter(Boolean).join('\n');

  return (
    <div
      className="quota-strip"
      onClick={onOpenSettings}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter') onOpenSettings(); }}
      title={fullTitle}
    >
      <Gauge size={14} className="quota-strip-icon" style={{ color: accent }} />

      {/* Progress bar expanded to ~50% width with threshold percentage notches and labels */}
      <div className="quota-strip-bar-container">
        <div className="quota-strip-bar" aria-hidden="true">
          <div className="quota-strip-bar-fill" style={{ width: `${usedPct}%`, background: accent }} />
          {((q.thresholds && q.thresholds.length > 0) ? q.thresholds : [50, 80, 100])
            .filter((tPct) => tPct < 100)
            .map((tPct) => {
              const isReached = usedPct >= tPct;
              return (
                <div
                  key={tPct}
                  className={`quota-strip-notch ${isReached ? 'is-reached' : ''}`}
                  style={{ left: `${Math.min(100, Math.max(0, tPct))}%` }}
                  title={`${tPct}% alert threshold`}
                />
              );
            })}
        </div>
        <div className="quota-strip-notches-labels" aria-hidden="true">
          {((q.thresholds && q.thresholds.length > 0) ? q.thresholds : [50, 80, 100])
            .filter((tPct) => tPct < 100)
            .map((tPct) => {
              const isReached = usedPct >= tPct;
              return (
                <span
                  key={tPct}
                  className={`quota-strip-notch-label ${isReached ? 'is-reached' : ''}`}
                  style={{ left: `${Math.min(100, Math.max(0, tPct))}%` }}
                >
                  {tPct}%
                </span>
              );
            })}
        </div>
      </div>

      <span className="quota-strip-main font-mono">
        {formatBytes(q.used_bytes)} / {formatBytes(q.limit_bytes)}
        <span className="quota-strip-sep">·</span>
        {(() => {
          // A non-midnight reset carries a precise instant; show days + hours.
          if (q.cycle_end_at) {
            // cycle_end_at is a naive router-local instant, parsed here in the
            // browser's own zone. That is correct when the two match - the
            // LAN-admin common case. Do not "fix" this by appending 'Z': that
            // would wrongly assume the router runs on UTC.
            const ms = new Date(q.cycle_end_at).getTime() - Date.now();
            if (ms > 0) {
              const totalHours = Math.floor(ms / 3_600_000);
              const d = Math.floor(totalHours / 24);
              const h = totalHours % 24;
              if (h !== 0 || d === 0) {
                return t('quota_time_left', { d, h });
              }
            }
          }
          return t('quota_days_left', { days: q.days_remaining });
        })()}
      </span>

      <span className="quota-strip-forecast">
        <span className={`quota-strip-pill ${onTrack ? 'is-ok' : 'is-over'}`}>
          {onTrack ? <Check size={11} /> : <AlertTriangle size={11} />}
          {onTrack ? t('quota_on_track') : t('quota_over')}
        </span>
        <span className="font-mono quota-strip-proj" style={{ color: accent }}>
          {t('quota_projected')} {projPct}%
        </span>
        <span className="font-mono quota-strip-pace" title={paceTitle}>
          {t('quota_at_pace')} {q.pace_basis === 'sparse' ? '~' : ''}{pacePct}%
        </span>
      </span>

      {q.portal_url && (
        <a
          className="quota-strip-portal"
          href={q.portal_url}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
        >
          <ExternalLink size={12} />
          {q.portal_label || t('quota_portal_default')}
        </a>
      )}
    </div>
  );
}
