import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { X, Settings as SettingsIcon, Send, CheckCircle2, AlertTriangle, Power, Server, Plus, Trash2, Check, Loader2 } from 'lucide-react';

export function SettingsModal({ isOpen, onClose, onReboot, onRoutersChanged }) {
  const { t } = useI18n();
  const [activeTab, setActiveTab] = useState('general');

  // General Settings
  const [settings, setSettings] = useState({
    telegram_bot_token: '',
    telegram_admin_ids: '',
    telegram_mode: 'polling'
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
    username: 'admin',
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

  useEffect(() => {
    if (isOpen) {
      loadSettingsAndRouters();
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
      const res = await api.testTelegram();
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
                <h3 style={{ fontSize: '0.9rem', fontWeight: 700, marginBottom: 10, color: 'var(--color-primary)' }}>
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
                      fontSize: '0.8rem',
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

              {/* System Actions */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: '0.85rem' }}>{t('reboot_router')}</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Dispatches a reboot signal to the active MikroTik router</div>
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
                <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--color-primary)', textAlign: 'center' }}>
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
              <div style={{ fontWeight: 700, fontSize: '0.9rem' }}>{t('routers_title')}</div>
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
              <form onSubmit={handleCreateRouter} style={{ background: 'var(--bg-secondary)', padding: 14, borderRadius: 8, marginBottom: 14, border: '1px solid var(--border-color)' }}>
                <div style={{ fontWeight: 700, fontSize: '0.85rem', marginBottom: 8, color: 'var(--color-primary)' }}>New Router Details</div>
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
                    fontSize: '0.75rem',
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
                    borderRadius: 8
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        background: r.is_online ? 'var(--color-success)' : 'var(--text-muted)'
                      }}
                    />
                    <div>
                      <div style={{ fontWeight: 700, fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span>{r.name}</span>
                        {r.is_default && (
                          <span className="badge badge-primary" style={{ fontSize: '0.65rem', padding: '1px 6px' }}>
                            {t('active_router')}
                          </span>
                        )}
                        <span className={`badge ${r.use_ssl ? 'badge-success' : 'badge-neutral'}`} style={{ fontSize: '0.65rem', padding: '1px 6px' }}>
                          {r.use_ssl ? 'HTTPS' : 'HTTP'}
                        </span>
                      </div>
                      <div style={{ fontSize: '0.725rem', color: 'var(--text-muted)' }} className="font-mono">
                        {r.host}:{r.port} {r.board_name ? `• ${r.board_name}` : ''}
                      </div>
                    </div>
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    {!r.use_ssl && r.is_online && (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => handleUpgradeSsl(r.id)}
                        style={{ fontSize: '0.75rem', padding: '3px 8px', color: 'var(--color-success)', borderColor: 'rgba(16, 185, 129, 0.3)' }}
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
                        style={{ fontSize: '0.75rem', padding: '3px 8px' }}
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
