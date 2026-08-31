import React from 'react';
import { Github } from 'lucide-react';
import { useI18n } from '../context/I18nContext';

const REPO_URL = 'https://github.com/masseselsev/mikroman';

/**
 * Page footer: a copyright line and a link back to the project's source.
 * Deliberately quiet - it sits below every screen's content, muted, and never
 * competes with the dashboard.
 */
export function AppFooter() {
  const { t } = useI18n();
  const year = new Date().getFullYear();

  return (
    <footer className="app-footer">
      <span className="app-footer-copy">
        © {year} MikroMan · {t('footer_tagline')}
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
