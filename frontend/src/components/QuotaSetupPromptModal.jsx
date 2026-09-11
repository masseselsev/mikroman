import React from 'react';
import { useI18n } from '../context/I18nContext';
import { Gauge, X, ArrowRight, ShieldAlert } from 'lucide-react';

export function QuotaSetupPromptModal({ isOpen, router, onConfigure, onDismiss }) {
  const { t } = useI18n();

  if (!isOpen || !router) return null;

  const handleDismiss = () => {
    try {
      localStorage.setItem(`mikroman:quota-prompt-dismissed-${router.id}`, 'true');
    } catch {
      // ignore
    }
    onDismiss();
  };

  const handleConfigure = () => {
    try {
      localStorage.setItem(`mikroman:quota-prompt-dismissed-${router.id}`, 'true');
    } catch {
      // ignore
    }
    onConfigure();
  };

  return (
    <div className="modal-backdrop" onClick={handleDismiss}>
      <div
        className="modal-card"
        onClick={e => e.stopPropagation()}
        style={{ maxWidth: 460, padding: 0, overflow: 'hidden' }}
      >
        <div style={{
          padding: '16px 20px',
          background: 'var(--bg-secondary)',
          borderBottom: '1px solid var(--border-color)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              width: 32,
              height: 32,
              borderRadius: '50%',
              background: 'var(--color-primary-light)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--color-primary)'
            }}>
              <Gauge size={18} />
            </div>
            <div>
              <div style={{ fontWeight: 700, fontSize: 'var(--fs-sm)' }}>
                {t('quota_setup_prompt_title') || 'ISP Traffic Quota'}
              </div>
              <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
                {router.name || router.host}
              </div>
            </div>
          </div>
          <button className="btn-icon" onClick={handleDismiss} style={{ width: 28, height: 28 }}>
            <X size={16} />
          </button>
        </div>

        <div style={{ padding: '20px' }}>
          <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-primary)', lineHeight: 1.5, margin: 0 }}>
            {t('quota_setup_prompt_desc', { router: router.name || router.host })}
          </p>

          <div style={{
            marginTop: 16,
            padding: '10px 12px',
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border-color)',
            borderRadius: 'var(--radius-sm)',
            fontSize: 'var(--fs-xs)',
            color: 'var(--text-muted)',
            display: 'flex',
            alignItems: 'center',
            gap: 8
          }}>
            <ShieldAlert size={16} style={{ color: 'var(--color-primary)', flexShrink: 0 }} />
            <span>
              {t('quota_desc')}
            </span>
          </div>
        </div>

        <div style={{
          padding: '12px 20px',
          background: 'var(--bg-secondary)',
          borderTop: '1px solid var(--border-color)',
          display: 'flex',
          justifyContent: 'flex-end',
          gap: 8
        }}>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleDismiss}
          >
            {t('quota_unmetered_btn')}
          </button>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={handleConfigure}
            style={{ display: 'flex', alignItems: 'center', gap: 6 }}
          >
            {t('quota_setup_btn')}
            <ArrowRight size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
