import React, { useState, useRef, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { Server, Check, ChevronDown, Plus } from 'lucide-react';

export function RouterSelector({ routers = [], activeRouter, onSelectRouter, onAddRouter }) {
  const { t } = useI18n();
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef(null);

  // Close dropdown on click outside
  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  if (!routers || routers.length === 0) {
    return null;
  }

  const current = activeRouter || routers.find(r => r.is_default) || routers[0];

  return (
    <div className="router-selector" ref={dropdownRef} style={{ position: 'relative' }}>
      <button
        type="button"
        className="btn btn-ghost"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 12px',
          background: 'var(--bg-secondary)',
          border: '1px solid var(--border-color)',
          borderRadius: 8,
          fontSize: '0.85rem',
          fontWeight: 600
        }}
        onClick={() => setIsOpen(!isOpen)}
      >
        <Server size={16} style={{ color: 'var(--color-primary)' }} />
        <span style={{ maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {current?.name || 'Router'}
        </span>
        <span
          className="badge-dot"
          style={{
            background: current?.is_online ? 'var(--color-success)' : 'var(--text-muted)',
            width: 8,
            height: 8,
            borderRadius: '50%'
          }}
        />
        <ChevronDown size={14} style={{ opacity: 0.7 }} />
      </button>

      {isOpen && (
        <div
          className="card shadow-lg"
          style={{
            position: 'absolute',
            top: 'calc(100% + 6px)',
            left: 0,
            width: 240,
            zIndex: 1000,
            padding: '6px 0',
            background: 'var(--bg-card)',
            border: '1px solid var(--border-color)'
          }}
        >
          <div style={{ padding: '6px 12px', fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
            {t('switch_router')}
          </div>

          <div style={{ maxHeight: 200, overflowY: 'auto' }}>
            {routers.map(r => {
              const isSelected = r.id === current?.id;
              return (
                <button
                  key={r.id}
                  type="button"
                  onClick={() => {
                    onSelectRouter(r.id);
                    setIsOpen(false);
                  }}
                  style={{
                    width: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '8px 12px',
                    background: isSelected ? 'var(--bg-secondary)' : 'transparent',
                    border: 'none',
                    textAlign: 'left',
                    color: 'var(--text-primary)',
                    cursor: 'pointer',
                    fontSize: '0.85rem'
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, overflow: 'hidden' }}>
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: '50%',
                        background: r.is_online ? 'var(--color-success)' : 'var(--text-muted)'
                      }}
                    />
                    <span style={{ fontWeight: isSelected ? 700 : 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {r.name}
                    </span>
                  </div>
                  {isSelected && <Check size={14} style={{ color: 'var(--color-primary)' }} />}
                </button>
              );
            })}
          </div>

          <div style={{ borderTop: '1px solid var(--border-color)', marginTop: 4, paddingTop: 4 }}>
            <button
              type="button"
              onClick={() => {
                setIsOpen(false);
                if (onAddRouter) onAddRouter();
              }}
              style={{
                width: '100%',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '8px 12px',
                background: 'transparent',
                border: 'none',
                color: 'var(--color-primary)',
                cursor: 'pointer',
                fontSize: '0.825rem',
                fontWeight: 600
              }}
            >
              <Plus size={14} />
              {t('add_router_btn')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
