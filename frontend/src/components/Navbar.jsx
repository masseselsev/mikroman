import React from 'react';
import { useTheme } from '../context/ThemeContext';
import { useI18n } from '../context/I18nContext';
import { RouterSelector } from './RouterSelector';
import { Sun, Moon, Globe, Settings as SettingsIcon, Activity } from 'lucide-react';

export function Navbar({ isConnected, routerInfo, routers = [], activeRouter, onSelectRouter, onOpenSettings, onAddRouter }) {
  const { theme, toggleTheme } = useTheme();
  const { lang, setLang, t } = useI18n();

  return (
    <header style={{
      background: 'var(--bg-secondary)',
      borderBottom: '1px solid var(--border-color)',
      padding: '12px 24px',
      position: 'sticky',
      top: 0,
      zIndex: 100,
      boxShadow: 'var(--shadow-sm)'
    }}>
      <div style={{
        maxWidth: 1360,
        margin: '0 auto',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: 12
      }}>
        {/* Brand & Connection Pill & Router Selector */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            cursor: 'pointer'
          }}>
            <div style={{
              background: 'var(--color-primary)',
              color: '#fff',
              padding: '6px 8px',
              borderRadius: 'var(--radius-md)',
              display: 'flex',
              alignItems: 'center',
              boxShadow: '0 2px 10px var(--color-primary-glow)'
            }}>
              <Activity size={20} />
            </div>
            <div>
              <div style={{ fontWeight: 800, fontSize: '1.15rem', letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>
                {t('app_title')}
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: -2 }}>
                {routerInfo?.board_name ? `${routerInfo.board_name} (${routerInfo.version || 'ROS 7.x'})` : t('app_subtitle')}
              </div>
            </div>
          </div>

          {/* Router Selector Dropdown */}
          <RouterSelector
            routers={routers}
            activeRouter={activeRouter}
            onSelectRouter={onSelectRouter}
            onAddRouter={onAddRouter}
          />

          <div className={`badge ${isConnected ? 'badge-success' : 'badge-danger'}`} style={{ marginLeft: 4 }}>
            <span className={isConnected ? "live-indicator" : ""} style={{ width: 6, height: 6, borderRadius: '50%', background: isConnected ? 'var(--color-success)' : 'var(--color-danger)' }}></span>
            {isConnected ? t('connected') : t('disconnected')}
          </div>
        </div>

        {/* Action Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {/* Language Switcher */}
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => setLang(lang === 'en' ? 'ru' : 'en')}
            title="Switch Language (EN / RU)"
            style={{ fontWeight: 700 }}
          >
            <Globe size={15} />
            {lang.toUpperCase()}
          </button>

          {/* Theme Toggle */}
          <button
            className="btn-icon"
            onClick={toggleTheme}
            title={theme === 'dark' ? 'Switch to Light Theme' : 'Switch to Dark Theme'}
            style={{ width: 36, height: 36 }}
          >
            {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
          </button>

          {/* Settings Trigger */}
          <button
            className="btn btn-secondary btn-sm"
            onClick={onOpenSettings}
            title={t('tab_settings')}
          >
            <SettingsIcon size={16} />
            <span className="hide-mobile">{t('tab_settings')}</span>
          </button>
        </div>
      </div>
    </header>
  );
}
