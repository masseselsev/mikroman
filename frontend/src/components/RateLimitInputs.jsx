import React from 'react';
import { useI18n } from '../context/I18nContext';
import { ArrowDown, ArrowUp } from 'lucide-react';

/**
 * Paired download/upload rate fields for a manual bandwidth limit.
 *
 * The user and device dialogs each carried their own copy of this markup, which
 * is how they drifted to different field heights and label sizes for the same
 * control. One component keeps them identical; only the placeholders and the
 * explanatory hint differ, because a device limit is a child of its owner's
 * queue while a user limit is the parent.
 *
 * Values are RouterOS rate strings ("50M", "500k"), kept as text rather than
 * numbers so the unit suffix survives editing.
 */
export function RateLimitInputs({
  down,
  up,
  onChangeDown,
  onChangeUp,
  downPlaceholder = 'e.g. 50M or 100M',
  upPlaceholder = 'e.g. 20M or 50M',
  hint
}) {
  const { t } = useI18n();

  return (
    <div style={{
      background: 'var(--bg-secondary)',
      padding: '12px 14px',
      borderRadius: 'var(--radius-md)',
      border: '1px solid var(--border-color)',
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label className="rate-label" style={{ color: 'var(--color-success)' }}>
            <ArrowDown size={13} /> {t('download_limit')}
          </label>
          <input
            type="text"
            className="form-input font-mono"
            placeholder={downPlaceholder}
            value={down}
            onChange={e => onChangeDown(e.target.value)}
            style={{ height: 'var(--control-h-sm)', fontSize: 'var(--fs-sm)' }}
          />
        </div>
        <div>
          <label className="rate-label" style={{ color: 'var(--color-primary)' }}>
            <ArrowUp size={13} /> {t('upload_limit')}
          </label>
          <input
            type="text"
            className="form-input font-mono"
            placeholder={upPlaceholder}
            value={up}
            onChange={e => onChangeUp(e.target.value)}
            style={{ height: 'var(--control-h-sm)', fontSize: 'var(--fs-sm)' }}
          />
        </div>
      </div>
      {hint ? (
        <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>{hint}</div>
      ) : null}
    </div>
  );
}

/**
 * Presets / Custom switch shared by the same two dialogs. Uses the segmented
 * control that the analytics and metrics range pickers use, so every segmented
 * choice in the app looks and measures the same.
 */
export function LimitModeToggle({ isCustom, onPresets, onCustom }) {
  const { t } = useI18n();
  return (
    <div className="range-group">
      <button
        type="button"
        className={`range-btn${!isCustom ? ' active' : ''}`}
        onClick={onPresets}
      >
        ⚡ {t('presets')}
      </button>
      <button
        type="button"
        className={`range-btn${isCustom ? ' active' : ''}`}
        onClick={onCustom}
      >
        ✏️ {t('custom')}
      </button>
    </div>
  );
}
