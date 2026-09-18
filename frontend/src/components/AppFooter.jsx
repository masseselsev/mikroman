import React, { useState, useEffect } from 'react';
import { Github, Sparkles } from 'lucide-react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';

const REPO_URL = 'https://github.com/masseselsev/mikroman';

/**
 * Trim a trailing zero patch from a semver string for display.
 *
 * "0.2.0" reads as "0.2" in the footer; the full string stays in the tooltip,
 * so a bug report can still quote an exact build.
 */
export function formatVersionTag(version) {
  const parts = String(version).split('.');
  if (parts.length === 3 && parts[2] === '0') {
    return `v${parts[0]}.${parts[1]}`;
  }
  return `v${version}`;
}

/**
 * Cadence for the background update check.
 *
 * One mount-time request was not enough: a release published while a dashboard
 * stayed open remained invisible, because the browser never asked again and the
 * server-side cache had already answered. Re-asking is cheap - the backend
 * revalidates upstream with a conditional request, and a 304 reply carries no
 * GitHub rate-limit cost.
 */
export const VERSION_RECHECK_INTERVAL_MS = 15 * 60 * 1000;

/**
 * Page footer: the build version, a copyright line, update notifications, and
 * a link back to the project's source. Deliberately quiet - it sits below every
 * screen's content, muted, and never competes with the dashboard.
 *
 * The version lives here rather than beside the app name in the header: it is
 * a fact you look up once when filing a bug, not one you read on every glance
 * at the dashboard, and the header needed the width for the router controls.
 */
export function AppFooter() {
  const { t } = useI18n();
  const year = new Date().getFullYear();
  const [updateInfo, setUpdateInfo] = useState(null);

  useEffect(() => {
    let active = true;

    const load = () => {
      api.checkAppVersion()
        .then((res) => {
          if (!active || !res) return;
          const info = res.data || res;
          // Replace the state rather than only setting it: once the running
          // build matches the latest release the offer has to disappear, not
          // linger from an earlier answer.
          if (info) setUpdateInfo(info.has_update ? info : null);
        })
        .catch(() => {
          // Silently ignore network or offline errors for background check
        });
    };

    const onVisible = () => {
      if (document.visibilityState === 'visible') load();
    };

    load();
    const timer = setInterval(load, VERSION_RECHECK_INTERVAL_MS);
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      active = false;
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);

  return (
    <footer className="app-footer">
      <span className="app-footer-copy">
        <span>© {year} MikroMan</span>
        {/* Injected from package.json at build time - see vite.config.js */}
        <span className="version-tag" title={`MikroMan ${__APP_VERSION__}`}>
          {formatVersionTag(__APP_VERSION__)}
        </span>
        {updateInfo?.has_update && (
          <a
            className="footer-update-badge"
            href={updateInfo.release_url || REPO_URL}
            target="_blank"
            rel="noopener noreferrer"
            title={t('footer_update_title', { version: updateInfo.latest_version })}
          >
            <Sparkles size={11} className="footer-update-icon" />
            <span>{t('footer_update_available', { version: updateInfo.latest_version })}</span>
          </a>
        )}
        <span className="brand-subtitle">· {t('app_subtitle')}</span>
      </span>
      <a
        className="app-footer-link"
        href={REPO_URL}
        target="_blank"
        rel="noopener noreferrer"
      >
        <Github size={14} />
        {t('footer_source')}
      </a>
    </footer>
  );
}
