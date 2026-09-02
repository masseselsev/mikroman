import React, { useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { CheckCircle2, Loader2, Lock, Unlock, Sparkles } from 'lucide-react';

/**
 * The connection details of one router — used both to add a new one and to
 * repair an existing one.
 *
 * Editing was previously impossible. The API had always supported it
 * (`PUT /routers/{id}`, exposed as `api.updateRouter`), but nothing in the UI
 * ever called it: a saved router could only be activated or deleted. That gap
 * only shows itself at the worst possible moment — when the stored details stop
 * working. Reset a router to factory defaults and its certificate, its REST
 * user and its password all disappear at once, leaving a record that cannot
 * connect and cannot be corrected.
 *
 * Deleting and re-adding was not an equivalent workaround. `devices.router_id`
 * is `ON DELETE SET NULL`, so devices survive — but the gateway traffic rollups,
 * the system metrics and the interface metrics are all `ON DELETE CASCADE`.
 * Re-adding the same router would silently discard every traffic total and
 * every health graph ever recorded for it.
 *
 * @param initial   Existing values to edit, or undefined to start empty.
 * @param mode      'create' | 'edit' — decides the password semantics below.
 * @param onSubmit  Receives the payload. In edit mode the password key is
 *                  omitted entirely unless a new one was typed.
 */
export function RouterConnectionForm({ initial, mode = 'create', onSubmit, onCancel, busy = false }) {
  const { t } = useI18n();
  const isEdit = mode === 'edit';

  const [form, setForm] = useState({
    name: initial?.name ?? '',
    host: initial?.host ?? '192.168.88.1',
    port: initial?.port ?? 443,
    use_ssl: initial?.use_ssl ?? true,
    ssl_verify: initial?.ssl_verify ?? false,
    // Deliberately blank in both modes. Pre-filling "admin" meant a click on
    // Test Connection probed the router with a username the operator never
    // chose, and the probe chain (HTTPS, then port 80) turned one click into
    // several failed logins in the router's own log.
    username: initial?.username ?? '',
    password: ''
  });

  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [provisioningSsl, setProvisioningSsl] = useState(false);
  const [sslSuccessMsg, setSslSuccessMsg] = useState(null);

  const set = (patch) => {
    setForm(prev => ({ ...prev, ...patch }));
    // Any change invalidates a previous verdict; leaving it on screen would
    // vouch for settings that are no longer the ones displayed.
    setTestResult(null);
    setSslSuccessMsg(null);
  };

  /**
   * The stored password is never returned by the API — `RouterResponse` has no
   * password field at all — so an edit form cannot pre-fill it, and a blank box
   * means "keep whatever is already saved".
   *
   * That makes testing a special case. Sending the form as-is would test with
   * an empty password, which the router records as a failed login for the named
   * user. Enough of those look exactly like a brute-force attempt and can get
   * this machine blacklisted by an anti-bruteforce rule, so the button simply
   * refuses to fire until a password is typed.
   */
  const canTest = form.host && form.username && form.password;

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.testRouterConnection(form);
      const data = res.data;
      if (data?.success) {
        setTestResult({
          ok: true,
          msg: `${t('router_test_connected')} ${data.board_name || 'MikroTik'}${data.ros_version ? ` (ROS ${data.ros_version})` : ''}`
        });
      } else {
        // A failed probe often knows the right answer already — the router
        // speaks HTTP on 80 rather than HTTPS on 443, say — so the suggestion
        // is offered as a one-click correction rather than as prose to read.
        setTestResult({
          ok: false,
          msg: data?.message || t('router_test_failed'),
          suggestion: data?.suggested_port != null
            ? { port: data.suggested_port, use_ssl: !!data.suggested_ssl }
            : null
        });
      }
    } catch (err) {
      setTestResult({ ok: false, msg: err.message });
    } finally {
      setTesting(false);
    }
  };

  const handleAutoProvisionSsl = async () => {
    setProvisioningSsl(true);
    setSslSuccessMsg(null);
    try {
      const res = await api.autoProvisionSslDirect({
        host: form.host,
        port: form.port,
        use_ssl: form.use_ssl,
        username: form.username,
        password: form.password,
      });
      if (res.data?.success) {
        const newPort = res.data.port || form.port || 443;
        setSslSuccessMsg(t('auto_ssl_success', { port: newPort }));
        const updated = {
          ...form,
          port: newPort,
          use_ssl: true,
          ssl_verify: false
        };
        setForm(updated);
        try {
          const testRes = await api.testRouterConnection(updated);
          const tData = testRes.data;
          if (tData?.success) {
            setTestResult({
              ok: true,
              msg: `${t('router_test_connected')} ${tData.board_name || 'MikroTik'} HTTPS (${newPort})`
            });
          }
        } catch (_) {
          // ignore secondary test error
        }
      } else {
        setTestResult({ ok: false, msg: res.data?.message || 'SSL provisioning failed' });
      }
    } catch (err) {
      setTestResult({ ok: false, msg: `SSL setup error: ${err.message}` });
    } finally {
      setProvisioningSsl(false);
    }
  };

  const applySuggestion = (suggestion) => {
    set({ port: suggestion.port, use_ssl: suggestion.use_ssl });
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    const payload = { ...form };
    if (isEdit && !form.password) {
      delete payload.password;
    }
    onSubmit(payload);
  };

  return (
    <form onSubmit={handleSubmit} className="panel" style={{ marginBottom: 14 }}>
      <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)', marginBottom: 8, color: 'var(--color-primary)' }}>
        {isEdit ? t('edit_router_title') : t('new_router_title')}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 8, marginBottom: 8 }}>
        <input
          type="text"
          className="form-input"
          placeholder={t('wizard_router_name')}
          value={form.name}
          onChange={e => set({ name: e.target.value })}
          required
        />
        <input
          type="text"
          className="form-input font-mono"
          placeholder="192.168.88.1"
          value={form.host}
          onChange={e => set({ host: e.target.value })}
          required
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1.2fr', gap: 8, marginBottom: 8 }}>
        <input
          type="number"
          className="form-input font-mono"
          placeholder="443"
          value={form.port}
          onChange={e => set({ port: parseInt(e.target.value) || 443 })}
          required
        />
        <input
          type="text"
          className="form-input"
          placeholder={t('username_label')}
          value={form.username}
          onChange={e => set({ username: e.target.value })}
          required
        />
        <input
          type="password"
          className="form-input"
          placeholder={isEdit ? t('password_keep_current') : t('password_label')}
          value={form.password}
          onChange={e => set({ password: e.target.value })}
          required={!isEdit}
        />
      </div>

      {/* Transport. A factory-reset router has no certificate and no www-ssl
          service, so recovering from one always means coming back over HTTP
          first — which is exactly when this toggle has to be reachable. */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4, flexWrap: 'wrap' }}>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={() => set({ use_ssl: !form.use_ssl, port: form.use_ssl ? 80 : 443 })}
          style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-xs)' }}
          title={t('toggle_transport_hint')}
        >
          {form.use_ssl ? <Lock size={13} /> : <Unlock size={13} />}
          {form.use_ssl ? 'HTTPS' : 'HTTP'}
        </button>

        {form.use_ssl && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
            <input
              type="checkbox"
              checked={form.ssl_verify}
              onChange={e => set({ ssl_verify: e.target.checked })}
            />
            {t('ssl_verify_label')}
          </label>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 10, gap: 8, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleTest}
            disabled={testing || provisioningSsl || !canTest}
            style={{ display: 'flex', alignItems: 'center', gap: 6 }}
            title={canTest ? undefined : t('test_needs_password_hint')}
          >
            {testing ? <Loader2 size={13} className="spin" /> : <CheckCircle2 size={13} />}
            {t('wizard_test_conn')}
          </button>

          {/* If connected over HTTP without SSL, offer 1-click Auto-SSL setup */}
          {testResult && testResult.ok && !form.use_ssl && (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleAutoProvisionSsl}
              disabled={provisioningSsl || testing}
              style={{ display: 'flex', alignItems: 'center', gap: 6, background: 'linear-gradient(135deg, #10b981 0%, #059669 100%)' }}
              title={t('auto_ssl_hint')}
            >
              {provisioningSsl ? <Loader2 size={13} className="spin" /> : <Sparkles size={13} />}
              <span>{t('auto_ssl_btn')}</span>
            </button>
          )}
        </div>

        <div style={{ display: 'flex', gap: 6 }}>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>
            {t('cancel')}
          </button>
          <button type="submit" className="btn btn-primary btn-sm" disabled={busy || provisioningSsl}>
            {busy ? <Loader2 size={13} className="spin" /> : null}
            {t('save')}
          </button>
        </div>
      </div>

      {isEdit && !form.password && (
        <div style={{ marginTop: 8, fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
          {t('test_needs_password_hint')}
        </div>
      )}

      {sslSuccessMsg && (
        <div
          style={{
            marginTop: 8,
            padding: '6px 10px',
            borderRadius: 'var(--radius-sm)',
            fontSize: 'var(--fs-xs)',
            background: 'rgba(16, 185, 129, 0.12)',
            color: 'var(--color-success)',
            border: '1px solid rgba(16, 185, 129, 0.3)',
            display: 'flex',
            alignItems: 'center',
            gap: 6
          }}
        >
          <CheckCircle2 size={14} style={{ flexShrink: 0 }} />
          <span>{sslSuccessMsg}</span>
        </div>
      )}

      {testResult && (
        <div style={{
          marginTop: 8,
          fontSize: 'var(--fs-xs)',
          color: testResult.ok ? 'var(--color-success)' : 'var(--color-danger)'
        }}>
          {testResult.msg}
          {testResult.suggestion && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => applySuggestion(testResult.suggestion)}
              style={{ marginLeft: 8, fontSize: 'var(--fs-2xs)', padding: '2px 8px' }}
            >
              {t('apply_suggested_transport', {
                port: testResult.suggestion.port,
                scheme: testResult.suggestion.use_ssl ? 'HTTPS' : 'HTTP'
              })}
            </button>
          )}
        </div>
      )}
    </form>
  );
}
