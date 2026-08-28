import React, { useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { useTheme } from '../context/ThemeContext';
import { api } from '../api/client';
import {
  Server,
  Send,
  Moon,
  Sun,
  CheckCircle2,
  AlertCircle,
  Loader2,
  ArrowRight,
  ArrowLeft,
  ShieldCheck,
  Globe,
  Hash,
  User,
  Lock,
  Tag,
  Key,
  Bot,
  Sparkles,
  Zap,
  HelpCircle,
  FileText,
  UploadCloud,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  Award
} from 'lucide-react';

export function SetupWizard({ onComplete }) {
  const { t, lang, changeLang } = useI18n();
  const { theme, toggleTheme } = useTheme();

  const [step, setStep] = useState(1);

  // Router fields (default port 80 HTTP out of the box for RouterOS default configs)
  const [routerForm, setRouterForm] = useState({
    name: 'Main Router',
    host: '192.168.88.1',
    port: 80,
    use_ssl: false,
    ssl_verify: false,
    ca_cert: '',
    username: 'admin',
    password: ''
  });

  // Telegram fields
  const [telegramForm, setTelegramForm] = useState({
    bot_token: '',
    admin_ids: '',
    mode: 'polling'
  });

  // Connection test & SSL provisioning state
  const [testing, setTesting] = useState(false);
  const [provisioningSsl, setProvisioningSsl] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [sslSuccessMsg, setSslSuccessMsg] = useState(null);

  // Manual Certificate State
  const [showManualSsl, setShowManualSsl] = useState(false);
  const [manualTab, setManualTab] = useState('existing'); // 'existing' | 'upload'
  const [routerCerts, setRouterCerts] = useState([]);
  const [loadingCerts, setLoadingCerts] = useState(false);
  const [certError, setCertError] = useState(null);
  const [uploadForm, setUploadForm] = useState({
    cert_content: '',
    key_content: '',
    cert_name: 'custom-ssl',
    passphrase: ''
  });
  const [uploadingCert, setUploadingCert] = useState(false);
  const [bindingCert, setBindingCert] = useState(null);

  const handleSslToggle = (checked) => {
    setRouterForm(prev => ({
      ...prev,
      use_ssl: checked,
      port: checked ? (prev.port === 80 ? 443 : prev.port) : (prev.port === 443 ? 80 : prev.port)
    }));
  };

  const handleTestConnection = async (customForm = null) => {
    const formToTest = customForm || routerForm;
    setTesting(true);
    setTestResult(null);
    setError(null);
    setSslSuccessMsg(null);
    try {
      const res = await api.testRouterConnection(formToTest);
      if (res.data && res.data.success) {
        setTestResult({
          success: true,
          model: res.data.board_name,
          version: res.data.ros_version,
          ssl_status: res.data.ssl_status
        });
      } else {
        setTestResult({
          success: false,
          error: res.data?.message || 'Connection failed',
          suggested_port: res.data?.suggested_port,
          suggested_ssl: res.data?.suggested_ssl
        });
      }
    } catch (err) {
      setTestResult({
        success: false,
        error: err.message
      });
    } finally {
      setTesting(false);
    }
  };

  const handleApplySuggested = (suggestedPort, suggestedSsl) => {
    const updated = {
      ...routerForm,
      port: suggestedPort,
      use_ssl: suggestedSsl !== undefined ? suggestedSsl : routerForm.use_ssl
    };
    setRouterForm(updated);
    handleTestConnection(updated);
  };

  const handleAutoProvisionSsl = async () => {
    setProvisioningSsl(true);
    setError(null);
    setSslSuccessMsg(null);
    try {
      const res = await api.autoProvisionSslDirect({
        ...routerForm,
        port: routerForm.port,
        use_ssl: routerForm.use_ssl
      });
      if (res.data && res.data.success) {
        setSslSuccessMsg(t('auto_ssl_success'));
        const updated = {
          ...routerForm,
          port: 443,
          use_ssl: true,
          ssl_verify: false
        };
        setRouterForm(updated);
        await handleTestConnection(updated);
      }
    } catch (err) {
      setError(`SSL setup error: ${err.message}`);
    } finally {
      setProvisioningSsl(false);
    }
  };

  const handleFetchCertificates = async () => {
    if (!routerForm.host) {
      setCertError('Please specify router IP / Host first');
      return;
    }
    setLoadingCerts(true);
    setCertError(null);
    try {
      const res = await api.testListCertificates(routerForm);
      setRouterCerts(res.data || []);
    } catch (err) {
      setCertError(err.message || 'Failed to fetch certificates from router');
    } finally {
      setLoadingCerts(false);
    }
  };

  const handleBindCertificate = async (certName) => {
    setBindingCert(certName);
    setError(null);
    setSslSuccessMsg(null);
    try {
      const res = await api.testBindCertificate(routerForm, certName, 443);
      if (res.data && res.data.success) {
        setSslSuccessMsg(`Certificate '${certName}' bound to www-ssl! Switched to HTTPS (443).`);
        const updated = {
          ...routerForm,
          port: 443,
          use_ssl: true
        };
        setRouterForm(updated);
        await handleTestConnection(updated);
        await handleFetchCertificates();
      }
    } catch (err) {
      setError(`Failed to bind certificate: ${err.message}`);
    } finally {
      setBindingCert(null);
    }
  };

  const handleUploadCertificate = async (e) => {
    e.preventDefault();
    if (!uploadForm.cert_content) {
      setError('Please paste certificate content');
      return;
    }
    setUploadingCert(true);
    setError(null);
    setSslSuccessMsg(null);
    try {
      const res = await api.testUploadCertificate(routerForm, {
        ...uploadForm,
        port: 443
      });
      if (res.data && res.data.success) {
        setSslSuccessMsg(`Certificate '${uploadForm.cert_name}' uploaded and active on port 443!`);
        const updated = {
          ...routerForm,
          port: 443,
          use_ssl: true
        };
        setRouterForm(updated);
        await handleTestConnection(updated);
        await handleFetchCertificates();
      }
    } catch (err) {
      setError(`Failed to upload certificate: ${err.message}`);
    } finally {
      setUploadingCert(false);
    }
  };

  const handleFinish = async () => {
    setSaving(true);
    setError(null);
    try {
      // 1. Create Router in DB
      await api.createRouter(routerForm);

      // 2. Save settings if any
      const settingsPayload = {
        theme,
        lang
      };
      if (telegramForm.bot_token) {
        settingsPayload.telegram_bot_token = telegramForm.bot_token;
        settingsPayload.telegram_admin_ids = telegramForm.admin_ids;
        settingsPayload.telegram_mode = telegramForm.mode;
      }
      await api.saveSettings(settingsPayload);

      // 3. Notify parent
      if (onComplete) {
        await onComplete();
      }
    } catch (err) {
      setError(err.message || 'Failed to complete setup');
      setSaving(false);
    }
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'radial-gradient(ellipse at top, rgba(14, 28, 54, 0.92) 0%, rgba(8, 12, 20, 0.98) 100%)',
        backdropFilter: 'blur(16px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 2000,
        padding: '24px 16px',
        overflowY: 'auto'
      }}
    >
      <div
        className="card shadow-2xl"
        style={{
          width: '100%',
          maxWidth: 640,
          maxHeight: '94vh',
          overflowY: 'auto',
          background: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          borderRadius: 20,
          padding: '32px 28px',
          position: 'relative',
          boxShadow: '0 24px 64px rgba(0, 0, 0, 0.55)'
        }}
      >
        {/* Header */}
        <div style={{ textAlign: 'center', marginBottom: 22 }}>
          <div
            style={{
              width: 52,
              height: 52,
              borderRadius: 16,
              background: 'linear-gradient(135deg, rgba(11, 114, 201, 0.25) 0%, rgba(30, 135, 227, 0.1) 100%)',
              border: '1px solid rgba(11, 114, 201, 0.3)',
              color: 'var(--color-primary)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              margin: '0 auto 12px auto',
              boxShadow: '0 8px 20px var(--color-primary-glow)'
            }}
          >
            <Server size={28} />
          </div>
          <h2 style={{ fontSize: '1.45rem', fontWeight: 800, letterSpacing: '-0.02em' }}>
            {t('wizard_title')}
          </h2>
          <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: 4, lineHeight: 1.4 }}>
            {t('wizard_subtitle')}
          </p>
        </div>

        {/* Step Indicator */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 22 }}>
          {[
            { num: 1, label: t('wizard_step1') },
            { num: 2, label: t('wizard_step2') },
            { num: 3, label: t('wizard_step3') }
          ].map(s => (
            <div key={s.num} style={{ flex: 1 }}>
              <div
                style={{
                  height: 4,
                  borderRadius: 2,
                  background: step >= s.num ? 'var(--color-primary)' : 'var(--bg-secondary)',
                  transition: 'background 0.3s ease',
                  marginBottom: 6
                }}
              />
              <div
                style={{
                  fontSize: '0.725rem',
                  fontWeight: 600,
                  color: step === s.num ? 'var(--color-primary)' : 'var(--text-muted)',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis'
                }}
              >
                {s.num}. {s.label}
              </div>
            </div>
          ))}
        </div>

        {error && (
          <div
            style={{
              padding: '12px 16px',
              borderRadius: 10,
              background: 'rgba(239, 68, 68, 0.12)',
              border: '1px solid var(--color-danger)',
              color: 'var(--color-danger)',
              fontSize: '0.85rem',
              marginBottom: 16,
              display: 'flex',
              alignItems: 'center',
              gap: 10
            }}
          >
            <AlertCircle size={18} style={{ flexShrink: 0 }} />
            <span>{error}</span>
          </div>
        )}

        {/* Step 1: Router Connection */}
        {step === 1 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div>
              <h3 style={{ fontSize: '1.025rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8 }}>
                <Server size={18} style={{ color: 'var(--color-primary)' }} />
                {t('wizard_step1')}
              </h3>
              <p style={{ fontSize: '0.775rem', color: 'var(--text-muted)', marginTop: 2 }}>
                {t('wizard_step1_desc')}
              </p>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div className="form-group">
                <label className="form-label">{t('wizard_router_name')}</label>
                <div className="input-with-icon">
                  <Tag size={16} className="input-icon" />
                  <input
                    type="text"
                    className="form-input"
                    placeholder={t('wizard_router_name_placeholder')}
                    value={routerForm.name}
                    onChange={e => setRouterForm({ ...routerForm, name: e.target.value })}
                    required
                  />
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 10 }}>
                <div className="form-group">
                  <label className="form-label">{t('router_host')}</label>
                  <div className="input-with-icon">
                    <Globe size={16} className="input-icon" />
                    <input
                      type="text"
                      className="form-input font-mono"
                      placeholder="192.168.88.1"
                      value={routerForm.host}
                      onChange={e => setRouterForm({ ...routerForm, host: e.target.value })}
                      required
                    />
                  </div>
                </div>
                <div className="form-group">
                  <label className="form-label">{t('router_port')}</label>
                  <div className="input-with-icon">
                    <Hash size={16} className="input-icon" />
                    <input
                      type="number"
                      className="form-input font-mono"
                      placeholder={routerForm.use_ssl ? "443" : "80"}
                      value={routerForm.port}
                      onChange={e => setRouterForm({ ...routerForm, port: parseInt(e.target.value) || 80 })}
                      required
                    />
                  </div>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div className="form-group">
                  <label className="form-label">{t('router_user')}</label>
                  <div className="input-with-icon">
                    <User size={16} className="input-icon" />
                    <input
                      type="text"
                      className="form-input"
                      placeholder="admin"
                      value={routerForm.username}
                      onChange={e => setRouterForm({ ...routerForm, username: e.target.value })}
                      required
                    />
                  </div>
                </div>
                <div className="form-group">
                  <label className="form-label">{t('router_pass')}</label>
                  <div className="input-with-icon">
                    <Lock size={16} className="input-icon" />
                    <input
                      type="password"
                      className="form-input"
                      placeholder="••••••••"
                      value={routerForm.password}
                      onChange={e => setRouterForm({ ...routerForm, password: e.target.value })}
                    />
                  </div>
                </div>
              </div>

              {/* Port & Service Note */}
              <div
                style={{
                  padding: '7px 10px',
                  borderRadius: 8,
                  background: 'var(--bg-secondary)',
                  border: '1px solid var(--border-color)',
                  fontSize: '0.75rem',
                  color: 'var(--text-muted)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8
                }}
              >
                <HelpCircle size={14} style={{ color: 'var(--color-primary)', flexShrink: 0 }} />
                <span>{t('port_explanation')}</span>
              </div>

              {/* Checkbox Card for HTTPS */}
              <label className="checkbox-card">
                <input
                  type="checkbox"
                  id="wizard_ssl"
                  checked={routerForm.use_ssl}
                  onChange={e => handleSslToggle(e.target.checked)}
                />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                    {t('wizard_use_ssl')}
                  </div>
                  <div style={{ fontSize: '0.725rem', color: 'var(--text-muted)', marginTop: 2 }}>
                    {t('wizard_ssl_desc')}
                  </div>
                </div>
              </label>

              {/* Live Connection Tester Card & Auto-SSL Provisioning */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => handleTestConnection()}
                    disabled={testing || provisioningSsl}
                    style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                  >
                    {testing ? <Loader2 size={14} className="spin" /> : <ShieldCheck size={14} />}
                    <span>{t('wizard_test_conn')}</span>
                  </button>

                  {/* If connected over HTTP without SSL, offer 1-click Auto-SSL setup */}
                  {testResult && testResult.success && !routerForm.use_ssl && (
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      onClick={handleAutoProvisionSsl}
                      disabled={provisioningSsl || testing}
                      style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'linear-gradient(135deg, #10b981 0%, #059669 100%)' }}
                      title={t('auto_ssl_hint')}
                    >
                      {provisioningSsl ? <Loader2 size={14} className="spin" /> : <Sparkles size={14} />}
                      <span>{t('auto_ssl_btn')}</span>
                    </button>
                  )}
                </div>

                {sslSuccessMsg && (
                  <div
                    style={{
                      padding: '8px 12px',
                      borderRadius: 8,
                      fontSize: '0.8rem',
                      background: 'rgba(16, 185, 129, 0.12)',
                      color: 'var(--color-success)',
                      border: '1px solid rgba(16, 185, 129, 0.3)',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8
                    }}
                  >
                    <CheckCircle2 size={16} style={{ flexShrink: 0 }} />
                    <span>{sslSuccessMsg}</span>
                  </div>
                )}

                {testResult && (
                  <div
                    style={{
                      padding: '9px 12px',
                      borderRadius: 10,
                      fontSize: '0.825rem',
                      background: testResult.success ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                      color: testResult.success ? 'var(--color-success)' : 'var(--color-danger)',
                      border: `1px solid ${testResult.success ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)'}`,
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 6
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      {testResult.success ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
                      <span style={{ fontWeight: 600 }}>
                        {testResult.success
                          ? t('wizard_conn_success', { model: testResult.model || 'MikroTik', version: testResult.version || '' })
                          : t('wizard_conn_failed', { error: testResult.error })}
                      </span>
                    </div>

                    {testResult.suggested_port && (
                      <div style={{ marginTop: 2, display: 'flex', alignItems: 'center', gap: 6 }}>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => handleApplySuggested(testResult.suggested_port, testResult.suggested_ssl)}
                          style={{ fontSize: '0.725rem', padding: '3px 8px', display: 'flex', alignItems: 'center', gap: 5 }}
                        >
                          <Zap size={12} style={{ color: 'var(--color-warning)' }} />
                          <span>Switch to port {testResult.suggested_port} ({testResult.suggested_ssl ? 'HTTPS' : 'HTTP'})</span>
                        </button>
                      </div>
                    )}
                  </div>
                )}

                {/* MANUAL CERTIFICATE / SSL ACCORDION */}
                <div
                  style={{
                    marginTop: 4,
                    border: '1px solid var(--border-color)',
                    borderRadius: 10,
                    overflow: 'hidden',
                    background: 'var(--bg-secondary)'
                  }}
                >
                  <button
                    type="button"
                    onClick={() => {
                      setShowManualSsl(!showManualSsl);
                      if (!showManualSsl && routerCerts.length === 0) {
                        handleFetchCertificates();
                      }
                    }}
                    style={{
                      width: '100%',
                      padding: '10px 14px',
                      background: 'none',
                      border: 'none',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      cursor: 'pointer',
                      color: 'var(--text-primary)',
                      fontWeight: 600,
                      fontSize: '0.825rem'
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <FileText size={15} style={{ color: 'var(--color-primary)' }} />
                      <span>{t('manual_ssl_toggle')}</span>
                    </div>
                    {showManualSsl ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                  </button>

                  {showManualSsl && (
                    <div style={{ padding: '12px 14px', borderTop: '1px solid var(--border-color)', background: 'var(--bg-card)' }}>
                      <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 10 }}>
                        {t('manual_ssl_desc')}
                      </p>

                      {/* Sub-tabs */}
                      <div style={{ display: 'flex', gap: 6, marginBottom: 12 }}>
                        <button
                          type="button"
                          className={`btn btn-sm ${manualTab === 'existing' ? 'btn-primary' : 'btn-ghost'}`}
                          onClick={() => {
                            setManualTab('existing');
                            handleFetchCertificates();
                          }}
                          style={{ fontSize: '0.75rem', padding: '4px 10px' }}
                        >
                          <Award size={13} style={{ marginRight: 4 }} />
                          {t('existing_certs')}
                        </button>
                        <button
                          type="button"
                          className={`btn btn-sm ${manualTab === 'upload' ? 'btn-primary' : 'btn-ghost'}`}
                          onClick={() => setManualTab('upload')}
                          style={{ fontSize: '0.75rem', padding: '4px 10px' }}
                        >
                          <UploadCloud size={13} style={{ marginRight: 4 }} />
                          {t('upload_custom_cert')}
                        </button>
                      </div>

                      {/* Tab 1: Existing Router Certificates */}
                      {manualTab === 'existing' && (
                        <div>
                          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              onClick={handleFetchCertificates}
                              disabled={loadingCerts}
                              style={{ fontSize: '0.725rem', padding: '2px 6px', display: 'flex', alignItems: 'center', gap: 4 }}
                            >
                              <RefreshCw size={12} className={loadingCerts ? 'spin' : ''} />
                              <span>{t('refresh_certs')}</span>
                            </button>
                          </div>

                          {certError && (
                            <div
                              style={{
                                padding: '8px 12px',
                                borderRadius: 8,
                                background: 'rgba(239, 68, 68, 0.1)',
                                border: '1px solid rgba(239, 68, 68, 0.3)',
                                color: 'var(--color-danger)',
                                fontSize: '0.775rem',
                                marginBottom: 8,
                                display: 'flex',
                                alignItems: 'center',
                                gap: 6
                              }}
                            >
                              <AlertCircle size={14} style={{ flexShrink: 0 }} />
                              <span>{certError}</span>
                            </div>
                          )}

                          {loadingCerts && (
                            <div style={{ textAlign: 'center', padding: '16px', color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                              <Loader2 size={16} className="spin" style={{ margin: '0 auto 6px auto' }} />
                              Loading router certificates...
                            </div>
                          )}

                          {!loadingCerts && !certError && routerCerts.length === 0 && (
                            <div style={{ fontSize: '0.775rem', color: 'var(--text-muted)', padding: '8px 0' }}>
                              {t('no_certs_on_router')}
                            </div>
                          )}

                          {!loadingCerts && routerCerts.length > 0 && (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 180, overflowY: 'auto' }}>
                              {routerCerts.map((c) => (
                                <div
                                  key={c.name}
                                  style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'space-between',
                                    padding: '8px 10px',
                                    background: 'var(--bg-secondary)',
                                    borderRadius: 6,
                                    border: `1px solid ${c.is_active_ssl ? 'var(--color-primary)' : 'var(--border-color)'}`
                                  }}
                                >
                                  <div>
                                    <div style={{ fontSize: '0.8rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}>
                                      <span>{c.name}</span>
                                      {c.is_active_ssl && (
                                        <span className="badge badge-primary" style={{ fontSize: '0.65rem', padding: '1px 5px' }}>
                                          Active www-ssl
                                        </span>
                                      )}
                                    </div>
                                    <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }} className="font-mono">
                                      {c.common_name ? `CN: ${c.common_name} • ` : ''}Expires: {c.invalid_after || 'N/A'}
                                    </div>
                                  </div>

                                  <button
                                    type="button"
                                    className="btn btn-secondary btn-sm"
                                    onClick={() => handleBindCertificate(c.name)}
                                    disabled={bindingCert === c.name}
                                    style={{ fontSize: '0.725rem', padding: '3px 8px' }}
                                  >
                                    {bindingCert === c.name ? <Loader2 size={12} className="spin" /> : 'Use for HTTPS'}
                                  </button>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      )}

                      {/* Tab 2: Upload Custom Certificate */}
                      {manualTab === 'upload' && (
                        <form onSubmit={handleUploadCertificate} style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                          <div className="form-group">
                            <label className="form-label" style={{ fontSize: '0.75rem' }}>{t('cert_name_label')}</label>
                            <input
                              type="text"
                              className="form-input"
                              placeholder="my-domain-cert"
                              value={uploadForm.cert_name}
                              onChange={e => setUploadForm({ ...uploadForm, cert_name: e.target.value })}
                              required
                            />
                          </div>

                          <div className="form-group">
                            <label className="form-label" style={{ fontSize: '0.75rem' }}>{t('cert_pem_label')}</label>
                            <textarea
                              className="form-input font-mono"
                              rows={3}
                              placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----"
                              value={uploadForm.cert_content}
                              onChange={e => setUploadForm({ ...uploadForm, cert_content: e.target.value })}
                              required
                              style={{ fontSize: '0.725rem', resize: 'vertical' }}
                            />
                          </div>

                          <div className="form-group">
                            <label className="form-label" style={{ fontSize: '0.75rem' }}>{t('key_pem_label')}</label>
                            <textarea
                              className="form-input font-mono"
                              rows={2}
                              placeholder="-----BEGIN PRIVATE KEY-----&#10;...&#10;-----END PRIVATE KEY-----"
                              value={uploadForm.key_content}
                              onChange={e => setUploadForm({ ...uploadForm, key_content: e.target.value })}
                              style={{ fontSize: '0.725rem', resize: 'vertical' }}
                            />
                          </div>

                          <div className="form-group">
                            <label className="form-label" style={{ fontSize: '0.75rem' }}>{t('passphrase_label')}</label>
                            <input
                              type="password"
                              className="form-input"
                              placeholder="Optional passphrase"
                              value={uploadForm.passphrase}
                              onChange={e => setUploadForm({ ...uploadForm, passphrase: e.target.value })}
                            />
                          </div>

                          <button
                            type="submit"
                            className="btn btn-primary btn-sm"
                            disabled={uploadingCert}
                            style={{ alignSelf: 'flex-start', marginTop: 4, display: 'flex', alignItems: 'center', gap: 6 }}
                          >
                            {uploadingCert ? <Loader2 size={13} className="spin" /> : <UploadCloud size={13} />}
                            <span>{t('upload_cert_btn')}</span>
                          </button>
                        </form>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setStep(2)}
                disabled={!routerForm.name || !routerForm.host}
                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 22px' }}
              >
                <span>Next</span>
                <ArrowRight size={16} />
              </button>
            </div>
          </div>
        )}

        {/* Step 2: Telegram Bot */}
        {step === 2 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            <div>
              <h3 style={{ fontSize: '1.05rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8 }}>
                <Send size={18} style={{ color: 'var(--color-primary)' }} />
                {t('wizard_step2')}
              </h3>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: 2 }}>
                {t('wizard_step2_desc')}
              </p>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div className="form-group">
                <label className="form-label">{t('tg_bot_token')}</label>
                <div className="input-with-icon">
                  <Key size={16} className="input-icon" />
                  <input
                    type="text"
                    className="form-input font-mono"
                    placeholder="123456789:ABC-DEF1234ghIkl..."
                    value={telegramForm.bot_token}
                    onChange={e => setTelegramForm({ ...telegramForm, bot_token: e.target.value })}
                  />
                </div>
                <span className="form-hint">Obtain from @BotFather in Telegram</span>
              </div>

              <div className="form-group">
                <label className="form-label">{t('tg_admin_ids')}</label>
                <div className="input-with-icon">
                  <Hash size={16} className="input-icon" />
                  <input
                    type="text"
                    className="form-input font-mono"
                    placeholder="12345678, 87654321"
                    value={telegramForm.admin_ids}
                    onChange={e => setTelegramForm({ ...telegramForm, admin_ids: e.target.value })}
                  />
                </div>
                <span className="form-hint">User IDs authorized to execute commands & receive alerts</span>
              </div>

              <div className="form-group">
                <label className="form-label">{t('tg_mode')}</label>
                <div className="input-with-icon">
                  <Bot size={16} className="input-icon" />
                  <select
                    className="form-select"
                    value={telegramForm.mode}
                    onChange={e => setTelegramForm({ ...telegramForm, mode: e.target.value })}
                  >
                    <option value="polling">Long Polling (Zero-config / NAT-friendly)</option>
                    <option value="webhook">Webhook (Requires public HTTPS domain)</option>
                  </select>
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 12 }}>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setStep(1)}
                style={{ display: 'flex', alignItems: 'center', gap: 6 }}
              >
                <ArrowLeft size={16} />
                <span>Back</span>
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setStep(3)}
                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 22px' }}
              >
                <span>Next</span>
                <ArrowRight size={16} />
              </button>
            </div>
          </div>
        )}

        {/* Step 3: Preferences */}
        {step === 3 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
            <div>
              <h3 style={{ fontSize: '1.05rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8 }}>
                <Sparkles size={18} style={{ color: 'var(--color-primary)' }} />
                {t('wizard_step3')}
              </h3>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: 2 }}>
                {t('wizard_step3_desc')}
              </p>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div className="form-group">
                <label className="form-label">Theme Mode</label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  <button
                    type="button"
                    className={`btn ${theme === 'dark' ? 'btn-primary' : 'btn-secondary'}`}
                    style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 }}
                    onClick={() => theme !== 'dark' && toggleTheme()}
                  >
                    <Moon size={18} />
                    <span>WinBox Dark</span>
                  </button>
                  <button
                    type="button"
                    className={`btn ${theme === 'light' ? 'btn-primary' : 'btn-secondary'}`}
                    style={{ padding: '14px 16px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10 }}
                    onClick={() => theme !== 'light' && toggleTheme()}
                  >
                    <Sun size={18} />
                    <span>WebFig Light</span>
                  </button>
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">Interface Language</label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  <button
                    type="button"
                    className={`btn ${lang === 'en' ? 'btn-primary' : 'btn-secondary'}`}
                    style={{ padding: '14px 16px', fontSize: '0.95rem' }}
                    onClick={() => changeLang('en')}
                  >
                    🇬🇧 English
                  </button>
                  <button
                    type="button"
                    className={`btn ${lang === 'ru' ? 'btn-primary' : 'btn-secondary'}`}
                    style={{ padding: '14px 16px', fontSize: '0.95rem' }}
                    onClick={() => changeLang('ru')}
                  >
                    🇷🇺 Русский
                  </button>
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 12 }}>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setStep(2)}
                disabled={saving}
                style={{ display: 'flex', alignItems: 'center', gap: 6 }}
              >
                <ArrowLeft size={16} />
                <span>Back</span>
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleFinish}
                disabled={saving}
                style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 24px', fontWeight: 700 }}
              >
                {saving && <Loader2 size={16} className="spin" />}
                <span>{t('wizard_finish_btn')}</span>
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
