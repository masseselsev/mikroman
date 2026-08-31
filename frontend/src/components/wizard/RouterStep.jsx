import React, { useState } from 'react';
import { useI18n } from '../../context/I18nContext';
import { api } from '../../api/client';
import {
  AlertCircle,
  ArrowRight,
  Award,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  FileText,
  Globe,
  Hash,
  HelpCircle,
  Loader2,
  Lock,
  RefreshCw,
  Server,
  ShieldCheck,
  Sparkles,
  Tag,
  UploadCloud,
  User,
  Zap,
} from 'lucide-react';

/**
 * Wizard step 1: reach the router, and optionally get HTTPS working.
 *
 * By far the largest step, and the only one that can fail in interesting ways -
 * wrong port, wrong scheme, self-signed certificate, an operator's own
 * certificate to upload. All of that state (test results, certificate lists,
 * upload forms, provisioning progress) is used here and nowhere else, so it
 * lives here rather than in the wizard shell, which only needs to know when the
 * step is done and what connection details it produced.
 */
export function RouterStep({ routerForm, setRouterForm, onNext }) {
  const { t } = useI18n();

  // Connection test & SSL provisioning state
  const [testing, setTesting] = useState(false);
  const [provisioningSsl, setProvisioningSsl] = useState(false);
  const [testResult, setTestResult] = useState(null);
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

  // The shell owns navigation; the step only reports that it is finished.
  const setStep = () => onNext();

  return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div>
          <h3 style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Server size={18} style={{ color: 'var(--color-primary)' }} />
            {t('wizard_step1')}
          </h3>
          <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginTop: 2 }}>
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
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border-color)',
              fontSize: 'var(--fs-xs)',
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
              <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text-primary)' }}>
                {t('wizard_use_ssl')}
              </div>
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginTop: 2 }}>
                {t('wizard_ssl_desc')}
              </div>
            </div>
          </label>

          {/* Where the app runs decides whether TLS buys anything. Measured
              on a hAP be^3 / RouterOS 7.25, HTTPS costs the router nothing
              extra over HTTP once connections are pooled - which they are -
              so this is about exposure, not load. */}
          <div className="alert alert-info" style={{ display: 'flex', gap: 8 }}>
            <ShieldCheck size={15} style={{ flexShrink: 0, marginTop: 1 }} />
            <span style={{ color: 'var(--text-secondary)' }}>{t('ssl_placement_hint')}</span>
          </div>

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
                  borderRadius: 'var(--radius-sm)',
                  fontSize: 'var(--fs-sm)',
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
                  borderRadius: 'var(--radius-md)',
                  fontSize: 'var(--fs-sm)',
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
                      style={{ fontSize: 'var(--fs-xs)', padding: '3px 8px', display: 'flex', alignItems: 'center', gap: 5 }}
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
                borderRadius: 'var(--radius-md)',
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
                  fontSize: 'var(--fs-sm)'
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
                  <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginBottom: 10 }}>
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
                      style={{ fontSize: 'var(--fs-xs)', padding: '4px 10px' }}
                    >
                      <Award size={13} style={{ marginRight: 4 }} />
                      {t('existing_certs')}
                    </button>
                    <button
                      type="button"
                      className={`btn btn-sm ${manualTab === 'upload' ? 'btn-primary' : 'btn-ghost'}`}
                      onClick={() => setManualTab('upload')}
                      style={{ fontSize: 'var(--fs-xs)', padding: '4px 10px' }}
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
                          style={{ fontSize: 'var(--fs-xs)', padding: '2px 6px', display: 'flex', alignItems: 'center', gap: 4 }}
                        >
                          <RefreshCw size={12} className={loadingCerts ? 'spin' : ''} />
                          <span>{t('refresh_certs')}</span>
                        </button>
                      </div>

                      {certError && (
                        <div
                          style={{
                            padding: '8px 12px',
                            borderRadius: 'var(--radius-sm)',
                            background: 'rgba(239, 68, 68, 0.1)',
                            border: '1px solid rgba(239, 68, 68, 0.3)',
                            color: 'var(--color-danger)',
                            fontSize: 'var(--fs-xs)',
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
                        <div style={{ textAlign: 'center', padding: '16px', color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>
                          <Loader2 size={16} className="spin" style={{ margin: '0 auto 6px auto' }} />
                          Loading router certificates...
                        </div>
                      )}

                      {!loadingCerts && !certError && routerCerts.length === 0 && (
                        <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', padding: '8px 0' }}>
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
                                borderRadius: 'var(--radius-sm)',
                                border: `1px solid ${c.is_active_ssl ? 'var(--color-primary)' : 'var(--border-color)'}`
                              }}
                            >
                              <div>
                                <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}>
                                  <span>{c.name}</span>
                                  {c.is_active_ssl && (
                                    <span className="badge badge-primary" style={{ fontSize: 'var(--fs-3xs)', padding: '1px 5px' }}>
                                      Active www-ssl
                                    </span>
                                  )}
                                </div>
                                <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }} className="font-mono">
                                  {c.common_name ? `CN: ${c.common_name} • ` : ''}Expires: {c.invalid_after || 'N/A'}
                                </div>
                              </div>

                              <button
                                type="button"
                                className="btn btn-secondary btn-sm"
                                onClick={() => handleBindCertificate(c.name)}
                                disabled={bindingCert === c.name}
                                style={{ fontSize: 'var(--fs-xs)', padding: '3px 8px' }}
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
                        <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('cert_name_label')}</label>
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
                        <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('cert_pem_label')}</label>
                        <textarea
                          className="form-input font-mono"
                          rows={3}
                          placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----"
                          value={uploadForm.cert_content}
                          onChange={e => setUploadForm({ ...uploadForm, cert_content: e.target.value })}
                          required
                          style={{ fontSize: 'var(--fs-xs)', resize: 'vertical' }}
                        />
                      </div>

                      <div className="form-group">
                        <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('key_pem_label')}</label>
                        <textarea
                          className="form-input font-mono"
                          rows={2}
                          placeholder="-----BEGIN PRIVATE KEY-----&#10;...&#10;-----END PRIVATE KEY-----"
                          value={uploadForm.key_content}
                          onChange={e => setUploadForm({ ...uploadForm, key_content: e.target.value })}
                          style={{ fontSize: 'var(--fs-xs)', resize: 'vertical' }}
                        />
                      </div>

                      <div className="form-group">
                        <label className="form-label" style={{ fontSize: 'var(--fs-xs)' }}>{t('passphrase_label')}</label>
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
  );
}
