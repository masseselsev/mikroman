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
          gap: 8,
          padding: '6px 12px',
          background: 'var(--bg-secondary)',
          border: '1px solid var(--border-color)',
          borderRadius: 'var(--radius-sm)',
          fontSize: 'var(--fs-sm)'
        }}
        onClick={() => setIsOpen(!isOpen)}
      >
        <Server size={16} style={{ color: 'var(--color-primary)' }} />
        <span className="truncate" style={{ maxWidth: 140 }}>
          {current?.name || 'Router'}
        </span>
        <span className={`status-dot${current?.is_online ? ' is-online' : ''}`} />
        <ChevronDown size={14} style={{ opacity: 0.7 }} />
      </button>

      {isOpen && (
        <div className="popover" style={{ top: 'calc(100% + 6px)', left: 0, width: 240 }}>
          <div className="popover-head">
            {t('switch_router')}
          </div>

          <div style={{ maxHeight: 240, overflowY: 'auto' }}>
            {routers.map(r => {
              const isSelected = r.id === current?.id;
              return (
                <button
                  key={r.id}
                  type="button"
                  className={`popover-item${isSelected ? ' is-selected' : ''}`}
                  onClick={() => {
                    onSelectRouter(r.id);
                    setIsOpen(false);
                  }}
                >
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2, overflow: 'hidden', flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span className={`status-dot${r.is_online ? ' is-online' : ''}`} />
                      <span className="truncate" style={{ fontWeight: isSelected ? 700 : 600 }}>
                        {r.name}
                      </span>
                    </div>
                    <div className="truncate" style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', paddingLeft: 14 }}>
                      {r.board_name || r.model || 'MikroTik'} {r.ros_version ? `• ROS ${r.ros_version}` : ''}
                    </div>
                  </div>
                  {isSelected && <Check size={14} style={{ color: 'var(--color-primary)', flexShrink: 0 }} />}
                </button>
              );
            })}
          </div>

          <div className="popover-foot">
            <button
              type="button"
              className="popover-item"
              onClick={() => {
                setIsOpen(false);
                if (onAddRouter) onAddRouter();
              }}
              style={{ justifyContent: 'flex-start', color: 'var(--color-primary)', fontWeight: 600 }}
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
