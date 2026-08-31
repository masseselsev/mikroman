import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { useI18n } from '../context/I18nContext';
import {
  Container as ContainerIcon,
  Play,
  Square,
  Trash2,
  RefreshCw,
  Plus,
  AlertTriangle,
  X,
  Check,
} from 'lucide-react';

/**
 * RouterOS container management for the selected router.
 *
 * Container support is an optional package that a stock install does not carry
 * and cannot enable without a reboot. The page is built to degrade gracefully:
 * the API always returns a `support` block, and when the feature is not ready
 * this shows an explanatory banner with every control disabled, rather than an
 * error. Once the package is installed and enabled, the same page drives it.
 */

const STATUS_TONE = {
  running: 'is-ok',
  stopped: 'is-idle',
  error: 'is-bad',
  extracting: 'is-busy',
};

function StatusPill({ status }) {
  const tone = STATUS_TONE[status] || 'is-idle';
  return <span className={`ctr-status ${tone}`}>{status || '—'}</span>;
}

function AddContainerForm({ mounts, envs, disabled, busy, onSubmit, onCancel }) {
  const { t } = useI18n();
  const [form, setForm] = useState({
    remote_image: '',
    interface: '',
    root_dir: '',
    hostname: '',
    cmd: '',
    entrypoint: '',
    mounts: '',
    envlist: '',
    start_on_boot: false,
    logging: true,
    comment: '',
  });
  const set = (patch) => setForm(f => ({ ...f, ...patch }));
  const canSubmit = form.remote_image.trim() && form.interface.trim() && !disabled && !busy;

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <strong style={{ fontSize: 'var(--fs-md)' }}>{t('ctr_add_title')}</strong>
        <button className="btn-icon" style={{ width: 24, height: 24 }} onClick={onCancel} title={t('cancel')}>
          <X size={14} />
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
        <label className="ctr-field">
          <span>{t('ctr_image')} *</span>
          <input className="form-input font-mono" placeholder="library/nginx:alpine"
            value={form.remote_image} onChange={e => set({ remote_image: e.target.value })} disabled={disabled} />
        </label>
        <label className="ctr-field">
          <span>{t('ctr_interface')} *</span>
          <input className="form-input font-mono" placeholder="veth1"
            value={form.interface} onChange={e => set({ interface: e.target.value })} disabled={disabled} />
        </label>
        <label className="ctr-field">
          <span>{t('ctr_root_dir')}</span>
          <input className="form-input font-mono" placeholder="usb1/nginx"
            value={form.root_dir} onChange={e => set({ root_dir: e.target.value })} disabled={disabled} />
        </label>
        <label className="ctr-field">
          <span>{t('ctr_hostname')}</span>
          <input className="form-input font-mono"
            value={form.hostname} onChange={e => set({ hostname: e.target.value })} disabled={disabled} />
        </label>
        <label className="ctr-field">
          <span>{t('ctr_cmd')}</span>
          <input className="form-input font-mono"
            value={form.cmd} onChange={e => set({ cmd: e.target.value })} disabled={disabled} />
        </label>
        <label className="ctr-field">
          <span>{t('ctr_entrypoint')}</span>
          <input className="form-input font-mono"
            value={form.entrypoint} onChange={e => set({ entrypoint: e.target.value })} disabled={disabled} />
        </label>
        <label className="ctr-field">
          <span>{t('ctr_mounts')}</span>
          <select className="form-select" value={form.mounts}
            onChange={e => set({ mounts: e.target.value })} disabled={disabled}>
            <option value="">—</option>
            {mounts.map(m => <option key={m.id} value={m.name}>{m.name} ({m.src} → {m.dst})</option>)}
          </select>
        </label>
        <label className="ctr-field">
          <span>{t('ctr_envlist')}</span>
          <select className="form-select" value={form.envlist}
            onChange={e => set({ envlist: e.target.value })} disabled={disabled}>
            <option value="">—</option>
            {[...new Set(envs.map(v => v.name).filter(Boolean))].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label className="ctr-field">
          <span>{t('ctr_comment')}</span>
          <input className="form-input"
            value={form.comment} onChange={e => set({ comment: e.target.value })} disabled={disabled} />
        </label>
      </div>

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-secondary)' }}>
          <input type="checkbox" checked={form.start_on_boot}
            onChange={e => set({ start_on_boot: e.target.checked })} disabled={disabled} />
          {t('ctr_start_on_boot')}
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 'var(--fs-sm)', color: 'var(--text-secondary)' }}>
          <input type="checkbox" checked={form.logging}
            onChange={e => set({ logging: e.target.checked })} disabled={disabled} />
          {t('ctr_logging')}
        </label>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
        <button className="btn btn-ghost btn-sm" onClick={onCancel}>{t('cancel')}</button>
        <button className="btn btn-primary btn-sm" disabled={!canSubmit}
          onClick={() => onSubmit(form)}>
          <Check size={13} /> {t('ctr_create')}
        </button>
      </div>
    </div>
  );
}

export function ContainersPage({ activeRouter }) {
  const { t } = useI18n();
  const routerId = activeRouter?.id;
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [showAdd, setShowAdd] = useState(false);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    if (!routerId) { setData(null); return; }
    setLoading(true);
    setError(null);
    try {
      const res = await api.getContainers(routerId);
      setData(res?.data || null);
    } catch (e) {
      setError(e.message || String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [routerId]);

  useEffect(() => { load(); }, [load]);

  const support = data?.support || { status: 'unreachable', message: null };
  const ready = support.status === 'ready';
  const containers = data?.containers || [];
  const mounts = data?.mounts || [];
  const envs = data?.envs || [];
  const config = data?.config || {};

  const runAction = async (id, action) => {
    if (action === 'remove' && !window.confirm(t('ctr_confirm_remove'))) return;
    setBusyId(id + action);
    setError(null);
    try {
      await api.containerAction(routerId, id, action);
      await load();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusyId(null);
    }
  };

  const submitCreate = async (form) => {
    setCreating(true);
    setError(null);
    try {
      await api.createContainer(routerId, form);
      setShowAdd(false);
      await load();
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setCreating(false);
    }
  };

  if (!routerId) {
    return <div className="card" style={{ color: 'var(--text-muted)' }}>{t('ctr_no_router')}</div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <ContainerIcon size={22} style={{ color: 'var(--color-primary)' }} />
        <h2 style={{ fontSize: 'var(--fs-xl)', fontWeight: 700 }}>{t('ctr_title')}</h2>
        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>{activeRouter?.name}</span>
        <span style={{ flex: 1 }} />
        <button className="btn btn-secondary btn-sm" onClick={load} disabled={loading}>
          <RefreshCw size={14} style={loading ? { animation: 'spin 1s linear infinite' } : undefined} />
          {t('ctr_refresh')}
        </button>
        <button className="btn btn-primary btn-sm" disabled={!ready} onClick={() => setShowAdd(v => !v)}>
          <Plus size={14} /> {t('ctr_add')}
        </button>
      </div>

      {support.status !== 'ready' && (
        <div className="card" style={{
          display: 'flex', gap: 12, alignItems: 'flex-start',
          borderLeft: `3px solid ${support.status === 'unreachable' ? 'var(--color-danger)' : 'var(--color-warning)'}`,
        }}>
          <AlertTriangle size={18} style={{ flexShrink: 0, marginTop: 2, color: 'var(--color-warning)' }} />
          <div>
            <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)' }}>
              {support.status === 'not_installed' && t('ctr_not_installed')}
              {support.status === 'disabled' && t('ctr_disabled')}
              {support.status === 'unreachable' && t('ctr_unreachable')}
            </div>
            <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', marginTop: 3 }}>
              {support.message}
            </div>
          </div>
        </div>
      )}

      {error && (
        <div className="alert alert-danger">{error}</div>
      )}

      {showAdd && (
        <AddContainerForm
          mounts={mounts}
          envs={envs}
          disabled={!ready}
          busy={creating}
          onSubmit={submitCreate}
          onCancel={() => setShowAdd(false)}
        />
      )}

      <div className="card panel-flush" style={{ overflowX: 'auto' }}>
        <table className="table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th>{t('ctr_col_name')}</th>
              <th>{t('ctr_col_status')}</th>
              <th>{t('ctr_col_arch')}</th>
              <th>{t('ctr_col_iface')}</th>
              <th>{t('ctr_col_root')}</th>
              <th>{t('ctr_col_boot')}</th>
              <th style={{ textAlign: 'right' }}>{t('ctr_col_actions')}</th>
            </tr>
          </thead>
          <tbody>
            {containers.length === 0 && (
              <tr>
                <td colSpan={7} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 20 }}>
                  {ready ? t('ctr_none') : t('ctr_unavailable_short')}
                </td>
              </tr>
            )}
            {containers.map(c => {
              const isRunning = c.status === 'running';
              return (
                <tr key={c.id}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{c.name || c.id}</div>
                    <div className="font-mono" style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>{c.tag}</div>
                  </td>
                  <td><StatusPill status={c.status} /></td>
                  <td className="font-mono" style={{ fontSize: 'var(--fs-2xs)' }}>{c.arch || '—'}</td>
                  <td className="font-mono" style={{ fontSize: 'var(--fs-2xs)' }}>{c.interface || '—'}</td>
                  <td className="font-mono" style={{ fontSize: 'var(--fs-2xs)' }}>{c.root_dir || '—'}</td>
                  <td>{c.start_on_boot ? t('yes') : t('no')}</td>
                  <td>
                    <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                      <button className="btn-icon" style={{ width: 26, height: 26 }} title={t('ctr_start')}
                        disabled={!ready || isRunning || busyId === c.id + 'start'}
                        onClick={() => runAction(c.id, 'start')}>
                        <Play size={13} />
                      </button>
                      <button className="btn-icon" style={{ width: 26, height: 26 }} title={t('ctr_stop')}
                        disabled={!ready || !isRunning || busyId === c.id + 'stop'}
                        onClick={() => runAction(c.id, 'stop')}>
                        <Square size={13} />
                      </button>
                      <button className="btn-icon" style={{ width: 26, height: 26, color: 'var(--color-danger)' }} title={t('ctr_remove')}
                        disabled={!ready || busyId === c.id + 'remove'}
                        onClick={() => runAction(c.id, 'remove')}>
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Reference panels: the router's container config, and the mount / env
          definitions a new container can attach by name. */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14 }}>
        <div className="card">
          <strong style={{ fontSize: 'var(--fs-sm)' }}>{t('ctr_config')}</strong>
          <dl className="ctr-dl">
            <div><dt>registry-url</dt><dd className="font-mono">{config.registry_url || '—'}</dd></div>
            <div><dt>tmpdir</dt><dd className="font-mono">{config.tmpdir || '—'}</dd></div>
            <div><dt>layer-dir</dt><dd className="font-mono">{config.layer_dir || '—'}</dd></div>
            <div><dt>ram-high</dt><dd className="font-mono">{config.ram_high || '—'}</dd></div>
          </dl>
        </div>
        <div className="card">
          <strong style={{ fontSize: 'var(--fs-sm)' }}>{t('ctr_mounts')} ({mounts.length})</strong>
          <ul className="ctr-list">
            {mounts.length === 0 && <li style={{ color: 'var(--text-muted)' }}>—</li>}
            {mounts.map(m => <li key={m.id} className="font-mono">{m.name}: {m.src} → {m.dst}</li>)}
          </ul>
        </div>
        <div className="card">
          <strong style={{ fontSize: 'var(--fs-sm)' }}>{t('ctr_envs')} ({envs.length})</strong>
          <ul className="ctr-list">
            {envs.length === 0 && <li style={{ color: 'var(--text-muted)' }}>—</li>}
            {envs.map(v => <li key={v.id} className="font-mono">{v.name} · {v.key}={v.value}</li>)}
          </ul>
        </div>
      </div>
    </div>
  );
}
