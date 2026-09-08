import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { useI18n } from '../context/I18nContext';
import { AlertTriangle, Check, HardDrive, RefreshCw, Wrench, X } from 'lucide-react';

/**
 * Prepare a router to host a container.
 *
 * The panel is plan-first because everything it can do writes to the running
 * configuration of a device that is routing traffic right now: a bridge, a veth,
 * a NAT rule, where a 340 MB image unpacks. Plan writes nothing, its output is
 * the same list of steps Apply executes rather than a description rewritten
 * afterwards, and Apply stays disabled until that list is clean.
 *
 * Storage is chosen from `/disk` rather than typed. The check used to be "does
 * some path with this name appear in /file", which passes for a stick mounted
 * read-only, for an NTFS volume RouterOS cannot write, and for a 128 MB
 * partition that cannot hold a 340 MB image - and each of those fails minutes
 * later, mid-pull, with a message from the registry. So the picker lists what
 * the router reports, marks what is actually usable, and refuses the rest with
 * the reason next to it.
 *
 * Formatting is offered for a device that cannot be used as-is, and only for
 * those the router says are free of container state. It erases the device, so it
 * asks for the slot name to be typed back; the backend repeats every guard
 * regardless of what this form says.
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

function formatSize(bytes) {
  if (!bytes) return '—';
  const mb = bytes / (1024 * 1024);
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb.toFixed(0)} MB`;
}

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

/** One `/disk` row, as a radio option with the facts that decide the choice. */
function DiskOption({ disk, selected, onSelect }) {
  const { t } = useI18n();
  const reason = !disk.mounted ? t('ctr_disk_unmounted')
    : disk.read_only ? t('ctr_disk_readonly')
    : !disk.fs || disk.fs === '-' ? t('ctr_disk_no_fs')
    : disk.formatting ? t('ctr_disk_formatting')
    : null;
  return (
    <label className="checkbox-card" style={{ display: 'flex', gap: 8, alignItems: 'flex-start', cursor: 'pointer' }}>
      <input type="radio" name="ctr-storage" checked={selected} onChange={() => onSelect(disk.slot)}
        style={{ marginTop: 3, accentColor: 'var(--color-primary)' }} />
      <span style={{ minWidth: 0 }}>
        <span className="font-mono" style={{ fontWeight: 700, fontSize: 'var(--fs-xs)' }}>{disk.slot}</span>
        <span style={{ display: 'block', fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', overflowWrap: 'anywhere' }}>
          {disk.fs && disk.fs !== '-' ? `${disk.fs} · ` : ''}
          {formatSize(disk.free_bytes)} {t('ctr_disk_free')}
          {disk.model ? ` · ${disk.model}` : ''}
        </span>
        {reason && (
          <span style={{ display: 'block', fontSize: 'var(--fs-2xs)', color: 'var(--color-danger)' }}>{reason}</span>
        )}
      </span>
    </label>
  );
}

export function ContainerSetupPanel({ routerId, config, onDone }) {
  const { t } = useI18n();
  const [storage, setStorage] = useState('usb1-part1');
  const [subnet, setSubnet] = useState('172.17.0.0/24');
  const [expose, setExpose] = useState('br.lan');
  const [ramHigh, setRamHigh] = useState('');
  const [plan, setPlan] = useState(null);
  const [disks, setDisks] = useState([]);
  const [verdict, setVerdict] = useState(null);
  const [message, setMessage] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState('');
  const [formatTarget, setFormatTarget] = useState(null);
  const [formatAck, setFormatAck] = useState('');
  const [formatFs, setFormatFs] = useState('ext4');

  const loadStorage = useCallback(async () => {
    if (!routerId) return;
    try {
      const res = await api.containerStorage(routerId, storage.trim());
      const data = res?.data || {};
      setDisks(data.disks || []);
      setVerdict(data);
      // Nothing chosen yet (or the typed name is not a disk): pre-pick the first
      // usable one, so a fresh router offers a decision instead of a blank box.
      const usable = (data.disks || []).filter(d => d.usable_for_containers);
      if (usable.length && !usable.some(d => d.slot === data.matched_slot)) {
        const known = (data.disks || []).some(d => d.slot === storage.trim() || d.mount_point === storage.trim());
        if (!known) setStorage(usable[0].slot);
      }
    } catch (e) {
      setError(e.message || String(e));
    }
  }, [routerId, storage]);

  useEffect(() => { loadStorage(); }, [routerId]);   // eslint-disable-line react-hooks/exhaustive-deps

  const payload = () => ({
    storage_dir: storage.trim(),
    subnet: subnet.trim(),
    // Empty means no port forward at all. An unbounded dstnat would publish an
    // administrative UI on every interface the router has, WAN included.
    expose_on_interface: expose.trim() || null,
    ram_high: ramHigh.trim() || null,
  });

  const pending = plan
    ? plan.steps.filter(s => s.action === 'create' || s.action === 'set').length
    : 0;

  const act = async (kind, call, onDone_) => {
    setBusy(kind);
    setError(null);
    setMessage(null);
    try {
      onDone_(await call());
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

  const formatable = disks.filter(d => d.formatable);

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div className="panel-head" style={{ padding: 0 }}>
        <div className="panel-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Wrench size={16} style={{ color: 'var(--color-primary)' }} />
          {t('ctr_setup_title')}
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="btn btn-secondary btn-sm" disabled={busy !== ''}
            title={t('ctr_plan_hint')}
            onClick={() => act('plan', () => api.containerSetupPlan(routerId, payload()), r => setPlan(r?.data || null))}>
            {busy === 'plan' ? t('ctr_working') : t('ctr_plan')}
          </button>
          <button className="btn btn-primary btn-sm"
            disabled={busy !== '' || !plan || !plan.ok || pending === 0}
            title={pending === 0 ? t('ctr_nothing_pending') : t('ctr_apply_hint')}
            onClick={() => act('apply', () => api.containerSetupApply(routerId, payload()),
              r => { setPlan(r?.data || null); setMessage(t('ctr_applied')); onDone && onDone(); })}>
            {busy === 'apply' ? t('ctr_working') : t('ctr_apply')}
            {pending > 0 ? ` (${pending})` : ''}
          </button>
          <button className="btn btn-ghost btn-sm" disabled={busy !== ''} title={t('ctr_refresh')}
            onClick={() => { loadStorage(); setPlan(null); }}>
            <RefreshCw size={13} className={busy === 'storage' ? 'spin' : ''} />
          </button>
        </div>
      </div>

      <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>{t('ctr_setup_hint')}</div>

      {/* Storage, chosen from what the router reports rather than typed blind. */}
      <div className="section-label">{t('ctr_setup_storage')}</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 8 }}>
        {disks.length === 0 && (
          <div className="empty-note">{t('ctr_disk_none')}</div>
        )}
        {disks.map(d => (
          <DiskOption key={d.slot} disk={d} selected={storage.trim() === d.slot} onSelect={setStorage} />
        ))}
      </div>

      {/* The verdict on the chosen slot, including the reason it was refused -
          the plan step says the same thing, but the operator needs it before
          pressing Plan. */}
      {verdict && verdict.problems?.length > 0 && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', color: 'var(--color-danger)', fontSize: 'var(--fs-xs)' }}>
          <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
          <span style={{ overflowWrap: 'anywhere' }}>{verdict.problems.join(' ')}</span>
        </div>
      )}
      {verdict && verdict.ready && verdict.warnings?.length > 0 && (
        <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--color-warning)' }}>{verdict.warnings.join(' ')}</div>
      )}

      {formatable.length > 0 && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 'var(--fs-2xs)' }}>
          {formatTarget ? (
            <>
              <select className="form-select form-control" style={{ width: 'auto' }} value={formatTarget}
                onChange={e => { setFormatTarget(e.target.value); setFormatAck(''); }}>
                {formatable.map(d => <option key={d.slot} value={d.slot}>{d.slot}</option>)}
              </select>
              <select className="form-select form-control" style={{ width: 'auto' }} value={formatFs}
                onChange={e => setFormatFs(e.target.value)}>
                {['ext4', 'fat32', 'exfat', 'xfs', 'btrfs'].map(fs => <option key={fs} value={fs}>{fs}</option>)}
              </select>
              <input className="form-input font-mono" style={{ width: 150 }} value={formatAck}
                placeholder={formatTarget} onChange={e => setFormatAck(e.target.value)} />
              <button className="btn btn-danger btn-sm"
                disabled={busy !== '' || formatAck !== formatTarget}
                title={t('ctr_format_hint')}
                onClick={() => act('format', () => api.containerFormat(routerId, {
                  slot: formatTarget, file_system: formatFs, label: '', confirm: formatAck,
                }), r => {
                  setMessage(r?.message || t('ctr_format_started'));
                  setFormatTarget(null); setFormatAck('');
                  loadStorage();
                })}>
                {busy === 'format' ? t('ctr_working') : t('ctr_format_confirm')}
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => setFormatTarget(null)}>{t('cancel')}</button>
            </>
          ) : (
            <span style={{ color: 'var(--text-muted)' }}>
              {t('ctr_format_available')}
              <button className="btn btn-ghost btn-sm" style={{ marginLeft: 6 }} onClick={() => setFormatTarget(formatable[0].slot)}>
                {t('ctr_format')}
              </button>
            </span>
          )}
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 'var(--fs-xs)' }}>
          {t('ctr_setup_subnet')}
          <input style={{ ...field, width: 130 }} value={subnet} onChange={e => setSubnet(e.target.value)} />
        </label>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 'var(--fs-xs)' }}>
          {t('ctr_setup_expose')}
          <input style={{ ...field, width: 110 }} value={expose} onChange={e => setExpose(e.target.value)}
            placeholder={t('ctr_setup_expose_ph')} />
        </label>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 'var(--fs-xs)' }}
          title={t('ctr_setup_ram_hint')}>
          {t('ctr_setup_ram')}
          <input style={{ ...field, width: 80 }} value={ramHigh} onChange={e => setRamHigh(e.target.value)}
            placeholder="—" />
        </label>
      </div>

      {/* Where the image layers would go today: the reason this panel exists. */}
      <div className="font-mono" style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
        layer-dir: {config?.layer_dir || '(internal flash)'} · tmpdir: {config?.tmpdir || '(internal flash)'}
        {config?.memory_current_bytes ? ` · ${t('ctr_mem_all')}: ${formatSize(config.memory_current_bytes)}` : ''}
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
