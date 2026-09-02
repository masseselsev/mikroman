import React, { useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { RouterConnectionForm } from './RouterConnectionForm';
import { Archive, Trash2, RotateCcw, Repeat, X, Loader2, AlertTriangle } from 'lucide-react';

/**
 * The three things that can happen to a managed router beyond a field edit:
 * delete-with-a-choice, replace-the-hardware, and bring-an-archived-one-back.
 * Kept out of the already-large SettingsModal; it renders these and owns only
 * the API calls.
 */

function Backdrop({ children, onClose }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 460 }}>
        {children}
      </div>
    </div>
  );
}

/**
 * Delete a router: archive (keep everything, re-addable by serial) or purge
 * (erase the router and every user, device, rollup, metric and setting). Purge
 * asks for the router's name typed back, because it cannot be undone.
 */
export function RouterDeleteDialog({ router, busy, onArchive, onPurge, onCancel }) {
  const { t } = useI18n();
  const [confirmText, setConfirmText] = useState('');
  const purgeArmed = confirmText.trim() === router.name;

  return (
    <Backdrop onClose={busy ? undefined : onCancel}>
      <div className="modal-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700 }}>
          <Trash2 size={16} style={{ color: 'var(--color-danger)' }} />
          {t('router_delete_title', { name: router.name })}
        </div>
        <button className="btn-icon" onClick={onCancel} disabled={busy} style={{ width: 28, height: 28 }}>
          <X size={16} />
        </button>
      </div>

      <div style={{ padding: '4px 20px 16px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        <button
          type="button"
          className="panel"
          onClick={() => !busy && onArchive()}
          disabled={busy}
          style={{ textAlign: 'left', cursor: 'pointer', display: 'flex', gap: 10, padding: 12, alignItems: 'flex-start' }}
        >
          <Archive size={18} style={{ color: 'var(--color-primary)', flexShrink: 0, marginTop: 2 }} />
          <span>
            <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)' }}>{t('router_delete_archive')}</div>
            <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginTop: 2 }}>
              {t('router_delete_archive_hint')}
            </div>
          </span>
        </button>

        <div className="panel" style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8, borderColor: 'rgba(239,68,68,0.35)' }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
            <AlertTriangle size={18} style={{ color: 'var(--color-danger)', flexShrink: 0, marginTop: 2 }} />
            <span>
              <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)' }}>{t('router_delete_purge')}</div>
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginTop: 2 }}>
                {t('router_delete_purge_hint')}
              </div>
            </span>
          </div>
          <input
            className="form-input"
            placeholder={t('router_delete_purge_confirm', { name: router.name })}
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            disabled={busy}
          />
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => onPurge()}
            disabled={busy || !purgeArmed}
            style={{ alignSelf: 'flex-start', color: '#fff', background: 'var(--color-danger)', opacity: purgeArmed ? 1 : 0.5 }}
          >
            {busy ? <Loader2 size={13} className="spin" /> : <Trash2 size={13} />}
            {t('router_delete_purge_btn')}
          </button>
        </div>
      </div>
    </Backdrop>
  );
}

/**
 * Replace the physical router behind an existing row. Every user, device,
 * traffic total and per-router setting stays attached. Works with the old
 * router already dead; the new one is tested before the swap commits.
 */
export function ChangeRouterModal({ router, busy, onSubmit, onCancel }) {
  const { t } = useI18n();
  const [historyMode, setHistoryMode] = useState('keep');

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 560 }}>
        <div className="modal-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700 }}>
            <Repeat size={16} style={{ color: 'var(--color-primary)' }} />
            {t('router_change_title', { name: router.name })}
          </div>
          <button className="btn-icon" onClick={onCancel} disabled={busy} style={{ width: 28, height: 28 }}>
            <X size={16} />
          </button>
        </div>

        <div style={{ padding: '4px 20px 6px' }}>
          <p style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', margin: '0 0 10px' }}>
            {t('router_change_hint')}
          </p>

          <div className="panel" style={{ padding: 10, marginBottom: 12 }}>
            <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, marginBottom: 6 }}>
              {t('router_change_history_q')}
            </div>
            <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 'var(--fs-xs)', marginBottom: 6 }}>
              <input type="radio" name="hm" checked={historyMode === 'keep'} onChange={() => setHistoryMode('keep')} />
              <span>{t('router_change_history_keep')}</span>
            </label>
            <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 'var(--fs-xs)' }}>
              <input type="radio" name="hm" checked={historyMode === 'reset_hardware'} onChange={() => setHistoryMode('reset_hardware')} />
              <span>{t('router_change_history_reset')}</span>
            </label>
          </div>
        </div>

        <div style={{ padding: '0 20px 16px' }}>
          <RouterConnectionForm
            mode="create"
            initial={{ name: router.name, host: '', port: 443, use_ssl: true, username: '' }}
            busy={busy}
            onSubmit={(payload) => onSubmit({ ...payload, history_mode: historyMode })}
            onCancel={onCancel}
          />
        </div>
      </div>
    </div>
  );
}

/**
 * Archived routers: the ones deleted with "keep data". Each can be brought
 * back untouched or purged for good.
 */
export function ArchivedRoutersSection({ items, busyId, onRestore, onPurge }) {
  const { t } = useI18n();
  if (!items || items.length === 0) return null;

  return (
    <div style={{ marginTop: 18 }}>
      <div style={{ fontSize: 'var(--fs-xs)', fontWeight: 700, color: 'var(--text-muted)', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
        <Archive size={13} />
        {t('archived_routers_title')} ({items.length})
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {items.map((r) => (
          <div
            key={r.id}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '8px 12px', border: '1px dashed var(--border-color)',
              borderRadius: 'var(--radius-sm)', background: 'var(--bg-secondary)',
            }}
          >
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)' }}>{r.name}</div>
              <div className="font-mono" style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {r.host}:{r.port}{r.serial_number ? ` · ${r.serial_number}` : ''}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => onRestore(r.id)}
                disabled={busyId === r.id}
                style={{ fontSize: 'var(--fs-xs)', padding: '3px 8px', display: 'flex', alignItems: 'center', gap: 4 }}
              >
                {busyId === r.id ? <Loader2 size={12} className="spin" /> : <RotateCcw size={12} />}
                {t('archived_restore')}
              </button>
              <button
                type="button"
                className="btn-icon"
                onClick={() => onPurge(r)}
                disabled={busyId === r.id}
                title={t('router_delete_purge')}
                style={{ color: 'var(--color-danger)', width: 28, height: 28 }}
              >
                <Trash2 size={13} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
