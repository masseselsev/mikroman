import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { templateErrorKey } from '../utils/ipLookup';
import { X, Settings as SettingsIcon, Send, CheckCircle2, AlertTriangle, Power, Server, Plus, Trash2, Check, Loader2 } from 'lucide-react';

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
  const [newRouter, setNewRouter] = useState({
    name: '',
    host: '192.168.88.1',
    port: 443,
    use_ssl: true,
    ssl_verify: false,
    // Deliberately blank. Pre-filling "admin" meant a click on Test Connection
    // probed the router with a username the user never chose - and the probe
    // chain (HTTPS, then port 80) turned one click into several failed logins
    // in the router's log.
    username: '',
    password: ''
  });
  const [testRouterResult, setTestRouterResult] = useState(null);
  const [testingRouter, setTestingRouter] = useState(false);

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

  const toggleLookupService = (id) => {
    setIpLookup(cfg => {
      const enabled = cfg.enabled_ids.includes(id)
        ? cfg.enabled_ids.filter(x => x !== id)
        : [...cfg.enabled_ids, id];
      // Disabling the default would leave a click with nowhere to go.
      const default_id = enabled.includes(cfg.default_id) ? cfg.default_id : (enabled[0] || null);
      return { ...cfg, enabled_ids: enabled, default_id };
    });
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

    setIpLookup(cfg => ({
      ...cfg,
      services: [...cfg.services, { id, name, url_template: template, builtin: false }],
      enabled_ids: [...cfg.enabled_ids, id],
    }));
    setCustomLookup({ name: '', url_template: '' });
    setIpLookupError('');
  };

  const removeCustomService = (id) => {
    setIpLookup(cfg => {
      const services = cfg.services.filter(s => s.id !== id);
      const enabled = cfg.enabled_ids.filter(x => x !== id);
      return {
        ...cfg,
        services,
        enabled_ids: enabled,
        default_id: enabled.includes(cfg.default_id) ? cfg.default_id : (enabled[0] || null),
      };
    });
  };

  useEffect(() => {
    if (isOpen) {
      loadSettingsAndRouters();
      loadQuota();
      loadIpLookup();
      setIpLookupError('');
      setTestResult(null);
      setTestRouterResult(null);
      setStatusMsg('');
      setShowAddRouter(false);
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

  const handleTestNewRouter = async () => {
    setTestingRouter(true);
    setTestRouterResult(null);
    try {
      const res = await api.testRouterConnection(newRouter);
      if (res.data?.success) {
        setTestRouterResult({
          ok: true,
          msg: `Connected! ${res.data.board_name || 'MikroTik'} (ROS ${res.data.ros_version || ''})`
        });
      } else {
        setTestRouterResult({ ok: false, msg: res.data?.message || 'Connection failed' });
      }
    } catch (err) {
      setTestRouterResult({ ok: false, msg: err.message });
    } finally {
      setTestingRouter(false);
    }
  };

  const handleCreateRouter = async (e) => {
    e.preventDefault();
    try {
      await api.createRouter(newRouter);
      setNewRouter({
        name: '',
        host: '192.168.88.1',
        port: 443,
        use_ssl: true,
        ssl_verify: false,
        username: 'admin',
        password: ''
      });
      setShowAddRouter(false);
      setTestRouterResult(null);
      await loadSettingsAndRouters();
      if (onRoutersChanged) onRoutersChanged();
    } catch (err) {
      alert('Error adding router: ' + err.message);
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
                    <select
                      className="form-select font-mono"
                      value={String(quota.limit_gb)}
                      onChange={e => setQuota({ ...quota, limit_gb: Number(e.target.value) })}
                      style={{ width: '100%', height: 36, fontSize: 'var(--fs-sm)' }}
                    >
                      <option value="0">{t('quota_unlimited')}</option>
                      {[50, 100, 200, 300, 500, 750, 1000, 1500, 2000, 3000].map(gb => (
                        <option key={gb} value={gb}>{gb >= 1000 ? `${gb / 1000} TB` : `${gb} GB`}</option>
                      ))}
                    </select>
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
                    const on = ipLookup.enabled_ids.includes(svc.id);
                    const isDefault = ipLookup.default_id === svc.id;
                    return (
                      <div key={svc.id} className={`list-row${on ? ' is-selected' : ''}`}>
                        <label style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, flex: 1, cursor: 'pointer' }}>
                          <input
                            type="checkbox"
                            checked={on}
                            onChange={() => toggleLookupService(svc.id)}
                            style={{ cursor: 'pointer', accentColor: 'var(--color-primary)' }}
                          />
                          <span style={{ minWidth: 0 }}>
                            <span style={{ fontWeight: on ? 700 : 500 }}>{svc.name}</span>
                            <span className="font-mono truncate" style={{
                              display: 'block',
                              fontSize: 'var(--fs-3xs)',
                              color: 'var(--text-muted)'
                            }}>
                              {svc.url_template}
                            </span>
                          </span>
                        </label>

                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                          {/* The service a plain click on the IP follows. */}
                          <button
                            type="button"
                            className={`badge ${isDefault ? 'badge-primary' : 'badge-neutral'}`}
                            disabled={!on}
                            onClick={() => setIpLookup(c => ({ ...c, default_id: svc.id }))}
                            style={{ border: 'none', cursor: on ? 'pointer' : 'not-allowed', opacity: on ? 1 : 0.4 }}
                            title={t('ip_lookup_set_default')}
                          >
                            {isDefault ? t('ip_lookup_default') : t('ip_lookup_set_default')}
                          </button>
                          {!svc.builtin && (
                            <button
                              type="button"
                              className="btn-icon"
                              style={{ width: 24, height: 24, color: 'var(--color-danger)' }}
                              onClick={() => removeCustomService(svc.id)}
                              title={t('delete')}
                            >
                              <Trash2 size={12} />
                            </button>
                          )}
                        </div>
                      </div>
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
                    value={settings.telemetry_interval_seconds || '1'}
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

            {/* Add Router Form */}
            {showAddRouter && (
              <form onSubmit={handleCreateRouter} style={{ background: 'var(--bg-secondary)', padding: 14, borderRadius: 'var(--radius-sm)', marginBottom: 14, border: '1px solid var(--border-color)' }}>
                <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)', marginBottom: 8, color: 'var(--color-primary)' }}>New Router Details</div>
                <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 8, marginBottom: 8 }}>
                  <input
                    type="text"
                    className="form-input"
                    placeholder={t('wizard_router_name')}
                    value={newRouter.name}
                    onChange={e => setNewRouter({ ...newRouter, name: e.target.value })}
                    required
                  />
                  <input
                    type="text"
                    className="form-input font-mono"
                    placeholder="192.168.88.1"
                    value={newRouter.host}
                    onChange={e => setNewRouter({ ...newRouter, host: e.target.value })}
                    required
                  />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8, marginBottom: 8 }}>
                  <input
                    type="number"
                    className="form-input font-mono"
                    placeholder="443"
                    value={newRouter.port}
                    onChange={e => setNewRouter({ ...newRouter, port: parseInt(e.target.value) || 443 })}
                    required
                  />
                  <input
                    type="text"
                    className="form-input"
                    placeholder="admin"
                    value={newRouter.username}
                    onChange={e => setNewRouter({ ...newRouter, username: e.target.value })}
                    required
                  />
                  <input
                    type="password"
                    className="form-input"
                    placeholder="Password"
                    value={newRouter.password}
                    onChange={e => setNewRouter({ ...newRouter, password: e.target.value })}
                  />
                </div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 10 }}>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={handleTestNewRouter}
                    disabled={testingRouter}
                    style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                  >
                    {testingRouter ? <Loader2 size={13} className="spin" /> : <CheckCircle2 size={13} />}
                    {t('wizard_test_conn')}
                  </button>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowAddRouter(false)}>
                      {t('cancel')}
                    </button>
                    <button type="submit" className="btn btn-primary btn-sm">
                      {t('save')}
                    </button>
                  </div>
                </div>
                {testRouterResult && (
                  <div style={{
                    marginTop: 8,
                    fontSize: 'var(--fs-xs)',
                    color: testRouterResult.ok ? 'var(--color-success)' : 'var(--color-danger)'
                  }}>
                    {testRouterResult.msg}
                  </div>
                )}
              </form>
            )}

            {/* Routers Table */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {routers.map(r => (
                <div
                  key={r.id}
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
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
