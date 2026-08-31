import React from 'react';
import { useI18n } from '../../context/I18nContext';
import { useTheme } from '../../context/ThemeContext';
import {
  ArrowLeft,
  Loader2,
  Moon,
  Sparkles,
  Sun,
} from 'lucide-react';

/**
 * Wizard step 3: language and theme, then finish.
 *
 * Both settings apply immediately rather than on save, so the operator sees the
 * result before committing to it - and both are trivially changeable later in
 * Settings, so nothing here is worth blocking on.
 */
export function PreferencesStep({ saving, onFinish, onBack }) {
  const { t, lang, setLang } = useI18n();
  const { theme, toggleTheme } = useTheme();
  const setStep = () => onBack();
  const handleFinish = onFinish;

  return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
        <div>
          <h3 style={{ fontSize: 'var(--fs-lg)', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Sparkles size={18} style={{ color: 'var(--color-primary)' }} />
            {t('wizard_step3')}
          </h3>
          <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', marginTop: 2 }}>
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
                style={{ padding: '14px 16px', fontSize: 'var(--fs-md)' }}
                onClick={() => setLang('en')}
              >
                🇬🇧 English
              </button>
              <button
                type="button"
                className={`btn ${lang === 'ru' ? 'btn-primary' : 'btn-secondary'}`}
                style={{ padding: '14px 16px', fontSize: 'var(--fs-md)' }}
                onClick={() => setLang('ru')}
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
  );
}
