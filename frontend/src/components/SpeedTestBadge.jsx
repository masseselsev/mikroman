import React, { useCallback, useEffect, useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { Gauge, Loader2, AlertCircle } from 'lucide-react';

/**
 * Run a WAN speed test from the router, and show the last result.
 *
 * Lives on the WAN IP tile because that is where the question is asked: the
 * address, the ISP and the line's actual speed are three facts about the same
 * link. The test runs in a container *on the router*, so it measures the ISP
 * link rather than the path from the router to whatever machine is displaying
 * this - a test from a laptop over Wi-Fi reports the Wi-Fi, and blames the ISP.
 *
 * A run takes up to a couple of minutes and the request blocks for its
 * duration, so the button stays busy rather than polling a job id.
 */
export function SpeedTestBadge({ routerId }) {
  const { t } = useI18n();
  const [status, setStatus] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    if (!routerId) return;
    try {
      const res = await api.getSpeedTestStatus(routerId);
      setStatus(res.data || null);
    } catch {
      // Unreachable router, or a RouterOS without the container package. The
      // tile is still useful without this, so failure is simply silence.
      setStatus(null);
    }
  }, [routerId]);

  useEffect(() => { load(); }, [load]);

  const run = async (e) => {
    e.stopPropagation();
    if (running || !status?.can_run) return;
    setRunning(true);
    setError(null);
    try {
      const res = await api.runSpeedTest(routerId);
      const result = res.data?.result;
      if (result && result.status !== 'ok') {
        setError(result.error || t('speedtest_failed'));
      }
      await load();
    } catch (err) {
      setError(err.message || t('speedtest_failed'));
    } finally {
      setRunning(false);
    }
  };

  if (!routerId || !status) return null;

  const last = status.last_result;
  const hasFigures = last && last.download_mbps != null;

  // Not runnable: say why in one line rather than showing a button that fails.
  if (!status.can_run) {
    const reasonKey = {
      no_container: 'speedtest_no_container',
      package_missing: 'speedtest_no_package',
      unreachable: 'speedtest_unreachable',
    }[status.reason] || 'speedtest_no_container';
    return (
      <span className="speedtest-note" title={t(reasonKey)}>
        {hasFigures ? (
          <span className="font-mono">
            ↓{Math.round(last.download_mbps)} ↑{Math.round(last.upload_mbps ?? 0)}
          </span>
        ) : null}
        <AlertCircle size={11} />
        {t('speedtest_unavailable')}
      </span>
    );
  }

  return (
    <span className="speedtest-row">
      {hasFigures && (
        <span
          className="font-mono speedtest-figures"
          title={[
            `${t('speedtest_download')}: ${last.download_mbps} Mbps`,
            `${t('speedtest_upload')}: ${last.upload_mbps ?? '—'} Mbps`,
            last.ping_ms != null ? `${t('speedtest_ping')}: ${last.ping_ms} ms` : null,
            last.jitter_ms != null ? `${t('speedtest_jitter')}: ${last.jitter_ms} ms` : null,
            last.server_name || null,
            last.created_at ? new Date(last.created_at).toLocaleString() : null,
          ].filter(Boolean).join('\n')}
        >
          ↓{Math.round(last.download_mbps)}
          <span className="speedtest-sep">/</span>
          ↑{Math.round(last.upload_mbps ?? 0)}
          <span className="speedtest-unit"> Mbps</span>
        </span>
      )}
      <button
        type="button"
        className="speedtest-btn"
        onClick={run}
        disabled={running}
        title={running ? t('speedtest_running_hint') : t('speedtest_run_hint')}
      >
        {running ? <Loader2 size={11} className="spin" /> : <Gauge size={11} />}
        {running ? t('speedtest_running') : t('speedtest_run')}
      </button>
      {error && (
        <span className="speedtest-error" title={error}>
          <AlertCircle size={11} />
        </span>
      )}
    </span>
  );
}
