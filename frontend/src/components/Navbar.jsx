import React from 'react';
import { useTheme } from '../context/ThemeContext';
import { useI18n } from '../context/I18nContext';
import { RouterSelector } from './RouterSelector';
import { RouterCommentBar } from './RouterCommentBar';
import { Sun, Moon, Globe, Settings as SettingsIcon, Activity, Clock } from 'lucide-react';

/**
 * Trim a trailing zero patch from a semver string for display.
 *
 * "0.1.0" reads as "0.1" in the header; the full string stays in the tooltip,
 * so a bug report can still quote an exact build.
 */
function formatVersionTag(version) {
  const parts = String(version).split('.');
  if (parts.length === 3 && parts[2] === '0') {
    return `v${parts[0]}.${parts[1]}`;
  }
  return `v${version}`;
}

/**
 * Live clock in the router's own timezone.
 *
 * Every time the dashboard shows - lease ages, billing cycles, daily rollups -
 * is anchored to the router, while the container commonly runs UTC. Showing the
 * router's clock makes it obvious which "today" is meant.
 *
 * The offset arrives with telemetry and the tick happens here, so a live clock
 * costs no additional polling.
 */
function RouterClock({ clock }) {
  const [now, setNow] = React.useState(() => Date.now());

  React.useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  if (!clock || clock.gmt_offset_minutes == null) return null;

  // Shift real UTC by the router's offset, then read the result as UTC.
  const shifted = new Date(now + clock.gmt_offset_minutes * 60000);
  const hh = String(shifted.getUTCHours()).padStart(2, '0');
  const mm = String(shifted.getUTCMinutes()).padStart(2, '0');
  const ss = String(shifted.getUTCSeconds()).padStart(2, '0');

  return (
    <div
      title={`${clock.timezone || 'Router time'}${clock.dst_active ? ' (DST)' : ''}`}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        padding: '0 10px',
        // Same height and radius as the buttons beside it, so the toolbar reads
        // as one row of controls rather than three unrelated widgets.
        height: 'var(--control-h-sm)',
        borderRadius: 'var(--radius-sm)',
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border-color)',
        color: 'var(--text-secondary)',
        whiteSpace: 'nowrap'
      }}
    >
      <Clock size={13} style={{ color: 'var(--color-primary)', flexShrink: 0 }} />
      <span className="font-mono" style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, color: 'var(--text-primary)' }}>
        {hh}:{mm}:{ss}
      </span>
      {clock.timezone && (
        <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
          {clock.timezone.split('/').pop().replace(/_/g, ' ')}
        </span>
      )}
    </div>
  );
}

export function Navbar({ isConnected, routerInfo, routers = [], activeRouter, onSelectRouter, onOpenSettings, onAddRouter, onRouterCommentSaved }) {
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
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
                <span style={{ fontWeight: 800, fontSize: 'var(--fs-xl)', letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>
                  {t('app_title')}
                </span>
                {/* Injected from package.json at build time - see vite.config.js */}
                <span className="version-tag" title={`MikroMan ${__APP_VERSION__}`}>
                  {formatVersionTag(__APP_VERSION__)}
                </span>
              </div>
              <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', marginTop: -2 }}>
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

          {/* Only the bad news is worth a badge. A healthy stream is already
              signalled by the router selector's own green dot, so saying it
              twice spends space on the state nobody needs telling about. */}
          {!isConnected && (
            <div className="badge badge-danger" style={{ marginLeft: 4 }}>
              <span style={{ width: 6, height: 6, borderRadius: 'var(--radius-full)', background: 'var(--color-danger)' }} />
              {t('disconnected')}
            </div>
          )}
        </div>

        {/* Selected router's note - between the selector and the clock, grows to
            fill the middle of the bar and drops a full editor down on click. */}
        {activeRouter && (
          <RouterCommentBar router={activeRouter} onSaved={onRouterCommentSaved} />
        )}

        {/* Action Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {/* Router's own local time, left of the language switcher */}
          <RouterClock clock={routerInfo?.clock} />

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
