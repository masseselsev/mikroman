import React from 'react';
import { useI18n } from '../context/I18nContext';
import { ArrowDown, ArrowUp } from 'lucide-react';

/**
 * Paired download/upload rate fields for a manual bandwidth limit.
 *
 * The user and device dialogs each carried their own copy of this markup, which
 * is how they drifted apart. This is now the *same* control as the per-user
 * card footer in UserCard - a bare two-field row (no panel box), tiny colour-
 * coded labels, 30px inputs - so the limit editor reads identically in the
 * card and in both dialogs. Only the placeholders and the tooltip hint differ.
 *
 * The hint is a tooltip on the dotted-underlined Down / Up labels, not a line
 * of small print. Values are RouterOS rate strings ("50M", "500k"), kept as
 * text so the unit suffix survives editing.
 */
const FIELD_LABEL_STYLE = {
  fontSize: 'var(--fs-2xs)',
  fontWeight: 700,
  display: 'flex',
  alignItems: 'center',
  gap: 3,
  marginBottom: 3,
};
const INPUT_STYLE = { padding: '4px 6px', fontSize: 'var(--fs-sm)', height: 30, width: '100%' };
const HINT_TEXT_STYLE = { borderBottom: '1px dotted currentColor', lineHeight: 1.1 };

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

  const field = (Icon, color, labelKey, value, onChange, placeholder) => (
    <div style={{ flex: 1, minWidth: 0 }}>
      <label
        title={hint || undefined}
        style={{ ...FIELD_LABEL_STYLE, color, ...(hint ? { cursor: 'help' } : null) }}
      >
        <Icon size={11} />
        {hint
          ? <span style={HINT_TEXT_STYLE}>{t(labelKey)}</span>
          : t(labelKey)}
      </label>
      <input
        type="text"
        className="form-input font-mono"
        style={INPUT_STYLE}
        placeholder={placeholder}
        value={value}
        onChange={e => onChange(e.target.value)}
      />
    </div>
  );

  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
      {field(ArrowDown, 'var(--color-success)', 'download_limit', down, onChangeDown, downPlaceholder)}
      {field(ArrowUp, 'var(--color-primary)', 'upload_limit', up, onChangeUp, upPlaceholder)}
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
