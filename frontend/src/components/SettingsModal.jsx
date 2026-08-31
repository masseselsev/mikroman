import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { templateErrorKey } from '../utils/ipLookup';
import { RouterConnectionForm } from './RouterConnectionForm';
import { X, Settings as SettingsIcon, Send, CheckCircle2, AlertTriangle, Power, Server, Plus, Pencil, Trash2, Check, Loader2 } from 'lucide-react';

export function SettingsModal({ isOpen, onClose, onReboot, onRoutersChanged }) {
  const { t } = useI18n();
  const [activeTab, setActiveTab] = useState('general');

  // General Settings
  const [settings, setSettings] = useState({
    telegram_bot_token: '',
    telegram_admin_ids: '',
    telegram_mode: 'polling',
    telegram_webhook_url: ''
  });
  const [isSaving, setIsSaving] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [statusMsg, setStatusMsg] = useState('');

  // Routers list
  const [routers, setRouters] = useState([]);
  const [loadingRouters, setLoadingRouters] = useState(false);
  const [showAddRouter, setShowAddRouter] = useState(false);
  // Which router's connection details are open for editing, if any. The form
  // itself owns the field state; this only decides whose details it is showing.
  const [editingRouterId, setEditingRouterId] = useState(null);
  const [savingRouter, setSavingRouter] = useState(false);

  const loadSettingsAndRouters = async () => {
    try {
      setLoadingRouters(true);
      const [settRes, routRes] = await Promise.all([
        api.getSettings().catch(() => ({ data: {} })),
        api.getRouters().catch(() => ({ data: [] }))
      ]);
      if (settRes.data) setSettings(prev => ({ ...prev, ...settRes.data }));
      if (routRes.data) setRouters(routRes.data);
    } catch (err) {
      console.error('Settings load error', err);
    } finally {
      setLoadingRouters(false);
    }
  };

  // Quota is kept apart from the key/value settings map because it has its own
  // endpoint and derived status, and is expressed in GB for the operator.
  const [quota, setQuota] = useState({ limit_gb: 0, thresholds: [], notify_telegram: true });

  const loadQuota = async () => {
    try {
      const res = await api.getQuota();
      if (res?.data) {
        setQuota({
          limit_gb: Math.round((res.data.limit_bytes || 0) / (1024 ** 3)),
          thresholds: res.data.thresholds || [],
          // Read back rather than assumed: assuming true meant that turning
          // Telegram alerts off survived the save but not the next page load,
          // and the following save wrote the assumption back over the choice.
          notify_telegram: res.data.notify_telegram ?? true,
        });
      }
    } catch (e) {
      console.debug('Failed to load quota config', e);
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
      loadSettingsAndRouters();
      loadQuota();
      loadIpLookup();
      setIpLookupError('');
      setTestResult(null);
      setStatusMsg('');
      setShowAddRouter(false);
      setEditingRouterId(null);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSaveGeneral = async (e) => {
    e.preventDefault();
    setIsSaving(true);
    try {
      await api.saveSettings(settings);
      await api.saveQuota({
        limit_bytes: Math.max(0, Math.round(quota.limit_gb * (1024 ** 3))),
        thresholds: quota.thresholds,
        notify_telegram: quota.notify_telegram,
      });
      // Only the user's own entries travel back; the built-in catalogue is the
      // server's and is reconstructed there.
      await api.saveIpLookup({
        enabled_ids: ipLookup.enabled_ids,
        default_id: ipLookup.default_id || undefined,
        custom: ipLookup.services.filter(sv => !sv.builtin).map(sv => ({
          id: sv.id, name: sv.name, url_template: sv.url_template,
        })),
      });
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

  const handleDeleteRouter = async (id) => {
    if (window.confirm(t('confirm_delete_router'))) {
      await api.deleteRouter(id);
      await loadSettingsAndRouters();
      if (onRoutersChanged) onRoutersChanged();
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

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={e => e.stopPropagation()} style={{ maxWidth: 640 }}>
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
          <form onSubmit={handleSaveGeneral}>
            <div className="modal-body">
              {/* Telegram Bot Section */}
              <div>
                <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, marginBottom: 10, color: 'var(--color-primary)' }}>
                  {t('telegram_integration')}
                </h3>
                <div className="form-group" style={{ marginBottom: 10 }}>
                  <label className="form-label">{t('tg_bot_token')}</label>
                  <input
                    type="password"
                    className="form-input font-mono"
                    value={settings.telegram_bot_token || ''}
                    onChange={e => setSettings({ ...settings, telegram_bot_token: e.target.value })}
                    placeholder="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
                  />
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 10, marginBottom: 10 }}>
                  <div className="form-group">
                    <label className="form-label">{t('tg_admin_ids')}</label>
                    <input
                      type="text"
                      className="form-input font-mono"
                      value={settings.telegram_admin_ids || ''}
                      onChange={e => setSettings({ ...settings, telegram_admin_ids: e.target.value })}
                      placeholder="12345678, 87654321"
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">{t('tg_mode')}</label>
                    <select
                      className="form-select"
                      value={settings.telegram_mode || 'polling'}
                      onChange={e => setSettings({ ...settings, telegram_mode: e.target.value })}
                    >
                      <option value="polling">Long Polling</option>
                      <option value="webhook">Webhook</option>
                    </select>
                  </div>
                </div>

                {/* Webhook mode needs a publicly reachable HTTPS URL; without
                    this field the mode could be selected but never configured. */}
                {settings.telegram_mode === 'webhook' && (
                  <div className="form-group" style={{ marginBottom: 10 }}>
                    <label className="form-label">{t('tg_webhook_url')}</label>
                    <input
                      type="text"
                      className="form-input font-mono"
                      value={settings.telegram_webhook_url || ''}
                      onChange={e => setSettings({ ...settings, telegram_webhook_url: e.target.value })}
                      placeholder="https://your-domain.example/api/v1/telegram/webhook"
                    />
                    <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginTop: 5, lineHeight: 1.5 }}>
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
                      fontSize: 'var(--fs-sm)',
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

              <div style={{ height: 1, background: 'var(--border-color)', margin: '6px 0' }}></div>

              <div>
                <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, marginBottom: 4, color: 'var(--color-success)' }}>
                  {t('quota_title')}
                </h3>
                <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginBottom: 10 }}>
                  {t('quota_desc')}
                </p>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
                  <div className="form-group">
                    <label className="form-label">{t('quota_limit')}</label>
                    {/* A plain number, not a picklist: an ISP allowance is
                        whatever the contract says, and a fixed set of round
                        figures cannot cover that. 0 means no limit. */}
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
                      />
                      <span className="input-suffix">GB</span>
                    </div>
                    <div className="form-hint">
                      {quota.limit_gb > 0 ? t('quota_limit_hint') : t('quota_unlimited')}
                    </div>
                  </div>
                  <div className="form-group">
                    <label className="form-label">{t('quota_thresholds')}</label>
                    <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', alignItems: 'center', minHeight: 36 }}>
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

                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-sm)', cursor: 'pointer', color: 'var(--text-secondary)' }}>
                  <input
                    type="checkbox"
                    checked={quota.notify_telegram}
                    onChange={e => setQuota({ ...quota, notify_telegram: e.target.checked })}
                    style={{ width: 14, height: 14, accentColor: 'var(--color-primary)' }}
                  />
                  {t('quota_notify_tg')}
                </label>
              </div>

              <div style={{ height: 1, background: 'var(--border-color)', margin: '6px 0' }}></div>

              {/* External IP lookup services */}
              <div>
                <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, marginBottom: 4, color: 'var(--color-primary)' }}>
                  {t('ip_lookup_title')}
                </h3>
                <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginBottom: 10 }}>
                  {t('ip_lookup_desc')}
                </p>

                <div className="list-box" style={{ maxHeight: 210, marginBottom: 10 }}>
                  {ipLookup.services.map(svc => {
                    const selected = ipLookup.default_id === svc.id;
                    return (
                      <label
                        key={svc.id}
                        className={`list-row${selected ? ' is-selected' : ''}`}
                        style={{ cursor: 'pointer' }}
                      >
                        <span style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, flex: 1 }}>
                          {/* One destination, not a set: the address links
                              somewhere, and a menu asked a question the reader
                              had no reason to care about mid-click. */}
                          <input
                            type="radio"
                            name="ip_lookup_service"
                            checked={selected}
                            onChange={() => selectLookupService(svc.id)}
                            style={{ cursor: 'pointer', accentColor: 'var(--color-primary)' }}
                          />
                          <span style={{ minWidth: 0 }}>
                            <span style={{ fontWeight: selected ? 700 : 500 }}>{svc.name}</span>
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
                            style={{ width: 24, height: 24, color: 'var(--color-danger)' }}
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

                {/* Custom service. The {ip} placeholder is the whole contract:
                    the app substitutes the address and nothing else. */}
                <div className="form-row" style={{ gridTemplateColumns: '1fr 2fr auto', alignItems: 'end', gap: 8 }}>
                  <div className="form-group">
                    <label className="form-label">{t('ip_lookup_custom_name')}</label>
                    <input
                      type="text"
                      className="form-input"
                      value={customLookup.name}
                      onChange={e => setCustomLookup({ ...customLookup, name: e.target.value })}
                      placeholder={t('ip_lookup_name_placeholder')}
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">{t('ip_lookup_custom_url')}</label>
                    <input
                      type="text"
                      className="form-input font-mono"
                      value={customLookup.url_template}
                      onChange={e => setCustomLookup({ ...customLookup, url_template: e.target.value })}
                      placeholder="https://example.com/lookup/{ip}"
                    />
                  </div>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={addCustomService}
                    disabled={!customLookup.name.trim() || !!templateErrorKey(customLookup.url_template)}
                  >
                    <Plus size={13} />
                    {t('add')}
                  </button>
                </div>

                {customLookup.url_template.trim() && templateErrorKey(customLookup.url_template) && (
                  <div className="alert alert-warning" style={{ marginTop: 8 }}>
                    {t(templateErrorKey(customLookup.url_template))}
                  </div>
                )}
                {ipLookupError && (
                  <div className="alert alert-danger" style={{ marginTop: 8 }}>{ipLookupError}</div>
                )}
              </div>

              <div style={{ height: 1, background: 'var(--border-color)', margin: '6px 0' }}></div>

              <div>
                <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, marginBottom: 4, color: 'var(--color-primary)' }}>
                  {t('poll_interval_title')}
                </h3>
                <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginBottom: 10 }}>
                  {t('poll_interval_desc')}
                </p>
                <div className="form-group" style={{ marginBottom: 6 }}>
                  <select
                    className="form-select font-mono"
                    value={settings.telemetry_interval_seconds || '3'}
                    onChange={e => setSettings({ ...settings, telemetry_interval_seconds: e.target.value })}
                    style={{ width: '100%', height: 36, fontSize: 'var(--fs-sm)' }}
                  >
                    <option value="1">1s — Most responsive, highest router load</option>
                    <option value="2">2s — Responsive</option>
                    <option value="3">3s — Balanced (recommended)</option>
                    <option value="5">5s — Light</option>
                    <option value="10">10s — Minimal router load</option>
                  </select>
                </div>
              </div>

              {/* Background Device Auto-Discovery (Auto-Scan) */}
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                  <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, color: 'var(--color-primary)' }}>
                    {t('auto_scan_title')}
                  </h3>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={settings.auto_scan_enabled !== 'false'}
                      onChange={e => setSettings({ ...settings, auto_scan_enabled: e.target.checked ? 'true' : 'false' })}
                      style={{ width: 18, height: 18, cursor: 'pointer', accentColor: 'var(--color-primary)' }}
                    />
                    <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600 }}>
                      {settings.auto_scan_enabled !== 'false' ? t('enable_auto_scan') : t('auto_scan_paused')}
                    </span>
                  </label>
                </div>
                <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
                  {t('auto_scan_desc')}
                </p>
              </div>

              <div style={{ height: 1, background: 'var(--border-color)', margin: '6px 0' }}></div>

              {/* Temperature Warning Threshold */}
              <div>
                <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, marginBottom: 4, color: 'var(--color-warning, #f59e0b)' }}>
                  {t('temp_warning_title')}
                </h3>
                <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginBottom: 10 }}>
                  {t('temp_warning_desc')}
                </p>

                <div className="form-group" style={{ marginBottom: 6 }}>
                  <label className="form-label">{t('temp_threshold_label')}</label>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <select
                      className="form-select font-mono"
                      value={settings.temp_warning_threshold || '80'}
                      onChange={e => setSettings({ ...settings, temp_warning_threshold: e.target.value })}
                      style={{ flex: 1, height: 36, fontSize: 'var(--fs-sm)' }}
                    >
                      <option value="65">65°C — Sensitive</option>
                      <option value="70">70°C — Low</option>
                      <option value="75">75°C — Moderate</option>
                      <option value="80">80°C — Standard Default</option>
                      <option value="85">85°C — High</option>
                      <option value="90">90°C — Critical</option>
                    </select>
                  </div>
                </div>
              </div>

              <div style={{ height: 1, background: 'var(--border-color)', margin: '6px 0' }}></div>

              {/* Unassigned / New Devices Quarantine Speed Limit */}
              <div>
                <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 700, marginBottom: 4, color: 'var(--color-primary)' }}>
                  {t('unassigned_quarantine_title')}
                </h3>
                <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginBottom: 10 }}>
                  {t('unassigned_quarantine_desc')}
                </p>

                <div className="form-group" style={{ marginBottom: 6 }}>
                  <label className="form-label">{t('quarantine_speed_limit')}</label>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <select
                      className="form-select font-mono"
                      value={settings.unassigned_device_speed_limit || '5M/5M'}
                      onChange={e => setSettings({ ...settings, unassigned_device_speed_limit: e.target.value })}
                      style={{ flex: 1, height: 36, fontSize: 'var(--fs-sm)' }}
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
              </div>

              <div style={{ height: 1, background: 'var(--border-color)', margin: '6px 0' }}></div>

              {/* System Actions */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 'var(--fs-sm)' }}>{t('reboot_router')}</div>
                  <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>Dispatches a reboot signal to the active MikroTik router</div>
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
                <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--color-primary)', textAlign: 'center' }}>
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
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: 'var(--radius-full)',
                        background: r.is_online ? 'var(--color-success)' : 'var(--text-muted)'
                      }}
                    />
                    <div>
                      <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)', display: 'flex', alignItems: 'center', gap: 6 }}>
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
                      <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }} className="font-mono">
                        {r.host}:{r.port} {r.board_name ? `• ${r.board_name}` : (r.model ? `• ${r.model}` : '')} {r.ros_version ? `• RouterOS ${r.ros_version}` : ''} {r.architecture ? `(${r.architecture})` : ''}
                      </div>
                    </div>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    {!r.use_ssl && r.is_online && (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => handleUpgradeSsl(r.id)}
                        style={{ fontSize: 'var(--fs-xs)', padding: '3px 8px', color: 'var(--color-success)', borderColor: 'rgba(16, 185, 129, 0.3)' }}
                        title={t('auto_ssl_hint')}
                      >
                        🔒 {t('provision_ssl_title')}
                      </button>
                    )}
                    {!r.is_default && (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => handleActivateRouter(r.id)}
                        style={{ fontSize: 'var(--fs-xs)', padding: '3px 8px' }}
                      >
                        Set Active
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
                    <button
                      type="button"
                      className="btn-icon"
                      style={{ color: 'var(--color-danger)', width: 28, height: 28 }}
                      onClick={() => handleDeleteRouter(r.id)}
                      title={t('confirm_delete_router')}
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
          </div>
        )}
      </div>
    </div>
  );
}
