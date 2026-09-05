import React from 'react';
import { useTheme } from '../context/ThemeContext';
import { useI18n } from '../context/I18nContext';
import { RouterSelector } from './RouterSelector';
import { RouterCommentBar } from './RouterCommentBar';
import { Sun, Moon, Settings as SettingsIcon, Activity, Clock, Terminal, Archive, Package } from 'lucide-react';

/**
 * "+5" / "-3:30" from a GMT offset in minutes. The city name that used to sit
 * inline (`Tashkent`) doesn't say anything a reader can act on faster than the
 * offset itself, and it was often wider than the clock next to it; the full
 * zone name is one hover away in the tooltip instead.
 */
function formatUtcOffset(totalMinutes) {
  const sign = totalMinutes < 0 ? '-' : '+';
  const abs = Math.abs(totalMinutes);
  const hours = Math.floor(abs / 60);
  const mins = abs % 60;
  return mins === 0 ? `${sign}${hours}` : `${sign}${hours}:${String(mins).padStart(2, '0')}`;
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
    // Only minutes are shown, so a tick anywhere within the minute is enough;
    // no point re-rendering every second for a digit nobody sees.
    const id = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(id);
  }, []);

  if (!clock || clock.gmt_offset_minutes == null) return null;

  // Shift real UTC by the router's offset, then read the result as UTC.
  const shifted = new Date(now + clock.gmt_offset_minutes * 60000);
  const hh = String(shifted.getUTCHours()).padStart(2, '0');
  const mm = String(shifted.getUTCMinutes()).padStart(2, '0');

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
        {hh}:{mm}
      </span>
      <span className="font-mono" style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
        {formatUtcOffset(clock.gmt_offset_minutes)}
      </span>
    </div>
  );
}

/**
 * Flag icons for the language switcher, drawn as plain SVG shapes rather than
 * the flag emoji (🇬🇧/🇷🇺) tried first. Regional-indicator emoji need a
 * colour-emoji font with flag glyphs; several common Linux and Windows setups
 * lack one and fall back to rendering the two bare letters ("GB", "RU")
 * instead of a flag, which is what actually shipped. An inline SVG renders
 * identically everywhere a browser can render SVG at all.
 */
function FlagGB({ size = 16 }) {
  return (
    <svg width={size} height={Math.round(size * (2 / 3))} viewBox="0 0 60 40" aria-hidden="true">
      <clipPath id="mm-flag-gb-clip"><rect width="60" height="40" /></clipPath>
      <g clipPath="url(#mm-flag-gb-clip)">
        <rect width="60" height="40" fill="#00247d" />
        <line x1="0" y1="0" x2="60" y2="40" stroke="#fff" strokeWidth="10" />
        <line x1="60" y1="0" x2="0" y2="40" stroke="#fff" strokeWidth="10" />
        <line x1="0" y1="0" x2="60" y2="40" stroke="#cf142b" strokeWidth="4" />
        <line x1="60" y1="0" x2="0" y2="40" stroke="#cf142b" strokeWidth="4" />
        <rect x="24" width="12" height="40" fill="#fff" />
        <rect y="14" width="60" height="12" fill="#fff" />
        <rect x="27" width="6" height="40" fill="#cf142b" />
        <rect y="17" width="60" height="6" fill="#cf142b" />
      </g>
    </svg>
  );
}

function FlagRU({ size = 16 }) {
  return (
    <svg width={size} height={Math.round(size * (2 / 3))} viewBox="0 0 60 40" aria-hidden="true">
      <rect width="60" height="40" fill="#fff" />
      <rect y="13" width="60" height="27" fill="#0039a6" />
      <rect y="26" width="60" height="14" fill="#d52b1e" />
    </svg>
  );
}

export function Navbar({
  isConnected,
  routerInfo,
  routers = [],
  activeRouter,
  onSelectRouter,
  onOpenSettings,
  onAddRouter,
  onRouterCommentSaved,
  onOpenLogs,
  onOpenBackups,
  onOpenFirmware,
  hasFirmwareUpdate,
  firmwareVersion,
  logErrorCount = 0,
  logWarningCount = 0,
  logAuthFailCount = 0,
}) {
  const { theme, toggleTheme } = useTheme();
  const { t, lang, setLang } = useI18n();

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
      <div className="navbar-row">
        {/* Brand, router selector and the tools that act on that router */}
        <div className="navbar-left">
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
            {/* `brand-title-block` reserves a line below the title and clips
                the tagline to whatever width the title itself renders at -
                see the CSS comment on `.brand-subtitle--clipped` for why a
                fixed font-size alone was not enough in either language. */}
            <div className="brand-title-block">
              <div style={{ fontWeight: 800, fontSize: 'var(--fs-xl)', letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>
                {t('app_title')}
              </div>
              {/* A fixed tagline, not the router's board and firmware: those
                  belong to whichever router is selected, so they live in the
                  selector's own list where switching context makes them
                  meaningful. The app version moved to the footer. */}
              <div className="brand-subtitle brand-subtitle--clipped">
                {t('app_subtitle')}
              </div>
            </div>
          </div>

          {/* Router Selector Dropdown */}
          <RouterSelector
            routers={routers}
            activeRouter={activeRouter}
            telemetryLive={isConnected}
            currentVersion={routerInfo?.version}
            onSelectRouter={onSelectRouter}
            onAddRouter={onAddRouter}
          />

          {/* Router tools, grouped against the selector they act on: all three
              are "this router's" data, unlike the app-wide controls on the far
              right. One segmented pill rather than four loose buttons, which is
              what the top bar had grown into. */}
          <div className="navbar-tools">
            {[
              {
                key: 'logs',
                Icon: Terminal,
                label: t('tab_logs'),
                onClick: onOpenLogs,
                title: t('router_logs_title'),
                badge: logErrorCount > 0 ? (logErrorCount > 99 ? '99+' : String(logErrorCount)) : null,
                badgeColor: 'var(--color-danger)',
                badgeTitle: t('log_stats_summary', {
                  errors: logErrorCount, warnings: logWarningCount, auth: logAuthFailCount,
                }),
              },
              {
                key: 'backups',
                Icon: Archive,
                label: t('tab_backups'),
                onClick: onOpenBackups,
                title: t('backups_modal_title'),
              },
              {
                key: 'firmware',
                Icon: Package,
                label: t('tab_firmware'),
                onClick: onOpenFirmware,
                title: t('firmware_modal_title'),
                badge: hasFirmwareUpdate ? (firmwareVersion ? `v${firmwareVersion}` : '!') : null,
                badgeColor: 'var(--color-warning)',
                badgeTitle: t('firmware_update_available'),
              },
            ].filter(item => item.onClick).map(({ key, Icon, label, onClick, title, badge, badgeColor, badgeTitle }) => (
              <button
                key={key}
                type="button"
                className="btn-icon navbar-tool"
                onClick={onClick}
                title={title}
              >
                <Icon size={14} />
                <span className="navbar-tool-label">{label}</span>
                {badge && (
                  <span className="navbar-tool-badge" title={badgeTitle} style={{ background: badgeColor }}>
                    {badge}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Only the bad news is worth a badge. A healthy stream is already
              signalled by the router selector's own green dot, so saying it
              twice spends space on the state nobody needs telling about. */}
          {!isConnected && (
            <div className="badge badge-danger">
              <span style={{ width: 6, height: 6, borderRadius: 'var(--radius-full)', background: 'var(--color-danger)' }} />
              {t('disconnected')}
            </div>
          )}
        </div>

        {/* Selected router's note - between the tools and the app controls, it
            takes whatever width is left over and drops a full editor down on
            click. It is also the only element in the row that shrinks. */}
        {activeRouter && (
          <RouterCommentBar router={activeRouter} onSaved={onRouterCommentSaved} />
        )}

        {/* App-wide controls, pinned to the right of the note */}
        <div className="navbar-actions">
          {/* Router's own local time, left of the language switcher */}
          <RouterClock clock={routerInfo?.clock} />

          {/* Language Switcher: one button showing the *current* language's
              flag; clicking it switches to the other one. Two flags side by
              side were tried first, but with only two languages a toggle
              needs half the width and the current language is still obvious
              without hovering - it is the flag showing. */}
          <button
            type="button"
            className="lang-switch-toggle"
            onClick={() => setLang(lang === 'en' ? 'ru' : 'en')}
            title={lang === 'en' ? 'Переключить на русский' : 'Switch to English'}
            aria-label={lang === 'en' ? 'Переключить на русский' : 'Switch to English'}
          >
            {lang === 'en' ? <FlagGB size={18} /> : <FlagRU size={18} />}
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
