import React, { useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { useTheme } from '../context/ThemeContext';
import { api } from '../api/client';
import {
  AlertCircle,
  Globe,
  Moon,
  Server,
  Sun,
} from 'lucide-react';

import { RouterStep } from './wizard/RouterStep';
import { TelegramStep } from './wizard/TelegramStep';
import { PreferencesStep } from './wizard/PreferencesStep';

export function SetupWizard({ onComplete }) {
  const { t, lang, setLang } = useI18n();
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
    // Blank rather than "admin": see SettingsModal. The wizard must not probe
    // the router with a username nobody typed.
    username: '',
    password: ''
  });

  // Telegram fields
  const [telegramForm, setTelegramForm] = useState({
    bot_token: '',
    admin_ids: '',
    mode: 'polling'
  });

  // `saving` and `error` stay here: finishing is the shell's job. Everything
  // else the connection step needed - test results, certificates, uploads -
  // moved into RouterStep with the markup that uses it.
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

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
          borderRadius: 'var(--radius-xl)',
          padding: '32px 28px',
          position: 'relative',
          boxShadow: '0 24px 64px rgba(0, 0, 0, 0.55)'
        }}
      >
        {/* Appearance and language controls. The wizard is shown before any
            router exists, so without these the first-run screen could not be
            switched out of the default theme. */}
        <div style={{ position: 'absolute', top: 16, right: 16, display: 'flex', gap: 6, zIndex: 1 }}>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => setLang(lang === 'en' ? 'ru' : 'en')}
            title="Switch Language (EN / RU)"
            style={{ fontWeight: 700 }}
          >
            <Globe size={14} />
            {lang.toUpperCase()}
          </button>
          <button
            type="button"
            className="btn-icon"
            onClick={toggleTheme}
            title={theme === 'dark' ? 'Switch to Light Theme' : 'Switch to Dark Theme'}
            style={{ width: 32, height: 32 }}
          >
            {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
          </button>
        </div>

        {/* Header */}
        <div style={{ textAlign: 'center', marginBottom: 22 }}>
          <div
            style={{
              width: 52,
              height: 52,
              borderRadius: 'var(--radius-lg)',
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
          <h2 style={{ fontSize: 'var(--fs-2xl)', fontWeight: 800, letterSpacing: '-0.02em' }}>
            {t('wizard_title')}
          </h2>
          <p style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-secondary)', marginTop: 4, lineHeight: 1.4 }}>
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
                  borderRadius: 'var(--radius-xs)',
                  background: step >= s.num ? 'var(--color-primary)' : 'var(--bg-secondary)',
                  transition: 'background 0.3s ease',
                  marginBottom: 6
                }}
              />
              <div
                style={{
                  fontSize: 'var(--fs-xs)',
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
              borderRadius: 'var(--radius-md)',
              background: 'rgba(239, 68, 68, 0.12)',
              border: '1px solid var(--color-danger)',
              color: 'var(--color-danger)',
              fontSize: 'var(--fs-sm)',
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

        {step === 1 && (
          <RouterStep
            routerForm={routerForm}
            setRouterForm={setRouterForm}
            onNext={() => setStep(2)}
          />
        )}

        {step === 2 && (
          <TelegramStep
            telegramForm={telegramForm}
            setTelegramForm={setTelegramForm}
            onNext={() => setStep(3)}
            onBack={() => setStep(1)}
          />
        )}

        {step === 3 && (
          <PreferencesStep saving={saving} onFinish={handleFinish} onBack={() => setStep(2)} />
        )}
      </div>
    </div>
  );
}
