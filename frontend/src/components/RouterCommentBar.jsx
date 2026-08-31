import React, { useEffect, useRef, useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { StickyNote, Check, X, Pencil } from 'lucide-react';

/**
 * The selected router's free-text note, sitting in the header between the router
 * selector and the clock.
 *
 * Collapsed it shows at most the first three lines; the rest is there but
 * clipped. Clicking it drops a panel down with the full text in a textarea and a
 * Save button, then it collapses again. The note is per-router and persisted via
 * PATCH-equivalent `PUT /routers/{id}` - only the `comment` field is sent, so it
 * never disturbs the connection settings.
 *
 * State is local: `saved` is what the server has, `draft` is what the textarea
 * holds while open. Both re-seed when the selected router changes.
 */
export function RouterCommentBar({ router, onSaved }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState(router?.comment || '');
  const [draft, setDraft] = useState(router?.comment || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const textareaRef = useRef(null);
  const rootRef = useRef(null);

  // Re-seed when a different router is selected, or the prop catches up with a
  // save made elsewhere while this panel was closed.
  useEffect(() => {
    if (open) return;
    setSaved(router?.comment || '');
    setDraft(router?.comment || '');
  }, [router?.id, router?.comment, open]);

  // A click anywhere outside the open panel is treated as "cancel".
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setOpen(false);
        setDraft(saved);
        setError(null);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open, saved]);

  useEffect(() => {
    if (open && textareaRef.current) {
      textareaRef.current.focus();
      const len = textareaRef.current.value.length;
      textareaRef.current.setSelectionRange(len, len);
    }
  }, [open]);

  if (!router) return null;

  const handleSave = async () => {
    const next = draft.trim();
    setBusy(true);
    setError(null);
    try {
      await api.updateRouter(router.id, { comment: next || null });
      setSaved(next);
      setOpen(false);
      if (onSaved) onSaved(next);
    } catch (err) {
      console.error('Failed to save router comment:', err);
      setError(t('router_comment_save_failed'));
    } finally {
      setBusy(false);
    }
  };

  const handleKeyDown = (e) => {
    // Ctrl/Cmd+Enter saves; Escape cancels. Plain Enter stays a newline.
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      handleSave();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setOpen(false);
      setDraft(saved);
      setError(null);
    }
  };

  const hasNote = saved.trim().length > 0;

  return (
    <div className="router-comment" ref={rootRef}>
      <button
        type="button"
        className={`router-comment-collapsed${hasNote ? '' : ' is-empty'}`}
        onClick={() => setOpen(o => !o)}
        title={hasNote ? saved : t('router_comment_add')}
        aria-expanded={open}
      >
        <StickyNote size={13} className="router-comment-icon" />
        {hasNote ? (
          <span className="router-comment-preview">{saved}</span>
        ) : (
          <span className="router-comment-placeholder">{t('router_comment_add')}</span>
        )}
        <Pencil size={11} className="router-comment-edit-hint" />
      </button>

      {open && (
        <div className="router-comment-panel">
          <div className="router-comment-panel-head">
            <span>
              <StickyNote size={13} style={{ verticalAlign: '-2px', marginRight: 6 }} />
              {t('router_comment_title', { name: router.name })}
            </span>
            <button
              type="button"
              className="btn-icon"
              style={{ width: 22, height: 22 }}
              onClick={() => { setOpen(false); setDraft(saved); setError(null); }}
              title={t('cancel')}
            >
              <X size={13} />
            </button>
          </div>

          <textarea
            ref={textareaRef}
            className="form-input router-comment-textarea font-mono"
            rows={6}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('router_comment_placeholder')}
            maxLength={4000}
          />

          {error && <div className="router-comment-error">{error}</div>}

          <div className="router-comment-panel-actions">
            <span className="router-comment-hint">{t('router_comment_shortcut_hint')}</span>
            <div style={{ display: 'flex', gap: 6 }}>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => { setOpen(false); setDraft(saved); setError(null); }}
                disabled={busy}
              >
                {t('cancel')}
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleSave}
                disabled={busy || draft === saved}
              >
                <Check size={13} />
                {t('save')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
