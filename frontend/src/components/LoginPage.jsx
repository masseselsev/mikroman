import React, { useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { useTheme } from '../context/ThemeContext';
import { useAuth } from '../context/AuthContext';
import { Shield, Lock, KeyRound, Eye, EyeOff, AlertCircle, Loader2, Globe, Sun, Moon } from 'lucide-react';

export function LoginPage() {
  const { t, lang, setLang } = useI18n();
  const { theme, toggleTheme } = useTheme();
  const { needsSetup, login, setup } = useAuth();

  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [error, setError] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);

    if (needsSetup) {
      if (password.length < 8) {
        setError(t('auth_password_min_length'));
        return;
      }
      if (password !== confirmPassword) {
        setError(t('auth_passwords_dont_match'));
        return;
      }
    } else {
      if (!password) {
        return;
      }
    }

    setIsSubmitting(true);
    try {
      if (needsSetup) {
        await setup(password);
      } else {
        await login(password);
      }
    } catch (err) {
      setError(err.message || t('auth_invalid_credentials'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg-primary)',
        padding: 16,
        position: 'relative',
        userSelect: 'none',
      }}
    >
      {/* Top right language switch and theme toggle */}
      <div style={{ position: 'absolute', top: 16, right: 16, display: 'flex', gap: 6, zIndex: 2 }}>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          id="auth-lang-toggle"
          onClick={() => setLang(lang === 'en' ? 'ru' : 'en')}
          style={{ fontWeight: 700, height: 32, gap: 6 }}
          title="Switch language (EN / RU)"
        >
          <Globe size={14} />
          {lang.toUpperCase()}
        </button>
        <button
          type="button"
          className="btn-icon"
          onClick={toggleTheme}
          style={{ width: 32, height: 32 }}
          title={theme === 'dark' ? 'Switch to Light Theme' : 'Switch to Dark Theme'}
        >
          {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
        </button>
      </div>

      {/* Main card */}
      <div
        style={{
          width: '100%',
          maxWidth: 420,
          background: 'var(--bg-card)',
          border: '1px solid var(--border-color)',
          borderRadius: 'var(--radius-xl)',
          padding: '36px 30px',
          position: 'relative',
          boxShadow: '0 24px 64px rgba(0, 0, 0, 0.55)',
        }}
      >
        {/* Brand Icon */}
        <div
          style={{
            width: 56,
            height: 56,
            borderRadius: 'var(--radius-lg)',
            background: 'linear-gradient(135deg, rgba(11, 114, 201, 0.25) 0%, rgba(30, 135, 227, 0.1) 100%)',
            border: '1px solid rgba(11, 114, 201, 0.3)',
            color: 'var(--color-primary)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 16px auto',
            boxShadow: '0 8px 20px var(--color-primary-glow)',
          }}
        >
          {needsSetup ? <KeyRound size={26} /> : <Shield size={26} />}
        </div>

        {/* Header */}
        <h1
          style={{
            fontSize: 'var(--fs-xl)',
            fontWeight: 800,
            textAlign: 'center',
            letterSpacing: '-0.02em',
            color: 'var(--text-primary)',
            margin: '0 0 6px 0',
          }}
        >
          {needsSetup ? t('auth_setup_title') : t('auth_login_title')}
        </h1>
        <p
          style={{
            fontSize: 'var(--fs-sm)',
            color: 'var(--text-secondary)',
            textAlign: 'center',
            margin: '0 0 24px 0',
            lineHeight: 1.4,
          }}
        >
          {needsSetup ? t('auth_setup_subtitle') : t('auth_login_subtitle')}
        </p>

        {/* Error Alert */}
        {error && (
          <div
            id="auth-error-alert"
            role="alert"
            style={{
              background: 'var(--color-danger-bg)',
              border: '1px solid var(--color-danger)',
              color: 'var(--color-danger)',
              borderRadius: 'var(--radius-md)',
              padding: '10px 14px',
              marginBottom: 20,
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              fontSize: 'var(--fs-sm)',
              lineHeight: 1.4,
            }}
          >
            <AlertCircle size={16} style={{ flexShrink: 0 }} />
            <span>{error}</span>
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: 16 }}>
            <label
              htmlFor="auth-password"
              style={{
                display: 'block',
                fontSize: 'var(--fs-xs)',
                fontWeight: 600,
                color: 'var(--text-secondary)',
                marginBottom: 6,
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
              }}
            >
              {t('auth_password_label')}
            </label>
            <div style={{ position: 'relative' }}>
              <input
                id="auth-password"
                type={showPassword ? 'text' : 'password'}
                autoFocus
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={t('auth_password_placeholder')}
                disabled={isSubmitting}
                className="form-control"
                style={{
                  width: '100%',
                  height: 'var(--control-h)',
                  paddingRight: 40,
                }}
              />
              <button
                type="button"
                id="auth-toggle-pwd-visibility"
                onClick={() => setShowPassword(!showPassword)}
                style={{
                  position: 'absolute',
                  right: 10,
                  top: '50%',
                  transform: 'translateY(-50%)',
                  background: 'none',
                  border: 'none',
                  color: 'var(--text-muted)',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  padding: 4,
                }}
                tabIndex={-1}
                aria-label="Toggle password visibility"
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          {/* Setup Mode: Confirm Password */}
          {needsSetup && (
            <div style={{ marginBottom: 16 }}>
              <label
                htmlFor="auth-confirm-password"
                style={{
                  display: 'block',
                  fontSize: 'var(--fs-xs)',
                  fontWeight: 600,
                  color: 'var(--text-secondary)',
                  marginBottom: 6,
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                }}
              >
                {t('auth_confirm_password_label')}
              </label>
              <div style={{ position: 'relative' }}>
                <input
                  id="auth-confirm-password"
                  type={showConfirm ? 'text' : 'password'}
                  required
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder={t('auth_confirm_password_placeholder')}
                  disabled={isSubmitting}
                  className="form-control"
                  style={{
                    width: '100%',
                    height: 'var(--control-h)',
                    paddingRight: 40,
                  }}
                />
                <button
                  type="button"
                  id="auth-toggle-confirm-visibility"
                  onClick={() => setShowConfirm(!showConfirm)}
                  style={{
                    position: 'absolute',
                    right: 10,
                    top: '50%',
                    transform: 'translateY(-50%)',
                    background: 'none',
                    border: 'none',
                    color: 'var(--text-muted)',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    padding: 4,
                  }}
                  tabIndex={-1}
                  aria-label="Toggle confirm password visibility"
                >
                  {showConfirm ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
              <p
                style={{
                  fontSize: 'var(--fs-2xs)',
                  color: 'var(--text-muted)',
                  marginTop: 6,
                  marginBottom: 0,
                }}
              >
                {t('auth_password_min_length')}
              </p>
            </div>
          )}

          {/* Submit Button */}
          <button
            type="submit"
            id="auth-submit-btn"
            disabled={isSubmitting || !password || (needsSetup && !confirmPassword)}
            className="btn btn-primary"
            style={{
              width: '100%',
              height: 'var(--control-h)',
              marginTop: 20,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 8,
              fontWeight: 600,
              fontSize: 'var(--fs-md)',
            }}
          >
            {isSubmitting ? (
              <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} />
            ) : needsSetup ? (
              <>
                <KeyRound size={16} />
                <span>{t('auth_setup_btn')}</span>
              </>
            ) : (
              <>
                <Lock size={16} />
                <span>{t('auth_login_btn')}</span>
              </>
            )}
          </button>
        </form>

        {/* Footer info */}
        <div
          style={{
            marginTop: 24,
            paddingTop: 16,
            borderTop: '1px solid var(--border-subtle)',
            textAlign: 'center',
            fontSize: 'var(--fs-xs)',
            color: 'var(--text-muted)',
          }}
        >
          MikroMan &middot; RouterOS Companion
        </div>
      </div>
    </div>
  );
}
