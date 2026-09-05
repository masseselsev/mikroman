import React, { useState, useRef, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { Server, Check, ChevronDown, Plus } from 'lucide-react';

/**
 * Router picker for the top bar.
 *
 * The collapsed button carries only what identifies the router - its name and
 * whether it is up. Hardware model and firmware version are shown per entry
 * once the list is expanded: they are the facts you compare routers by, and
 * kept in the header they only crowded the brand.
 *
 * `currentVersion` is the live RouterOS version from telemetry. The stored
 * `ros_version` is refreshed by the `/routers` probe, which can lag or fail;
 * for the router being watched right now, telemetry is the fresher source.
 */
export function RouterSelector({
  routers = [],
  activeRouter,
  telemetryLive = false,
  currentVersion = null,
  onSelectRouter,
  onAddRouter,
}) {
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
  // `activeRouter` is a snapshot taken when the router was picked and can carry
  // a stale `is_online` (it was often captured before the first poll marked the
  // router up). The `routers` list is refreshed on every poll, so read the live
  // status from the matching entry there and only fall back to the snapshot.
  const currentLive = routers.find(r => r.id === current?.id) || current;
  // A flowing telemetry stream is proof the selected router is reachable right
  // now - trust it over the 30 s `/routers` probe, which flaps on a slow remote
  // router and would otherwise grey out a box you are actively watching.
  const currentOnline = !!currentLive?.is_online || telemetryLive;

  return (
    <div className="router-selector" ref={dropdownRef} style={{ position: 'relative' }}>
      <button
        type="button"
        className="btn btn-secondary btn-sm"
        style={{
          gap: 8,
          fontSize: 'var(--fs-sm)'
        }}
        onClick={() => setIsOpen(!isOpen)}
      >
        <Server size={15} style={{ color: 'var(--color-primary)' }} />
        <span className="truncate" style={{ maxWidth: 140 }}>
          {current?.name || 'Router'}
        </span>
        <span className={`status-dot${currentOnline ? ' is-online' : ''}`} />
        <ChevronDown size={14} style={{ opacity: 0.7 }} />
      </button>

      {isOpen && (
        <div className="popover" style={{ top: 'calc(100% + 6px)', left: 0, width: 300 }}>
          <div className="popover-head">
            {t('switch_router')}
          </div>

          <div style={{ maxHeight: 240, overflowY: 'auto' }}>
            {routers.map(r => {
              const isSelected = r.id === current?.id;
              // Telemetry wins for the selected router; everything else falls
              // back to what the last `/routers` probe stored.
              const version = (isSelected && currentVersion) || r.ros_version;
              return (
                <button
                  key={r.id}
                  type="button"
                  className={`popover-item${isSelected ? ' is-selected' : ''}`}
                  onClick={() => {
                    setIsOpen(false);
                    onSelectRouter(r);
                  }}
                >
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 2, overflow: 'hidden', flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span className={`status-dot${(isSelected ? currentOnline : r.is_online) ? ' is-online' : ''}`} />
                      <span className="truncate" style={{ fontWeight: isSelected ? 700 : 600 }}>
                        {r.name}
                      </span>
                    </div>
                    <div className="truncate" style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', paddingLeft: 14 }}>
                      {/* Drop only the redundant "ROS" prefix - the firmware
                          channel, e.g. "(stable)" / "(testing)", stays. */}
                      {r.board_name || r.model || 'MikroTik'}
                      {version ? ` · v${version}` : ''}
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
