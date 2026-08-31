import React from 'react';
import { useI18n } from '../../context/I18nContext';
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  Hash,
  Key,
  Send,
} from 'lucide-react';

/**
 * Wizard step 2: the optional Telegram bot.
 *
 * Purely a form - nothing is validated against Telegram here, because a bad
 * token should not block finishing setup. The bot reports its own failure on
 * first poll, by which point the operator has a working dashboard to read it in.
 */
export function TelegramStep({ telegramForm, setTelegramForm, onNext, onBack }) {
  const { t } = useI18n();
  const setStep = (n) => (n === 1 ? onBack() : onNext());

  return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <div>
          <h3 style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Send size={18} style={{ color: 'var(--color-primary)' }} />
            {t('wizard_step2')}
          </h3>
          <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', marginTop: 2 }}>
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
            <span className="form-hint">{t('tg_token_hint')}</span>
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
  );
}
