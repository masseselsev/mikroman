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

  return (
    <div
      className="quota-strip"
      onClick={onOpenSettings}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter') onOpenSettings(); }}
      title={t('quota_strip_hint')}
    >
      <Gauge size={14} className="quota-strip-icon" style={{ color: accent }} />

      <div className="quota-strip-bar" aria-hidden="true">
        <div className="quota-strip-bar-fill" style={{ width: `${usedPct}%`, background: accent }} />
      </div>

      <span className="quota-strip-main font-mono">
        {formatBytes(q.used_bytes)} / {formatBytes(q.limit_bytes)}
        <span className="quota-strip-sep">·</span>
        {t('quota_days_left', { n: q.days_remaining })}
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
