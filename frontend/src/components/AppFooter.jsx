import React from 'react';
import { Github } from 'lucide-react';
import { useI18n } from '../context/I18nContext';

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
 * Page footer: the build version, a copyright line and a link back to the
 * project's source. Deliberately quiet - it sits below every screen's content,
 * muted, and never competes with the dashboard.
 *
 * The version lives here rather than beside the app name in the header: it is
 * a fact you look up once when filing a bug, not one you read on every glance
 * at the dashboard, and the header needed the width for the router controls.
 */
export function AppFooter() {
  const { t } = useI18n();
  const year = new Date().getFullYear();

  return (
    <footer className="app-footer">
      <span className="app-footer-copy">
        <span>© {year} MikroMan</span>
        {/* Injected from package.json at build time - see vite.config.js */}
        <span className="version-tag" title={`MikroMan ${__APP_VERSION__}`}>
          {formatVersionTag(__APP_VERSION__)}
        </span>
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
