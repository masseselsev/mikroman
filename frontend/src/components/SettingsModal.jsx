import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { SECRET_PLACEHOLDER } from '../api/secrets';
import { templateErrorKey } from '../utils/ipLookup';
import { RouterConnectionForm } from './RouterConnectionForm';
import { RouterDeleteDialog, ChangeRouterModal, ArchivedRoutersSection } from './RouterLifecycle';
import { PauseNetworksModal, parseNetworksList } from './PauseNetworksModal';
import { X, Settings as SettingsIcon, Send, CheckCircle2, AlertTriangle, Power, Server, Plus, Pencil, Trash2, Check, Loader2, Network, Repeat } from 'lucide-react';

export function SettingsModal({
  isOpen,
  onClose,
  onReboot,
  onRoutersChanged,
  onQuotaChanged,
  initialTab = 'general',
  autoOpenAddRouter = false,
  activeRouter = null,
  initialRouters = [],
}) {
  const { t } = useI18n();
  const [activeTab, setActiveTab] = useState(initialTab);
  const [selectedRouterId, setSelectedRouterId] = useState(activeRouter?.id || null);
  // General Settings
  const [settings, setSettings] = useState({
    telegram_bot_token: '',
    telegram_admin_ids: '',
    telegram_mode: 'polling',
    telegram_webhook_url: '',
    backup_enabled: 'true',
    backup_interval_hours: '24',
    backup_retention_days: '90',
    backup_max_count: '30',
    log_scraping_enabled: 'true',
    log_retention_days: '14',
  });
  const [isSaving, setIsSaving] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [statusMsg, setStatusMsg] = useState('');
  const [showPauseNetworksModal, setShowPauseNetworksModal] = useState(false);

  // Routers list
  const [routers, setRouters] = useState(initialRouters || []);
  const [loadingRouters, setLoadingRouters] = useState(false);
  // Whether the "add a router" form is expanded on the Routers tab. Opened
  // automatically when the modal is launched via the "connect a router" prompt.
  const [showAddRouter, setShowAddRouter] = useState(false);
  // Which router's connection details are open for editing, if any. The form
  // itself owns the field state; this only decides whose details it is showing.
  const [editingRouterId, setEditingRouterId] = useState(null);
  const [savingRouter, setSavingRouter] = useState(false);
  // Router lifecycle: delete-choice dialog, hardware-swap modal, archived list.
  const [deleteDialogRouter, setDeleteDialogRouter] = useState(null);
  const [changeRouterTarget, setChangeRouterTarget] = useState(null);
  const [archivedRouters, setArchivedRouters] = useState([]);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [archivedBusyId, setArchivedBusyId] = useState(null);
  const [switchingProtocolId, setSwitchingProtocolId] = useState(null);

  const loadSettingsAndRouters = async (routerId = selectedRouterId) => {
    try {
      setLoadingRouters(true);
      const [settRes, routRes, archRes] = await Promise.all([
        api.getSettings(routerId).catch(() => ({ data: {} })),
        api.getRouters().catch(() => ({ data: [] })),
        api.getArchivedRouters().catch(() => ({ data: [] }))
      ]);
      if (settRes.data) setSettings(prev => ({ ...prev, ...settRes.data }));
      setArchivedRouters(archRes.data || []);
      if (routRes.data) {
        setRouters(routRes.data);
        if (!routerId && routRes.data.length > 0) {
          const act = routRes.data.find(r => r.is_default) || routRes.data[0];
          setSelectedRouterId(act.id);
        }
      }
    } catch (e) {
      console.error('Failed to load settings or routers', e);
    } finally {
      setLoadingRouters(false);
    }
  };

  // Quota is kept apart from the key/value settings map because it has its own
  // endpoint and derived status, and is expressed in GB for the operator.
  const [quota, setQuota] = useState({ limit_gb: 0, thresholds: [], notify_telegram: true, portal_url: '', portal_label: '' });

  const loadQuota = async (routerId = selectedRouterId) => {
    try {
      const res = await api.getQuota(routerId);
      if (res?.data) {
        setQuota({
          limit_gb: Math.round((res.data.limit_bytes || 0) / (1024 ** 3)),
          // Carried through explicitly: a setQuota that omits it leaves
          // `thresholds` undefined, and the threshold picker below calls
          // `.includes` on it and takes the whole modal to a blank screen.
          thresholds: Array.isArray(res.data.thresholds) ? res.data.thresholds : [],
          // Read back rather than assumed: assuming true meant that turning
          // Telegram alerts off survived the save but not the next page load,
          notify_telegram: res.data.notify_telegram ?? true,
          portal_url: res.data.portal_url || '',
          portal_label: res.data.portal_label || '',
        });
      }
    } catch (e) {
      console.debug('Failed to load quota config', e);
    }
  };

  const [billingCycle, setBillingCycle] = useState({ anchor_day: 1, anchor_hour: 0, anchor_minute: 0 });

  const loadBillingCycle = async (routerId = selectedRouterId) => {
    if (typeof api.getBillingCycleConfig !== 'function') return;
    try {
      const res = await api.getBillingCycleConfig(routerId);
      if (res?.data) {
        setBillingCycle({
          anchor_day: res.data.anchor_day || 1,
          anchor_hour: res.data.anchor_hour ?? 0,
          anchor_minute: res.data.anchor_minute ?? 0,
        });
      }
    } catch (e) {
      console.debug('Failed to load billing cycle config', e);
    }
  };

  // External IP lookup services. The catalogue comes from the server so the
  // built-ins can change without a frontend release.
  const [ipLookup, setIpLookup] = useState({ services: [], enabled_ids: [], default_id: null });
  const [customLookup, setCustomLookup] = useState({ name: '', url_template: '' });
  const [ipLookupError, setIpLookupError] = useState('');

  const loadIpLookup = async () => {
    try {
      const res = await api.getIpLookup();
      if (res?.data) setIpLookup(res.data);
    } catch (e) {
      console.debug('Failed to load IP lookup config', e);
    }
  };

  // The API models a set, but exactly one destination is selected here, so the
  // two stay in step: the selected id is the only enabled id.
  const selectLookupService = (id) => {
    setIpLookup(cfg => ({ ...cfg, default_id: id, enabled_ids: [id] }));
  };

  const addCustomService = () => {
    const name = customLookup.name.trim();
    const template = customLookup.url_template.trim();
    if (!name || templateErrorKey(template)) return;

    // Derived from the name so the id stays readable in stored settings, with a
    // suffix to keep it unique.
    const base = name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 24) || 'custom';
    const taken = new Set(ipLookup.services.map(s => s.id));
    let id = base;
    let n = 2;
    while (taken.has(id)) id = `${base}_${n++}`;

    // A newly added service becomes the selected one: adding it is the act of
    // choosing it.
    setIpLookup(cfg => ({
      ...cfg,
      services: [...cfg.services, { id, name, url_template: template, builtin: false }],
      enabled_ids: [id],
      default_id: id,
    }));
    setCustomLookup({ name: '', url_template: '' });
    setIpLookupError('');
  };

  const removeCustomService = (id) => {
    setIpLookup(cfg => {
      const services = cfg.services.filter(s => s.id !== id);
      // Deleting the selected service must leave something to click.
      const default_id = cfg.default_id === id ? (services[0]?.id ?? null) : cfg.default_id;
      return { ...cfg, services, default_id, enabled_ids: default_id ? [default_id] : [] };
    });
  };

  useEffect(() => {
    if (isOpen) {
      setActiveTab(initialTab || 'general');
      setShowAddRouter(!!autoOpenAddRouter);
      setEditingRouterId(null);
      const rid = activeRouter?.id ?? selectedRouterId;
      if (activeRouter?.id && activeRouter.id !== selectedRouterId) {
        setSelectedRouterId(activeRouter.id);
      }
      loadSettingsAndRouters(rid);
      loadQuota(rid);
      loadBillingCycle(rid);
      loadIpLookup();
      setIpLookupError('');
      setTestResult(null);
      setStatusMsg('');
    }
  }, [isOpen, initialTab, autoOpenAddRouter, activeRouter?.id]);

  if (!isOpen) return null;

  const handleSaveGeneral = async (e) => {
    e.preventDefault();
    setIsSaving(true);
    try {
      await api.saveSettings(settings);
      const effRid = selectedRouterId || activeRouter?.id || null;
      const savePromises = [
        api.saveQuota({
          limit_bytes: Math.max(0, Math.round(quota.limit_gb * (1024 ** 3))),
          thresholds: quota.thresholds,
          notify_telegram: quota.notify_telegram,
          portal_url: quota.portal_url.trim() || null,
          portal_label: quota.portal_label.trim() || null,
        }, effRid)
      ];
      if (typeof api.saveBillingCycleConfig === 'function') {
        savePromises.push(
          api.saveBillingCycleConfig(
            billingCycle.anchor_day,
            effRid,
            billingCycle.anchor_hour,
            billingCycle.anchor_minute
          )
        );
      }
      const [quotaRes] = await Promise.all(savePromises);
      // Only the user's own entries travel back; the built-in catalogue is the
      // server's and is reconstructed there.
      await api.saveIpLookup({
        enabled_ids: ipLookup.enabled_ids,
        default_id: ipLookup.default_id || undefined,
        custom: ipLookup.services.filter(sv => !sv.builtin).map(sv => ({
          id: sv.id, name: sv.name, url_template: sv.url_template,
        })),
      });

      if (onQuotaChanged) {
        onQuotaChanged(quotaRes?.data);
      }
      window.dispatchEvent(new CustomEvent('mikroman:quota-changed', { detail: quotaRes?.data }));
      try {
        localStorage.setItem('mikroman:quota-updated-at', Date.now().toString());
      } catch {
        // localStorage error non-fatal
      }

      setStatusMsg(t('save') + ' OK');
      setTimeout(() => {
        onClose();
      }, 700);
    } catch (err) {
      setStatusMsg('Error: ' + err.message);
    } finally {
      setIsSaving(false);
    }
  };

  const handleTestTelegram = async () => {
    setTestResult(null);
    try {
      const res = await api.testTelegram({
        bot_token: settings.telegram_bot_token,
        admin_ids: settings.telegram_admin_ids
      });
      setTestResult({ ok: res.success, msg: res.message });
    } catch (err) {
      setTestResult({ ok: false, msg: err.message });
    }
  };

  const handleCreateRouter = async (payload) => {
    setSavingRouter(true);
    try {
      await api.createRouter(payload);
      setShowAddRouter(false);
      await loadSettingsAndRouters();
      if (onRoutersChanged) onRoutersChanged();
    } catch (err) {
      alert('Error adding router: ' + err.message);
    } finally {
      setSavingRouter(false);
    }
  };

  /**
   * Repair the connection details of an existing router.
   *
   * This is the path that was missing entirely. A router whose stored settings
   * stop working - most sharply after a factory reset, which drops its
   * certificate, its REST user and its password in one go - previously left
   * only "delete and re-add", and that discards the gateway traffic rollups and
   * every hardware metric, all of which cascade on the router row.
   *
   * The payload arrives from RouterConnectionForm with the password key already
   * omitted when the operator left it blank, so an untouched password is never
   * overwritten with an empty string.
   */
  const handleUpdateRouter = async (routerId, payload) => {
    setSavingRouter(true);
    try {
      await api.updateRouter(routerId, payload);
      setEditingRouterId(null);
      await loadSettingsAndRouters();
      if (onRoutersChanged) onRoutersChanged();
    } catch (err) {
      alert('Error updating router: ' + err.message);
    } finally {
      setSavingRouter(false);
    }
  };

  const runLifecycle = async (fn) => {
    setLifecycleBusy(true);
    try {
      const res = await fn();
      setDeleteDialogRouter(null);
      setChangeRouterTarget(null);
      await loadSettingsAndRouters();
      if (onRoutersChanged) onRoutersChanged();
      if (res?.message) setStatusMsg(res.message);
    } catch (err) {
      alert(err.message || 'Operation failed');
    } finally {
      setLifecycleBusy(false);
    }
  };

  const handleArchiveRouter = (id) => runLifecycle(() => api.deleteRouter(id, 'archive'));
  const handlePurgeRouter = (id) => runLifecycle(() => api.deleteRouter(id, 'purge'));
  const handleChangeRouter = (id, payload) => runLifecycle(() => api.changeRouter(id, payload));

  const handleRestoreRouter = async (id) => {
    setArchivedBusyId(id);
    try {
      const res = await api.restoreRouter(id);
      await loadSettingsAndRouters();
      if (onRoutersChanged) onRoutersChanged();
      if (res?.message) setStatusMsg(res.message);
    } catch (err) {
      alert(err.message || 'Restore failed');
    } finally {
      setArchivedBusyId(null);
    }
  };

  const handlePurgeArchived = async (r) => {
    if (!window.confirm(t('router_delete_purge_confirm_plain', { name: r.name }))) return;
    setArchivedBusyId(r.id);
    try {
      await api.deleteRouter(r.id, 'purge');
      await loadSettingsAndRouters();
      if (onRoutersChanged) onRoutersChanged();
    } catch (err) {
      alert(err.message || 'Purge failed');
    } finally {
      setArchivedBusyId(null);
    }
  };

  const handleActivateRouter = async (id) => {
    await api.activateRouter(id);
    await loadSettingsAndRouters();
    if (onRoutersChanged) onRoutersChanged();
  };

  const handleUpgradeSsl = async (routerId) => {
    try {
      const res = await api.provisionRouterSsl(routerId);
      alert(res.message || 'SSL successfully configured and router upgraded to HTTPS');
      await loadSettingsAndRouters();
      if (onRoutersChanged) onRoutersChanged();
    } catch (err) {
      alert('Failed to configure SSL: ' + err.message);
    }
  };

  const handleToggleProtocol = async (routerId, targetUseSsl) => {
    setSwitchingProtocolId(routerId);
    try {
      const res = await api.switchRouterProtocol(routerId, targetUseSsl);
      if (res?.message) setStatusMsg(res.message);
      await loadSettingsAndRouters();
      if (onRoutersChanged) onRoutersChanged();
    } catch (err) {
      alert(err.message || 'Failed to switch protocol');
    } finally {
      setSwitchingProtocolId(null);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 960, width: '95vw', maxHeight: '90vh', display: 'flex', flexDirection: 'column' }}>
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700 }}>
            <SettingsIcon size={18} style={{ color: 'var(--color-primary)' }} />
            {t('tab_settings')}
          </div>
          <button className="btn-icon" onClick={onClose} style={{ width: 28, height: 28 }}>
            <X size={16} />
          </button>
        </div>

        {/* Settings Tab Navigation */}
        <div style={{ display: 'flex', borderBottom: '1px solid var(--border-color)', padding: '0 20px', background: 'var(--bg-secondary)' }}>
          <button
            type="button"
            className="btn btn-ghost"
            style={{
              borderRadius: 0,
              borderBottom: activeTab === 'general' ? '2px solid var(--color-primary)' : '2px solid transparent',
              color: activeTab === 'general' ? 'var(--color-primary)' : 'var(--text-secondary)',
              fontWeight: activeTab === 'general' ? 700 : 500
            }}
            onClick={() => setActiveTab('general')}
          >
            General & Bot
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            style={{
              borderRadius: 0,
              borderBottom: activeTab === 'routers' ? '2px solid var(--color-primary)' : '2px solid transparent',
              color: activeTab === 'routers' ? 'var(--color-primary)' : 'var(--text-secondary)',
              fontWeight: activeTab === 'routers' ? 700 : 500,
              display: 'flex',
              alignItems: 'center',
              gap: 6
            }}
            onClick={() => setActiveTab('routers')}
          >
            <Server size={14} />
            {t('routers_tab')} ({routers.length})
          </button>
        </div>

        {activeTab === 'general' && (
          <form onSubmit={handleSaveGeneral} style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
            <div className="modal-body" style={{ overflowY: 'auto', padding: '16px 20px' }}>
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))',
                gap: 16,
                alignItems: 'start'
              }}>
                {/* LEFT COLUMN: Intervals, Thresholds, Auto-Scan, Quota & Accounting */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  {/* Card 1: Telemetry & Polling Intervals */}
                  <div style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)', padding: '14px 16px' }}>
                    <h3 style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, marginBottom: 4, color: 'var(--color-primary)' }}>
                      {t('poll_interval_title')}
                    </h3>
                    <p style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', marginBottom: 10 }}>
                      {t('poll_interval_desc')}
                    </p>
                    <div className="form-group" style={{ marginBottom: 8 }}>
                      <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>Dashboard Stream Interval</label>
                      <select
                        className="form-select font-mono"
                        value={settings.telemetry_interval_seconds || '3'}
                        onChange={e => setSettings({ ...settings, telemetry_interval_seconds: e.target.value })}
                        style={{ width: '100%', height: 34, fontSize: 'var(--fs-xs)' }}
                      >
                        <option value="1">1s — Most responsive, highest router load</option>
                        <option value="2">2s — Responsive</option>
                        <option value="3">3s — Balanced (recommended)</option>
                        <option value="5">5s — Light</option>
                        <option value="10">10s — Minimal router load</option>
                      </select>
                    </div>

                    <div className="form-group" style={{ marginBottom: 8 }}>
                      <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('poll_telemetry_label')}</label>
                      <select
                        className="form-select font-mono"
                        value={settings.poll_interval_seconds || '10'}
                        onChange={e => setSettings({ ...settings, poll_interval_seconds: e.target.value })}
                        style={{ width: '100%', height: 34, fontSize: 'var(--fs-xs)' }}
                      >
                        <option value="5">5s</option>
                        <option value="10">10s — {t('poll_telemetry_default')}</option>
                        <option value="30">30s</option>
                        <option value="60">60s</option>
                        <option value="300">5 min</option>
                      </select>
                      <div className="form-hint" style={{ fontSize: 'var(--fs-3xs)' }}>{t('poll_telemetry_hint')}</div>
                    </div>

                    <div className="form-group">
                      <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('poll_heavy_label')}</label>
                      <select
                        className="form-select font-mono"
                        value={settings.heavy_sync_interval_seconds || '60'}
                        onChange={e => setSettings({ ...settings, heavy_sync_interval_seconds: e.target.value })}
                        style={{ width: '100%', height: 34, fontSize: 'var(--fs-xs)' }}
                      >
                        <option value="10">10s — {t('poll_heavy_legacy')}</option>
                        <option value="30">30s</option>
                        <option value="60">60s — {t('poll_heavy_default')}</option>
                        <option value="300">5 min</option>
                        <option value="900">15 min</option>
                      </select>
                      <div className="form-hint" style={{ fontSize: 'var(--fs-3xs)' }}>{t('poll_heavy_hint')}</div>
                    </div>
                  </div>

                  {/* Card 2: Alerts & Thresholds */}
                  <div style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)', padding: '14px 16px' }}>
                    <h3 style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, marginBottom: 4, color: 'var(--color-warning, #f59e0b)' }}>
                      {t('temp_warning_title')}
                    </h3>
                    <p style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', marginBottom: 8 }}>
                      {t('temp_warning_desc')}
                    </p>

                    <div className="form-group" style={{ marginBottom: 10 }}>
                      <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('temp_threshold_label')}</label>
                      <select
                        className="form-select font-mono"
                        value={settings.temp_warning_threshold || '80'}
                        onChange={e => setSettings({ ...settings, temp_warning_threshold: e.target.value })}
                        style={{ width: '100%', height: 34, fontSize: 'var(--fs-xs)' }}
                      >
                        <option value="65">65°C — Sensitive</option>
                        <option value="70">70°C — Low</option>
                        <option value="75">75°C — Moderate</option>
                        <option value="80">80°C — Standard Default</option>
                        <option value="85">85°C — High</option>
                        <option value="90">90°C — Critical</option>
                      </select>
                    </div>

                    <div style={{ height: 1, background: 'var(--border-color)', margin: '10px 0' }}></div>

                    <h4 style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, marginBottom: 4, color: 'var(--color-primary)' }}>
                      {t('unassigned_quarantine_title')}
                    </h4>
                    <p style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', marginBottom: 8 }}>
                      {t('unassigned_quarantine_desc')}
                    </p>

                    <div className="form-group">
                      <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('quarantine_speed_limit')}</label>
                      <select
                        className="form-select font-mono"
                        value={settings.unassigned_device_speed_limit || '5M/5M'}
                        onChange={e => setSettings({ ...settings, unassigned_device_speed_limit: e.target.value })}
                        style={{ width: '100%', height: 34, fontSize: 'var(--fs-xs)' }}
                      >
                        <option value="1M/1M">1 Mbps (1M/1M) — Strict</option>
                        <option value="2M/2M">2 Mbps (2M/2M) — Low</option>
                        <option value="5M/5M">5 Mbps (5M/5M) — Recommended</option>
                        <option value="10M/10M">10 Mbps (10M/10M) — Moderate</option>
                        <option value="20M/20M">20 Mbps (20M/20M) — Fast</option>
                        <option value="unlimited">Unlimited (0/0) — No Cap</option>
                      </select>
                    </div>
                  </div>

                  {/* Card 3: Auto-Discovery */}
                  <div style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)', padding: '14px 16px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                      <h3 style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, color: 'var(--color-primary)' }}>
                        {t('auto_scan_title')}
                      </h3>
                      <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                        <input
                          type="checkbox"
                          checked={settings.auto_scan_enabled !== 'false'}
                          onChange={e => setSettings({ ...settings, auto_scan_enabled: e.target.checked ? 'true' : 'false' })}
                          style={{ width: 16, height: 16, cursor: 'pointer', accentColor: 'var(--color-primary)' }}
                        />
                        <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 600 }}>
                          {settings.auto_scan_enabled !== 'false' ? t('enable_auto_scan') : t('auto_scan_paused')}
                        </span>
                      </label>
                    </div>
                    <p style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', margin: 0 }}>
                      {t('auto_scan_desc')}
                    </p>
                  </div>

                  {/* Card 4: Monthly Quota & Accounting Scope */}
                  <div style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)', padding: '14px 16px' }}>
                    <h3 style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, marginBottom: 4, color: 'var(--color-success)' }}>
                      {t('quota_title')}
                    </h3>
                    <p style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', marginBottom: 10 }}>
                      {t('quota_desc')}
                    </p>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
                      <div className="form-group">
                        <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('quota_limit')}</label>
                        <div className="input-with-suffix">
                          <input
                            type="number"
                            min="0"
                            step="1"
                            inputMode="numeric"
                            className="form-input font-mono"
                            value={quota.limit_gb || ''}
                            placeholder="0"
                            onChange={e => {
                              const parsed = Number(e.target.value);
                              setQuota({
                                ...quota,
                                limit_gb: Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 0,
                              });
                            }}
                            style={{ height: 34, fontSize: 'var(--fs-xs)' }}
                          />
                          <span className="input-suffix">GB</span>
                        </div>
                        <div className="form-hint" style={{ fontSize: 'var(--fs-3xs)' }}>
                          {quota.limit_gb > 0 ? t('quota_limit_hint') : t('quota_unlimited')}
                        </div>
                      </div>
                      <div className="form-group">
                        <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('quota_thresholds')}</label>
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center', minHeight: 34 }}>
                          {[50, 75, 80, 90, 100].map(th => {
                            const on = quota.thresholds.includes(th);
                            return (
                              <button
                                key={th}
                                type="button"
                                onClick={() => setQuota({
                                  ...quota,
                                  thresholds: on
                                    ? quota.thresholds.filter(x => x !== th)
                                    : [...quota.thresholds, th].sort((a, b) => a - b)
                                })}
                                className="badge"
                                style={{
                                  cursor: 'pointer',
                                  padding: '2px 6px',
                                  fontSize: 'var(--fs-3xs)',
                                  border: `1px solid ${on ? 'var(--color-primary)' : 'var(--border-color)'}`,
                                  background: on ? 'var(--color-primary-light)' : 'transparent',
                                  color: on ? 'var(--color-primary)' : 'var(--text-muted)',
                                  fontFamily: 'var(--font-mono)'
                                }}
                              >
                                {th}%
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
                      <div className="form-group">
                        <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('billing_anchor_day')} (1 - 31)</label>
                        <input
                          type="number"
                          min="1"
                          max="31"
                          className="form-input font-mono"
                          value={billingCycle.anchor_day || 1}
                          onChange={e => {
                            const val = parseInt(e.target.value, 10);
                            setBillingCycle(prev => ({
                              ...prev,
                              anchor_day: Number.isFinite(val) ? Math.min(31, Math.max(1, val)) : 1,
                            }));
                          }}
                          style={{ height: 34, fontSize: 'var(--fs-xs)' }}
                        />
                        <div className="form-hint" style={{ fontSize: 'var(--fs-3xs)' }}>
                          {t('billing_anchor_desc')}
                        </div>
                      </div>
                      <div className="form-group">
                        <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }} htmlFor="settings-billing-time">{t('billing_anchor_time')}</label>
                        <input
                          id="settings-billing-time"
                          type="time"
                          aria-label={t('billing_anchor_time')}
                          className="form-input font-mono"
                          value={`${String(billingCycle.anchor_hour).padStart(2, '0')}:${String(billingCycle.anchor_minute).padStart(2, '0')}`}
                          onChange={e => {
                            const [h, m] = (e.target.value || '0:0').split(':').map(Number);
                            setBillingCycle(prev => ({
                              ...prev,
                              anchor_hour: Number.isFinite(h) ? Math.min(23, Math.max(0, h)) : 0,
                              anchor_minute: Number.isFinite(m) ? Math.min(59, Math.max(0, m)) : 0,
                            }));
                          }}
                          style={{ height: 34, fontSize: 'var(--fs-xs)' }}
                        />
                        <div className="form-hint" style={{ fontSize: 'var(--fs-3xs)' }}>
                          {t('billing_anchor_time_hint')}
                        </div>
                      </div>
                    </div>

                    <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-xs)', cursor: 'pointer', color: 'var(--text-secondary)', marginBottom: 10 }}>
                      <input
                        type="checkbox"
                        checked={quota.notify_telegram}
                        onChange={e => setQuota({ ...quota, notify_telegram: e.target.checked })}
                        style={{ width: 14, height: 14, accentColor: 'var(--color-primary)' }}
                      />
                      {t('quota_notify_tg')}
                    </label>

                    <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 8, marginBottom: 8 }}>
                      <div>
                        <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('quota_portal_url')}</label>
                        <input
                          type="url"
                          className="form-input font-mono"
                          placeholder="https://my.isp.example/usage"
                          value={quota.portal_url}
                          onChange={e => setQuota({ ...quota, portal_url: e.target.value })}
                          style={{ height: 34, fontSize: 'var(--fs-xs)' }}
                        />
                      </div>
                      <div>
                        <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('quota_portal_label')}</label>
                        <input
                          type="text"
                          className="form-input"
                          maxLength={40}
                          placeholder={t('quota_portal_label_ph')}
                          value={quota.portal_label}
                          onChange={e => setQuota({ ...quota, portal_label: e.target.value })}
                          style={{ height: 34, fontSize: 'var(--fs-xs)' }}
                        />
                      </div>
                    </div>

                    <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border-color)' }}>
                      <label className="form-label" style={{ fontWeight: 600, fontSize: 'var(--fs-xs)', marginBottom: 6 }}>
                        {t('accounting_scope_title')}
                      </label>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-xs)', cursor: 'pointer' }}>
                          <input
                            type="radio"
                            name="traffic_accounting_scope"
                            value="wan_only"
                            checked={(settings.traffic_accounting_scope || 'wan_only') === 'wan_only'}
                            onChange={e => setSettings(s => ({ ...s, traffic_accounting_scope: e.target.value }))}
                            style={{ accentColor: 'var(--color-primary)' }}
                          />
                          <span>{t('accounting_scope_wan_only')}</span>
                        </label>
                        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-xs)', cursor: 'pointer' }}>
                          <input
                            type="radio"
                            name="traffic_accounting_scope"
                            value="all_routed"
                            checked={settings.traffic_accounting_scope === 'all_routed'}
                            onChange={e => setSettings(s => ({ ...s, traffic_accounting_scope: e.target.value }))}
                            style={{ accentColor: 'var(--color-primary)' }}
                          />
                          <span>{t('accounting_scope_all_routed')}</span>
                        </label>
                      </div>
                    </div>
                  </div>
                </div>

                {/* RIGHT COLUMN: Telegram Bot, Backups, Logs, Pause Networks, IP Lookup */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                  {/* Card 1: Telegram Bot Companion */}
                  <div style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)', padding: '14px 16px' }}>
                    <h3 style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, marginBottom: 10, color: 'var(--color-primary)' }}>
                      Telegram Bot Companion
                    </h3>
                    <div className="form-group" style={{ marginBottom: 10 }}>
                      <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('telegram_bot_token')}</label>
                      <input
                        type="password"
                        className="form-input font-mono"
                        value={settings.telegram_bot_token || ''}
                        onChange={e => setSettings({ ...settings, telegram_bot_token: e.target.value })}
                        placeholder="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
                        style={{ height: 34, fontSize: 'var(--fs-xs)' }}
                      />
                      {settings.telegram_bot_token === SECRET_PLACEHOLDER && (
                        <div className="form-hint" style={{ fontSize: 'var(--fs-3xs)' }}>{t('telegram_token_masked')}</div>
                      )}
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 10, marginBottom: 10 }}>
                      <div className="form-group">
                        <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('tg_admin_ids')}</label>
                        <input
                          type="text"
                          className="form-input font-mono"
                          value={settings.telegram_admin_ids || ''}
                          onChange={e => setSettings({ ...settings, telegram_admin_ids: e.target.value })}
                          placeholder="12345678, 87654321"
                          style={{ height: 34, fontSize: 'var(--fs-xs)' }}
                        />
                      </div>
                      <div className="form-group">
                        <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('tg_mode')}</label>
                        <select
                          className="form-select"
                          value={settings.telegram_mode || 'polling'}
                          onChange={e => setSettings({ ...settings, telegram_mode: e.target.value })}
                          style={{ height: 34, fontSize: 'var(--fs-xs)' }}
                        >
                          <option value="polling">Long Polling</option>
                          <option value="webhook">Webhook</option>
                        </select>
                      </div>
                    </div>

                    {settings.telegram_mode === 'webhook' && (
                      <div className="form-group" style={{ marginBottom: 10 }}>
                        <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('tg_webhook_url')}</label>
                        <input
                          type="text"
                          className="form-input font-mono"
                          value={settings.telegram_webhook_url || ''}
                          onChange={e => setSettings({ ...settings, telegram_webhook_url: e.target.value })}
                          placeholder="https://your-domain.example/api/v1/telegram/webhook"
                          style={{ height: 34, fontSize: 'var(--fs-xs)' }}
                        />
                        <div style={{ fontSize: 'var(--fs-3xs)', color: 'var(--text-muted)', marginTop: 4, lineHeight: 1.4 }}>
                          {t('tg_webhook_help')}
                        </div>
                      </div>
                    )}

                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={handleTestTelegram}
                      >
                        <Send size={13} />
                        {t('tg_test_btn')}
                      </button>

                      {testResult && (
                        <span style={{
                          fontSize: 'var(--fs-xs)',
                          color: testResult.ok ? 'var(--color-success)' : 'var(--color-danger)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 4
                        }}>
                          {testResult.ok ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}
                          {testResult.msg}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Card 2: Automated Backups & Retention Policy */}
                  <div style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)', padding: '14px 16px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                      <h3 style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, color: 'var(--color-primary)' }}>
                        {t('backup_settings_title')}
                      </h3>
                      <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                        <input
                          type="checkbox"
                          checked={settings.backup_enabled !== 'false'}
                          onChange={e => setSettings({ ...settings, backup_enabled: e.target.checked ? 'true' : 'false' })}
                          style={{ width: 16, height: 16, cursor: 'pointer', accentColor: 'var(--color-primary)' }}
                        />
                        <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 600 }}>
                          {settings.backup_enabled !== 'false' ? t('backup_enabled_label') : 'Backups Disabled'}
                        </span>
                      </label>
                    </div>
                    <p style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', marginBottom: 8 }}>
                      {t('backup_settings_desc')}
                    </p>

                    <div style={{ display: 'grid', gridTemplateColumns: '1.25fr 1fr 1fr', gap: 8, marginBottom: 8 }}>
                      <div className="form-group">
                        <label className="form-label" style={{ fontSize: 'var(--fs-3xs)' }}>{t('backup_interval_label')}</label>
                        <select
                          className="form-select font-mono"
                          value={settings.backup_interval_hours || '24'}
                          onChange={e => setSettings({ ...settings, backup_interval_hours: e.target.value })}
                          style={{ width: '100%', height: 34, fontSize: 'var(--fs-2xs)', padding: '4px 26px 4px 8px' }}
                        >
                          <option value="6">6h</option>
                          <option value="12">12h</option>
                          <option value="24">Daily (24h)</option>
                          <option value="48">48h</option>
                          <option value="168">Weekly (7d)</option>
                        </select>
                      </div>

                      <div className="form-group">
                        <label className="form-label" style={{ fontSize: 'var(--fs-3xs)' }}>{t('backup_retention_days_label')}</label>
                        <input
                          type="number"
                          min="7"
                          max="365"
                          className="form-input font-mono"
                          value={settings.backup_retention_days || '90'}
                          onChange={e => setSettings({ ...settings, backup_retention_days: e.target.value })}
                          placeholder="90"
                          style={{ height: 34, fontSize: 'var(--fs-2xs)' }}
                        />
                      </div>

                      <div className="form-group">
                        <label className="form-label" style={{ fontSize: 'var(--fs-3xs)' }}>{t('backup_max_count_label')}</label>
                        <input
                          type="number"
                          min="5"
                          max="100"
                          className="form-input font-mono"
                          value={settings.backup_max_count || '30'}
                          onChange={e => setSettings({ ...settings, backup_max_count: e.target.value })}
                          placeholder="30"
                          style={{ height: 34, fontSize: 'var(--fs-2xs)' }}
                        />
                      </div>
                    </div>

                    <p style={{ fontSize: 'var(--fs-3xs)', color: 'var(--text-muted)', margin: 0 }}>
                      📌 {t('backup_pinning_hint')}
                    </p>
                  </div>

                  {/* Card 3: Log Collection & Retention */}
                  <div style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)', padding: '14px 16px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                      <h3 style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, color: 'var(--color-primary)' }}>
                        {t('log_scraping_enabled_label')}
                      </h3>
                      <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                        <input
                          type="checkbox"
                          checked={settings.log_scraping_enabled !== 'false'}
                          onChange={e => setSettings({ ...settings, log_scraping_enabled: e.target.checked ? 'true' : 'false' })}
                          style={{ width: 16, height: 16, cursor: 'pointer', accentColor: 'var(--color-primary)' }}
                        />
                        <span style={{ fontSize: 'var(--fs-xs)', fontWeight: 600 }}>
                          {settings.log_scraping_enabled !== 'false' ? t('enabled') : t('disabled')}
                        </span>
                      </label>
                    </div>
                    <p style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', marginBottom: 8 }}>
                      {t('log_scraping_enabled_desc')}
                    </p>
                    <div className="form-group" style={{ maxWidth: 180 }}>
                      <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('log_retention_days_label')}</label>
                      <input
                        type="number"
                        min="1"
                        max="365"
                        className="form-input font-mono"
                        value={settings.log_retention_days || '14'}
                        onChange={e => setSettings({ ...settings, log_retention_days: e.target.value })}
                        style={{ height: 32, fontSize: 'var(--fs-xs)' }}
                      />
                    </div>
                  </div>

                  {/* Card 4: Pause Allowed Networks */}
                  <div style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)', padding: '14px 16px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                      <h3 style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, color: 'var(--color-primary)' }}>
                        {t('pause_networks_title')}
                      </h3>
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => setShowPauseNetworksModal(true)}
                        style={{ padding: '3px 8px', fontSize: 'var(--fs-xs)' }}
                      >
                        <Network size={12} style={{ marginRight: 4 }} />
                        {t('configure_pause_networks')}
                      </button>
                    </div>
                    <p style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', marginBottom: 8 }}>
                      {t('pause_networks_desc')}
                    </p>

                    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                      {parseNetworksList(settings.pause_allowed_networks).map(net => (
                        <span key={net} className="badge badge-neutral font-mono" style={{ fontSize: 'var(--fs-3xs)', padding: '2px 6px' }}>
                          {net}
                        </span>
                      ))}
                    </div>
                  </div>

                  {/* Card 5: External IP Lookup Services */}
                  <div style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', borderRadius: 'var(--radius-sm)', padding: '14px 16px' }}>
                    <h3 style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, marginBottom: 4, color: 'var(--color-primary)' }}>
                      {t('ip_lookup_title')}
                    </h3>
                    <p style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', marginBottom: 8 }}>
                      {t('ip_lookup_desc')}
                    </p>

                    <div className="list-box" style={{ maxHeight: 150, marginBottom: 8, overflowY: 'auto' }}>
                      {ipLookup.services.map(svc => {
                        const selected = ipLookup.default_id === svc.id;
                        return (
                          <label
                            key={svc.id}
                            className={`list-row${selected ? ' is-selected' : ''}`}
                            style={{ cursor: 'pointer', padding: '6px 8px' }}
                          >
                            <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0, flex: 1 }}>
                              <input
                                type="radio"
                                name="ip_lookup_service"
                                checked={selected}
                                onChange={() => selectLookupService(svc.id)}
                                style={{ cursor: 'pointer', accentColor: 'var(--color-primary)' }}
                              />
                              <span style={{ minWidth: 0 }}>
                                <span style={{ fontWeight: selected ? 700 : 500, fontSize: 'var(--fs-xs)' }}>{svc.name}</span>
                                <span className="font-mono truncate" style={{
                                  display: 'block',
                                  fontSize: 'var(--fs-3xs)',
                                  color: 'var(--text-muted)'
                                }}>
                                  {svc.url_template}
                                </span>
                              </span>
                            </span>

                            {!svc.builtin && (
                              <button
                                type="button"
                                className="btn-icon"
                                style={{ width: 22, height: 22, color: 'var(--color-danger)' }}
                                onClick={e => { e.preventDefault(); removeCustomService(svc.id); }}
                                title={t('delete')}
                              >
                                <Trash2 size={12} />
                              </button>
                            )}
                          </label>
                        );
                      })}
                    </div>

                    <div className="form-row" style={{ gridTemplateColumns: '1fr 2fr auto', alignItems: 'end', gap: 6 }}>
                      <div className="form-group" style={{ marginBottom: 0 }}>
                        <label className="form-label" style={{ fontSize: 'var(--fs-3xs)' }}>{t('ip_lookup_custom_name')}</label>
                        <input
                          type="text"
                          className="form-input"
                          value={customLookup.name}
                          onChange={e => setCustomLookup({ ...customLookup, name: e.target.value })}
                          placeholder={t('ip_lookup_name_placeholder')}
                          style={{ height: 30, fontSize: 'var(--fs-3xs)' }}
                        />
                      </div>
                      <div className="form-group" style={{ marginBottom: 0 }}>
                        <label className="form-label" style={{ fontSize: 'var(--fs-3xs)' }}>{t('ip_lookup_custom_url')}</label>
                        <input
                          type="text"
                          className="form-input font-mono"
                          value={customLookup.url_template}
                          onChange={e => setCustomLookup({ ...customLookup, url_template: e.target.value })}
                          placeholder="https://example.com/lookup/{ip}"
                          style={{ height: 30, fontSize: 'var(--fs-3xs)' }}
                        />
                      </div>
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={addCustomService}
                        disabled={!customLookup.name.trim() || !!templateErrorKey(customLookup.url_template)}
                        style={{ height: 30, padding: '0 8px', fontSize: 'var(--fs-3xs)' }}
                      >
                        <Plus size={12} />
                        {t('add')}
                      </button>
                    </div>

                    {customLookup.url_template.trim() && templateErrorKey(customLookup.url_template) && (
                      <div className="alert alert-warning" style={{ marginTop: 6, padding: '4px 8px', fontSize: 'var(--fs-3xs)' }}>
                        {t(templateErrorKey(customLookup.url_template))}
                      </div>
                    )}
                    {ipLookupError && (
                      <div className="alert alert-danger" style={{ marginTop: 6, padding: '4px 8px', fontSize: 'var(--fs-3xs)' }}>{ipLookupError}</div>
                    )}
                  </div>
                </div>
              </div>

              {/* Full-width System Actions */}
              <div style={{ height: 1, background: 'var(--border-color)', margin: '14px 0 10px 0' }}></div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 'var(--fs-sm)' }}>{t('reboot_router')}</div>
                  <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>Dispatches a reboot signal to the active MikroTik router</div>
                </div>
                <button
                  type="button"
                  className="btn btn-danger btn-sm"
                  onClick={onReboot}
                >
                  <Power size={14} />
                  {t('reboot_router')}
                </button>
              </div>

              {statusMsg && (
                <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--color-primary)', textAlign: 'center', marginTop: 8 }}>
                  {statusMsg}
                </div>
              )}
            </div>

            <div className="modal-footer">
              <button type="button" className="btn btn-secondary btn-sm" onClick={onClose}>
                {t('cancel')}
              </button>
              <button type="submit" className="btn btn-primary btn-sm" disabled={isSaving}>
                {t('save')}
              </button>
            </div>
          </form>
        )}

        {/* Tab 2: Routers Management */}
        {activeTab === 'routers' && (
          <div className="modal-body">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <div style={{ fontWeight: 700, fontSize: 'var(--fs-md)' }}>{t('routers_title')}</div>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={() => setShowAddRouter(!showAddRouter)}
                style={{ display: 'flex', alignItems: 'center', gap: 6 }}
              >
                <Plus size={14} />
                {t('add_router_btn')}
              </button>
            </div>

            {/* Add Router. The same form is reused for editing below, so
                the two can never drift apart in what they accept. */}
            {showAddRouter && (
              <RouterConnectionForm
                mode="create"
                busy={savingRouter}
                onSubmit={handleCreateRouter}
                onCancel={() => setShowAddRouter(false)}
              />
            )}

            {/* Routers Table */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {routers.map(r => (
                <div key={r.id}>
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '10px 14px',
                    background: r.is_default ? 'var(--bg-secondary)' : 'var(--bg-card)',
                    border: `1px solid ${r.is_default ? 'var(--color-primary)' : 'var(--border-color)'}`,
                    borderRadius: 'var(--radius-sm)'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, flex: 1 }}>
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: 'var(--radius-full)',
                        background: r.is_online ? 'var(--color-success)' : 'var(--text-muted)',
                        flexShrink: 0
                      }}
                    />
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        <span>{r.name}</span>
                        {r.is_default && (
                          <span className="badge badge-primary" style={{ fontSize: 'var(--fs-3xs)', padding: '1px 6px' }}>
                            {t('active_router')}
                          </span>
                        )}
                        <span className={`badge ${r.use_ssl ? 'badge-success' : 'badge-neutral'}`} style={{ fontSize: 'var(--fs-3xs)', padding: '1px 6px' }}>
                          {r.use_ssl ? 'HTTPS' : 'HTTP'}
                        </span>
                      </div>
                      <div
                        style={{
                          fontSize: 'var(--fs-xs)',
                          color: 'var(--text-muted)',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis'
                        }}
                        className="font-mono"
                        title={`${r.host}:${r.port}${r.board_name ? ` • ${r.board_name}` : (r.model ? ` • ${r.model}` : '')}${r.ros_version ? ` • RouterOS ${r.ros_version}` : ''}${r.architecture ? ` (${r.architecture})` : ''}`}
                      >
                        {r.host}:{r.port} {r.board_name ? `• ${r.board_name}` : (r.model ? `• ${r.model}` : '')} {r.ros_version ? `• ROS ${r.ros_version.replace(/\s*\(stable\)|\s*\(long-term\)|\s*\(testing\)|\s*\(development\)/gi, '')}` : ''} {r.architecture ? `(${r.architecture})` : ''}
                      </div>
                    </div>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0, marginLeft: 12 }}>
                    {r.is_online && (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => handleToggleProtocol(r.id, !r.use_ssl)}
                        disabled={switchingProtocolId === r.id}
                        style={{
                          fontSize: 'var(--fs-xs)',
                          padding: '3px 8px',
                          color: r.use_ssl ? 'var(--text-main)' : 'var(--color-success)',
                          borderColor: r.use_ssl ? 'var(--border-color)' : 'rgba(16, 185, 129, 0.3)'
                        }}
                        title={r.use_ssl ? t('switch_to_http_hint') : t('switch_to_https_hint')}
                      >
                        {switchingProtocolId === r.id ? (
                          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                            <Loader2 size={12} className="spin" />
                            {t('protocol_switching')}
                          </span>
                        ) : r.use_ssl ? (
                          <>🔓 {t('switch_to_http')}</>
                        ) : (
                          <>🔒 {t('switch_to_https')}</>
                        )}
                      </button>
                    )}
                    {!r.is_default && (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => handleActivateRouter(r.id)}
                        style={{ fontSize: 'var(--fs-xs)', padding: '3px 8px' }}
                      >
                        {t('set_active')}
                      </button>
                    )}
                    {/* The recovery path. Without it, a router whose stored
                        details stop working could only be deleted - taking its
                        traffic rollups and hardware metrics with it. */}
                    <button
                      type="button"
                      className="btn-icon"
                      style={{ width: 28, height: 28 }}
                      onClick={() => setEditingRouterId(editingRouterId === r.id ? null : r.id)}
                      title={t('edit_router_title')}
                    >
                      <Pencil size={14} />
                    </button>
                    {/* Swap the hardware behind this row, keeping its users,
                        devices and history. Works even if the old box is dead. */}
                    <button
                      type="button"
                      className="btn-icon"
                      style={{ width: 28, height: 28 }}
                      onClick={() => setChangeRouterTarget(r)}
                      title={t('router_change_title', { name: r.name })}
                    >
                      <Repeat size={14} />
                    </button>
                    <button
                      type="button"
                      className="btn-icon"
                      style={{ color: 'var(--color-danger)', width: 28, height: 28 }}
                      onClick={() => setDeleteDialogRouter(r)}
                      title={t('router_delete_title', { name: r.name })}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>

                {/* Edit panel, opened beneath the router it belongs to so the
                    values being changed stay next to the router they describe.
                    Keyed on the router id so switching rows remounts the form
                    with the new router's details rather than keeping the old
                    ones in its local state. */}
                {editingRouterId === r.id && (
                  <div style={{ marginTop: 8 }}>
                    <RouterConnectionForm
                      key={r.id}
                      mode="edit"
                      initial={r}
                      busy={savingRouter}
                      onSubmit={(payload) => handleUpdateRouter(r.id, payload)}
                      onCancel={() => setEditingRouterId(null)}
                    />
                  </div>
                )}
                </div>
              ))}
            </div>

            <ArchivedRoutersSection
              items={archivedRouters}
              busyId={archivedBusyId}
              onRestore={handleRestoreRouter}
              onPurge={handlePurgeArchived}
            />
          </div>
        )}
      </div>

      {deleteDialogRouter && (
        <RouterDeleteDialog
          router={deleteDialogRouter}
          busy={lifecycleBusy}
          onArchive={() => handleArchiveRouter(deleteDialogRouter.id)}
          onPurge={() => handlePurgeRouter(deleteDialogRouter.id)}
          onCancel={() => setDeleteDialogRouter(null)}
        />
      )}

      {changeRouterTarget && (
        <ChangeRouterModal
          router={changeRouterTarget}
          busy={lifecycleBusy}
          onSubmit={(payload) => handleChangeRouter(changeRouterTarget.id, payload)}
          onCancel={() => setChangeRouterTarget(null)}
        />
      )}

      {showPauseNetworksModal && (
        <PauseNetworksModal
          isOpen={showPauseNetworksModal}
          onClose={() => setShowPauseNetworksModal(false)}
          currentNetworks={settings.pause_allowed_networks}
          onSave={async (newNetworks) => {
            const updated = { ...settings, pause_allowed_networks: newNetworks };
            setSettings(updated);
            await api.saveSettings(updated);
          }}
        />
      )}
    </div>
  );
}
