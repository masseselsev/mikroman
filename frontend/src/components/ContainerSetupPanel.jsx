import React, { useState } from 'react';
import { api } from '../api/client';
import { useI18n } from '../context/I18nContext';
import { AlertTriangle, Check, HardDrive, Wrench, X } from 'lucide-react';

/**
 * Prepare a router to host a container - and carry this installation's data in.
 *
 * The panel is deliberately plan-first. Everything it can do writes to the
 * running configuration of a device that is routing traffic right now: a bridge,
 * a veth, a NAT rule, where a 340 MB image unpacks. So the button that changes
 * nothing (Plan) comes first, its output is the same list of steps the apply
 * walk executes - not a description rewritten afterwards - and Apply stays
 * disabled until that list is clean.
 *
 * The order of the three actions is the order that keeps a single writer:
 * prepare, migrate while the container is still stopped, then start it from the
 * container list. Migrating after the container boots would replace a live
 * database underneath it, which the backend also refuses to do.
 */

const STEP_TONE = {
  done: 'var(--color-success, #10b981)',
  create: 'var(--color-primary)',
  set: 'var(--color-primary)',
  exists: 'var(--text-muted)',
  skip: 'var(--text-muted)',
  blocked: 'var(--color-danger)',
  failed: 'var(--color-danger)',
  conflict: 'var(--color-danger)',
};

function StepRow({ step }) {
  const tone = STEP_TONE[step.action] || 'var(--text-muted)';
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 'var(--fs-xs)' }}>
      <span
        className="font-mono"
        style={{ width: 66, flexShrink: 0, color: tone, fontWeight: step.applied ? 700 : 600 }}
      >
        {step.action}
      </span>
      <span className="font-mono" style={{ width: 116, flexShrink: 0, color: 'var(--text-secondary)' }}>
        {step.key}
      </span>
      <span style={{ color: 'var(--text-muted)', minWidth: 0, overflowWrap: 'anywhere' }}>{step.detail}</span>
    </div>
  );
}

export function ContainerSetupPanel({ routerId, config, onDone }) {
  const { t } = useI18n();
  const [storage, setStorage] = useState('usb1-part1');
  const [subnet, setSubnet] = useState('172.17.0.0/24');
  const [expose, setExpose] = useState('br.lan');
  const [plan, setPlan] = useState(null);
  const [carryOver, setCarryOver] = useState([]);
  const [message, setMessage] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState('');

  const payload = () => ({
    storage_dir: storage.trim(),
    subnet: subnet.trim(),
    // Empty means no port forward at all. An unbounded dstnat would publish an
    // administrative UI on every interface the router has, WAN included.
    expose_on_interface: expose.trim() || null,
  });

  const pending = plan
    ? plan.steps.filter(s => s.action === 'create' || s.action === 'set').length
    : 0;

  const act = async (kind, call, onDone_) => {
    setBusy(kind);
    setError(null);
    setMessage(null);
    setCarryOver([]);
    try {
      const res = await call();
      onDone_(res);
    } catch (e) {
      // The backend answers a refused step with the router's own message and the
      // list of what already landed, so the failure has to be shown, not logged.
      setError(e.message || String(e));
    } finally {
      setBusy('');
    }
  };

  const field = {
    background: 'var(--bg-secondary)',
    border: '1px solid var(--border-color)',
    borderRadius: 'var(--radius-sm)',
    color: 'var(--text-primary)',
    font: 'inherit',
    fontSize: 'var(--fs-xs)',
    padding: '4px 8px',
    fontFamily: 'monospace',
  };

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Wrench size={16} style={{ color: 'var(--color-primary)' }} />
        <div style={{ fontWeight: 700, fontSize: 'var(--fs-md)' }}>{t('ctr_setup_title')}</div>
        <span style={{ flex: 1 }} />
        <button
          className="btn btn-secondary btn-sm"
          disabled={busy !== ''}
          onClick={() => act('plan', () => api.containerSetupPlan(routerId, payload()), r => setPlan(r?.data || null))}
        >
          {busy === 'plan' ? t('ctr_working') : t('ctr_plan')}
        </button>
        <button
          className="btn btn-primary btn-sm"
          disabled={busy !== '' || !plan || !plan.ok || pending === 0}
          title={pending === 0 ? t('ctr_nothing_pending') : t('ctr_apply_hint')}
          onClick={() => act('apply', () => api.containerSetupApply(routerId, payload()),
            r => { setPlan(r?.data || null); setMessage(t('ctr_applied')); onDone && onDone(); })}
        >
          {busy === 'apply' ? t('ctr_working') : t('ctr_apply')}
          {pending > 0 ? ` (${pending})` : ''}
        </button>
        <button
          className="btn btn-secondary btn-sm"
          disabled={busy !== '' || !plan}
          onClick={() => act('migrate', () => api.containerMigrateData(routerId, {
            storage_dir: storage.trim(),
          }), r => {
            const d = r?.data || {};
            setMessage(`${t('ctr_staged')}: ${((d.database_bytes || 0) / 1048576).toFixed(1)} MB`
              + ` -> ${d.destination || ''} (.secret_key: ${d.secret_key})`);
            // RouterOS takes no binary uploads, so the last copy is stated rather
            // than silently skipped - the operator must not think the data moved.
            setCarryOver(d.next_steps || []);
          })}
        >
          {busy === 'migrate' ? t('ctr_working') : t('ctr_migrate')}
        </button>
      </div>

      <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>{t('ctr_setup_hint')}</div>

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 'var(--fs-xs)' }}>
          <HardDrive size={13} style={{ color: 'var(--text-muted)' }} />
          {t('ctr_setup_storage')}
          <input style={{ ...field, width: 150 }} value={storage} onChange={e => setStorage(e.target.value)} />
        </label>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 'var(--fs-xs)' }}>
          {t('ctr_setup_subnet')}
          <input style={{ ...field, width: 130 }} value={subnet} onChange={e => setSubnet(e.target.value)} />
        </label>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 'var(--fs-xs)' }}>
          {t('ctr_setup_expose')}
          <input style={{ ...field, width: 110 }} value={expose} onChange={e => setExpose(e.target.value)}
            placeholder={t('ctr_setup_expose_ph')} />
        </label>
      </div>

      {/* Where the image layers would go today: the reason this panel exists. */}
      <div className="font-mono" style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
        layer-dir: {config?.layer_dir || '(internal flash)'} · tmpdir: {config?.tmpdir || '(internal flash)'}
      </div>

      {error && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', color: 'var(--color-danger)', fontSize: 'var(--fs-xs)' }}>
          <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
          <span style={{ overflowWrap: 'anywhere' }}>{error}</span>
        </div>
      )}
      {message && !error && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', color: 'var(--color-success)', fontSize: 'var(--fs-xs)' }}>
          <Check size={14} /> <span style={{ overflowWrap: 'anywhere' }}>{message}</span>
        </div>
      )}

      {/* The part RouterOS will not do for us: no upload endpoint, and /file/add
          only carries JSON text. Stating it beats reporting a migration that did
          not move the database. */}
      {carryOver.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3, paddingLeft: 2 }}>
          {carryOver.map((line, i) => (
            <div key={i} className="font-mono" style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-secondary)', overflowWrap: 'anywhere' }}>
              {i + 1}. {line}
            </div>
          ))}
        </div>
      )}

      {plan && plan.blockers.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          {plan.blockers.map((b, i) => (
            <div key={i} style={{ display: 'flex', gap: 6, fontSize: 'var(--fs-xs)', color: 'var(--color-danger)' }}>
              <X size={13} style={{ flexShrink: 0, marginTop: 2 }} />
              <span style={{ overflowWrap: 'anywhere' }}>{b}</span>
            </div>
          ))}
        </div>
      )}

      {plan && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {plan.steps.map(s => <StepRow key={s.key} step={s} />)}
        </div>
      )}
    </div>
  );
}
