import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import {
  X,
  Terminal,
  RefreshCw,
  Pause,
  Play,
  Search,
  Copy,
  Download,
  Settings2,
  Trash2,
  Plus,
  ArrowDownToLine,
} from 'lucide-react';

/**
 * Live RouterOS log viewer.
 *
 * Two sources, deliberately distinct:
 *  - "live" reads `/log` off the router. RouterOS keeps that in a small memory
 *    ring, so it is the last few hundred lines and nothing older.
 *  - "db" reads what the background scraper has copied into SQLite, which is
 *    the only place yesterday's lines still exist.
 *
 * Auto-scroll sticks to the bottom while the reader is at the bottom and
 * freezes the moment they scroll up, so a burst of DHCP chatter cannot yank a
 * line out from under them mid-read.
 */

// `icon` is what marks a line in the terminal gutter; the pill label comes from
// the translation, which already carries its own emoji.
const CATEGORIES = [
  { id: 'all', icon: '', labelKey: 'cat_all' },
  { id: 'auth', icon: '🚨', labelKey: 'cat_auth' },
  { id: 'interface', icon: '🔌', labelKey: 'cat_interface' },
  { id: 'dhcp', icon: '⚡', labelKey: 'cat_dhcp' },
  { id: 'wireless', icon: '📶', labelKey: 'cat_wireless' },
  { id: 'firewall', icon: '🛡️', labelKey: 'cat_firewall' },
  { id: 'system', icon: '⚙️', labelKey: 'cat_errors' },
];

// One click each for the topics RouterOS does not record unless asked.
const TOPIC_PRESETS = ['wireless', 'firewall', 'wireguard', 'dns', 'script', 'dhcp'];

const SEVERITY_COLORS = {
  critical: '#f43f5e',
  error: '#ef4444',
  warning: '#f59e0b',
  info: 'var(--text-secondary)',
};

const LIVE_POLL_MS = 2500;

function severityColor(sev) {
  return SEVERITY_COLORS[sev] || SEVERITY_COLORS.info;
}

function formatStamp(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function RouterLogsModal({ isOpen, onClose, routerId = null, routerName = '' }) {
  const { t } = useI18n();

  const [entries, setEntries] = useState([]);
  const [stats, setStats] = useState(null);
  const [source, setSource] = useState('live');
  const [category, setCategory] = useState('all');
  const [search, setSearch] = useState('');
  // A per-viewer convenience (which router's noise to hide is not something
  // worth syncing across devices), so it lives in localStorage rather than
  // an app setting.
  const [hideSelfApi, setHideSelfApi] = useState(() => {
    try {
      return localStorage.getItem('mikroman:logs-hide-self-api') === 'true';
    } catch {
      return false;
    }
  });
  const [isStreaming, setIsStreaming] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [rulesOpen, setRulesOpen] = useState(false);
  const [rules, setRules] = useState([]);
  const [rulesBusy, setRulesBusy] = useState(false);
  const [customTopic, setCustomTopic] = useState('');

  const viewportRef = useRef(null);
  const stickToBottomRef = useRef(true);
  const timerRef = useRef(null);

  const fetchLogs = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const params = { source, limit: 500 };
      if (routerId) params.router_id = routerId;
      if (category !== 'all') params.category = category;
      if (search.trim()) params.search = search.trim();
      if (hideSelfApi) params.hide_self_api = true;
      const res = await api.getLogs(params);
      setEntries(res?.data || []);
      setError(res?.data?.length === 0 && res?.message ? res.message : null);
    } catch (err) {
      setError(err.message || 'Failed to load logs');
    } finally {
      if (showLoading) setLoading(false);
    }
  }, [source, category, search, routerId, hideSelfApi]);

  const toggleHideSelfApi = () => {
    setHideSelfApi(prev => {
      const next = !prev;
      try {
        localStorage.setItem('mikroman:logs-hide-self-api', String(next));
      } catch {
        // Best-effort only - a private-browsing tab losing the preference
        // on close is not worth surfacing an error over.
      }
      return next;
    });
  };

  const fetchStats = useCallback(async () => {
    try {
      const res = await api.getLogStats(routerId ? { router_id: routerId } : {});
      setStats(res?.data || null);
    } catch {
      setStats(null);
    }
  }, [routerId]);

  useEffect(() => {
    if (!isOpen) {
      setEntries([]);
      setRulesOpen(false);
      return;
    }
    fetchLogs(true);
    fetchStats();
  }, [isOpen, fetchLogs, fetchStats]);

  // Live polling. Stored history does not change under the reader, so it only
  // refreshes on demand.
  useEffect(() => {
    if (!isOpen || !isStreaming || source !== 'live') {
      if (timerRef.current) clearInterval(timerRef.current);
      return undefined;
    }
    timerRef.current = setInterval(() => fetchLogs(false), LIVE_POLL_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isOpen, isStreaming, source, fetchLogs]);

  // Auto-scroll, but only while the reader has not scrolled away from the tail.
  useEffect(() => {
    const el = viewportRef.current;
    if (el && stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [entries]);

  const handleScroll = (e) => {
    const el = e.currentTarget;
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };

  const jumpToBottom = () => {
    const el = viewportRef.current;
    if (el) {
      stickToBottomRef.current = true;
      el.scrollTop = el.scrollHeight;
    }
  };

  const asText = useMemo(
    () => entries.map(e => `${formatStamp(e.timestamp)} [${e.topics}] ${e.message}`).join('\n'),
    [entries]
  );

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(asText);
    } catch {
      setError(t('log_copy_failed'));
    }
  };

  const handleExport = () => {
    const blob = new Blob([asText], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `mikroman-${(routerName || 'router').replace(/\s+/g, '-').toLowerCase()}-logs.log`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const loadRules = useCallback(async () => {
    setRulesBusy(true);
    try {
      const res = await api.getLoggingRules(routerId);
      setRules(res?.data || []);
    } catch (err) {
      setError(err.message || 'Failed to load logging rules');
    } finally {
      setRulesBusy(false);
    }
  }, [routerId]);

  const toggleRules = async () => {
    const next = !rulesOpen;
    setRulesOpen(next);
    if (next) await loadRules();
  };

  const addRule = async (topics) => {
    const clean = String(topics || '').trim();
    if (!clean) return;
    setRulesBusy(true);
    setError(null);
    try {
      await api.createLoggingRule({ topics: clean, action: 'memory' }, routerId);
      setCustomTopic('');
      await loadRules();
    } catch (err) {
      setError(err.message || 'Could not add logging rule');
    } finally {
      setRulesBusy(false);
    }
  };

  const removeRule = async (rule) => {
    setRulesBusy(true);
    setError(null);
    try {
      await api.deleteLoggingRule(rule.id, routerId);
      await loadRules();
    } catch (err) {
      setError(err.message || 'Could not remove logging rule');
    } finally {
      setRulesBusy(false);
    }
  };

  if (!isOpen) return null;

  const activeTopics = new Set(rules.map(r => (r.topics || '').trim()));

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-card"
        onClick={e => e.stopPropagation()}
        style={{ maxWidth: 1100, width: '95vw', display: 'flex', flexDirection: 'column' }}
      >
        <div className="modal-header">
          <div className="modal-title-group">
            <div className="modal-icon"><Terminal size={20} /></div>
            <div style={{ minWidth: 0 }}>
              <h3>{t('router_logs_title')}</h3>
              <div className="modal-subtitle truncate">
                {routerName || t('app_subtitle')}
                {stats ? ` · ${t('log_stats_summary', {
                  errors: stats.error_count + stats.critical_count,
                  warnings: stats.warning_count,
                  auth: stats.auth_failures_count,
                })}` : ''}
              </div>
            </div>
          </div>
          <button className="btn-icon" onClick={onClose} aria-label={t('log_close')}>
            <X size={16} />
          </button>
        </div>

        <div className="modal-body" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {error && <div className="alert alert-danger">{error}</div>}

          {/* Source switch, stream controls and tools */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <div style={{
              display: 'flex', background: 'var(--bg-secondary)', padding: 3,
              borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-color)', gap: 2,
            }}>
              {[
                { id: 'live', label: t('source_live') },
                { id: 'db', label: t('source_db') },
              ].map(s => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setSource(s.id)}
                  style={{
                    padding: '4px 12px',
                    fontSize: 'var(--fs-xs)',
                    fontWeight: source === s.id ? 700 : 500,
                    borderRadius: 'var(--radius-xs)',
                    border: 'none',
                    background: source === s.id ? 'var(--color-primary)' : 'transparent',
                    color: source === s.id ? '#fff' : 'var(--text-secondary)',
                    cursor: 'pointer',
                  }}
                >
                  {s.label}
                </button>
              ))}
            </div>

            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setIsStreaming(v => !v)}
              disabled={source !== 'live'}
              title={isStreaming ? t('pause_btn') : t('resume_btn')}
              style={{ display: 'flex', alignItems: 'center', gap: 5 }}
            >
              {isStreaming ? <Pause size={13} /> : <Play size={13} />}
              <span>{isStreaming ? t('pause_btn') : t('resume_btn')}</span>
            </button>

            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => fetchLogs(true)}
              title={t('log_refresh')}
              style={{ display: 'flex', alignItems: 'center', gap: 5 }}
            >
              <RefreshCw size={13} className={loading ? 'spin' : ''} />
              <span>{t('log_refresh')}</span>
            </button>

            <div style={{ position: 'relative', flex: 1, minWidth: 160 }}>
              <Search
                size={13}
                style={{
                  position: 'absolute', left: 8, top: '50%',
                  transform: 'translateY(-50%)', color: 'var(--text-muted)',
                }}
              />
              <input
                type="text"
                className="form-input"
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder={t('log_search_ph')}
                style={{ paddingLeft: 26, height: 30, fontSize: 'var(--fs-sm)', width: '100%' }}
              />
            </div>

            <button type="button" className="btn btn-secondary btn-sm" onClick={handleCopy}
              title={t('copy_logs')} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <Copy size={13} /><span className="hide-mobile">{t('copy_logs')}</span>
            </button>
            <button type="button" className="btn btn-secondary btn-sm" onClick={handleExport}
              title={t('export_logs')} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <Download size={13} /><span className="hide-mobile">{t('export_logs')}</span>
            </button>
            <button type="button" className="btn btn-secondary btn-sm" onClick={toggleRules}
              title={t('manage_logging_rules')} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <Settings2 size={13} /><span className="hide-mobile">{t('manage_logging_rules')}</span>
            </button>
          </div>

          {/* Category pills, plus the self-login declutter toggle */}
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
              {CATEGORIES.map(c => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => setCategory(c.id)}
                  style={{
                    padding: '3px 10px',
                    fontSize: 'var(--fs-2xs)',
                    fontWeight: category === c.id ? 700 : 500,
                    borderRadius: 999,
                    border: `1px solid ${category === c.id ? 'var(--color-primary)' : 'var(--border-color)'}`,
                    background: category === c.id ? 'var(--color-primary)' : 'transparent',
                    color: category === c.id ? '#fff' : 'var(--text-secondary)',
                    cursor: 'pointer',
                  }}
                >
                  {t(c.labelKey)}
                </button>
              ))}
            </div>

            {/* Hides only a login/logout line whose account AND source
                address both match this router's own configured credential -
                RouterOS re-logs the REST session every ~10 minutes even with
                keep-alive working, and that recurring pair is most of the
                noise in the Auth category. A login attempt from the same
                account but a different address - the actual anomaly worth
                seeing - is left alone. */}
            <label
              title={t('log_hide_self_api_hint')}
              style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', cursor: 'pointer' }}
            >
              <input
                type="checkbox"
                checked={hideSelfApi}
                onChange={toggleHideSelfApi}
                style={{ width: 13, height: 13, accentColor: 'var(--color-primary)', cursor: 'pointer' }}
              />
              {t('log_hide_self_api')}
            </label>
          </div>

          {/* Logging topic drawer */}
          {rulesOpen && (
            <div style={{
              border: '1px solid var(--border-color)',
              borderRadius: 'var(--radius-sm)',
              padding: 10,
              background: 'var(--bg-secondary)',
              display: 'flex', flexDirection: 'column', gap: 8,
            }}>
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
                {t('log_topics_desc')}
              </div>
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                {TOPIC_PRESETS.map(topic => (
                  <button
                    key={topic}
                    type="button"
                    className="btn btn-secondary btn-sm"
                    disabled={rulesBusy || activeTopics.has(topic)}
                    onClick={() => addRule(topic)}
                    style={{ display: 'flex', alignItems: 'center', gap: 4 }}
                  >
                    <Plus size={11} />{topic}
                  </button>
                ))}
                <input
                  type="text"
                  className="form-input"
                  value={customTopic}
                  onChange={e => setCustomTopic(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addRule(customTopic); } }}
                  placeholder={t('custom_topic_placeholder')}
                  style={{ height: 28, width: 150, fontSize: 'var(--fs-sm)' }}
                />
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {rules.map(r => (
                  <div key={r.id} style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    fontSize: 'var(--fs-xs)', fontFamily: 'var(--font-mono, monospace)',
                  }}>
                    <span style={{ flex: 1, minWidth: 0 }} className="truncate">
                      {r.topics} → {r.action}
                    </span>
                    {r.is_managed ? (
                      <button
                        type="button"
                        className="btn-icon"
                        title={t('delete_rule_btn')}
                        disabled={rulesBusy}
                        onClick={() => removeRule(r)}
                        style={{ color: 'var(--color-danger)' }}
                      >
                        <Trash2 size={12} />
                      </button>
                    ) : (
                      // Router-owned rules are protected by the write guard;
                      // deleting them would silence the log entirely.
                      <span style={{ color: 'var(--text-muted)' }}>{t('log_rule_builtin')}</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Terminal viewport */}
          <div style={{ position: 'relative' }}>
            <div
              ref={viewportRef}
              onScroll={handleScroll}
              data-testid="log-viewport"
              style={{
                height: '48vh',
                overflowY: 'auto',
                background: '#0b0f16',
                color: '#d7e0ea',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-color)',
                padding: '8px 10px',
                fontFamily: 'var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)',
                fontSize: 'var(--fs-xs)',
                lineHeight: 1.55,
              }}
            >
              {entries.length === 0 && !loading && (
                <div style={{ color: '#6b7a8c' }}>{t('no_logs_found')}</div>
              )}
              {entries.map((e, i) => (
                <div
                  key={e.id ?? `${e.external_id || 'x'}-${i}`}
                  style={{ display: 'flex', gap: 8, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
                >
                  <span style={{ color: '#6b7a8c', flexShrink: 0 }}>{formatStamp(e.timestamp)}</span>
                  <span
                    style={{ color: severityColor(e.severity), flexShrink: 0, fontWeight: 700 }}
                    title={e.severity}
                  >
                    {(CATEGORIES.find(c => c.id === e.category)?.icon) || '·'}
                  </span>
                  <span style={{ color: '#7f9bb8', flexShrink: 0 }}>[{e.topics}]</span>
                  <span style={{ color: severityColor(e.severity) === SEVERITY_COLORS.info ? '#d7e0ea' : severityColor(e.severity) }}>
                    {e.message}
                  </span>
                </div>
              ))}
            </div>

            <button
              type="button"
              onClick={jumpToBottom}
              className="btn btn-secondary btn-sm"
              title={t('scroll_to_latest')}
              style={{ position: 'absolute', right: 12, bottom: 10, display: 'flex', alignItems: 'center', gap: 4 }}
            >
              <ArrowDownToLine size={12} />
            </button>
          </div>

          <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
            {t('log_count', { count: entries.length })}
            {source === 'live' ? ` · ${t('log_source_live_hint')}` : ` · ${t('log_source_stored_hint')}`}
          </div>
        </div>
      </div>
    </div>
  );
}

export default RouterLogsModal;
