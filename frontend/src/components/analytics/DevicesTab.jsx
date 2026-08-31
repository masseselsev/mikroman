import React from 'react';
import { useI18n } from '../../context/I18nContext';
import { formatBytes } from '../../utils/formatters';
import { ShareBar, SortHeader, sortRows } from './tableParts';
import {
  EyeOff,
  Search,
} from 'lucide-react';

/**
 * Breakdown tab 3: one row per device, with search, owner filter and a
 * hidden-device toggle.
 *
 * The filtering itself is done by the parent, which owns the filter state -
 * the same state drives the empty-state message here, and splitting it would
 * let the two disagree about whether a result set is empty.
 */
export function DevicesTab({
  users,
  filteredDevices,
  deviceSort,
  toggleDeviceSort,
  searchTerm,
  setSearchTerm,
  userFilter,
  setUserFilter,
  showHidden,
  setShowHidden,
}) {
  const { t } = useI18n();

  return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {/* Search & User Filters */}
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 220, position: 'relative' }}>
            <Search size={14} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--text-muted)' }} />
            <input
              type="text"
              className="form-input"
              placeholder={t('search_devices_placeholder')}
              value={searchTerm}
              onChange={e => setSearchTerm(e.target.value)}
              style={{ paddingLeft: 32, paddingTop: 6, paddingBottom: 6, fontSize: 'var(--fs-sm)' }}
            />
          </div>

          <select
            className="form-select"
            value={userFilter}
            onChange={e => setUserFilter(e.target.value)}
            style={{ width: 180, paddingTop: 6, paddingBottom: 6, fontSize: 'var(--fs-sm)' }}
          >
            <option value="all">{t('all_users_filter')}</option>
            <option value="unassigned">{t('unassigned_traffic')}</option>
            {users.map(u => (
              <option key={u.user_id} value={u.user_id}>{u.user_name}</option>
            ))}
          </select>

          {/* Hidden devices are infrastructure the operator parked on purpose.
              This table used to show them unconditionally while the rest of the
              app hid them by default, so the same network reported a different
              device count depending on which tab you were looking at. */}
          <button
            type="button"
            className={`btn btn-sm ${showHidden ? 'btn-secondary' : 'btn-ghost'}`}
            onClick={() => setShowHidden(!showHidden)}
            style={{ display: 'flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap' }}
            title={t('show_hidden_devices')}
          >
            <EyeOff size={14} />
            {t('show_hidden_devices')}
          </button>
        </div>

        {/* Devices Table */}
        <div style={{ overflowX: 'auto' }}>
          <table className="table" style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-color)', textAlign: 'left', fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
                <SortHeader label={t('table_device')} field="custom_name" sort={deviceSort} onSort={toggleDeviceSort} />
                <SortHeader label={t('table_ip_mac')} field="ip_address" sort={deviceSort} onSort={toggleDeviceSort} />
                <SortHeader label={t('table_user')} field="user_name" sort={deviceSort} onSort={toggleDeviceSort} />
                <SortHeader label={t('download_rx')} field="bytes_in" sort={deviceSort} onSort={toggleDeviceSort} />
                <SortHeader label={t('upload_tx')} field="bytes_out" sort={deviceSort} onSort={toggleDeviceSort} />
                <SortHeader label={t('table_total')} field="total_bytes" sort={deviceSort} onSort={toggleDeviceSort} />
                <SortHeader label={t('table_share')} field="pct_of_total" sort={deviceSort} onSort={toggleDeviceSort} />
                <SortHeader label={t('table_speed_limit')} field="speed_limit" sort={deviceSort} onSort={toggleDeviceSort} />
              </tr>
            </thead>
            <tbody>
              {filteredDevices.length === 0 ? (
                <tr>
                  <td colSpan={8} style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>
                    {t('no_devices_matching')}
                  </td>
                </tr>
              ) : (
                sortRows(filteredDevices, deviceSort).map(d => (
                  <tr key={d.device_id} style={{ borderBottom: '1px solid var(--border-color)', fontSize: 'var(--fs-sm)' }}>
                    <td style={{ padding: '10px 12px' }}>
                      <div style={{ fontWeight: 700 }}>{d.custom_name || d.hostname || `${t('table_device')} ${d.device_id}`}</div>
                      <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>{d.vendor || t('unknown_vendor')}</div>
                    </td>
                    <td style={{ padding: '10px 12px', fontSize: 'var(--fs-xs)' }} className="font-mono">
                      <div>{d.ip_address || '—'}</div>
                      <div style={{ color: 'var(--text-muted)' }}>{d.mac_address}</div>
                    </td>
                    <td style={{ padding: '10px 12px' }}>
                      {d.user_name ? (
                        <span style={{ padding: '2px 6px', borderRadius: 'var(--radius-xs)', background: 'var(--bg-secondary)', fontSize: 'var(--fs-xs)', fontWeight: 600 }}>
                          {d.user_name}
                        </span>
                      ) : (
                        <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>{t('unassigned_label')}</span>
                      )}
                    </td>
                    <td style={{ padding: '10px 12px', color: 'var(--color-success)', fontWeight: 600 }} className="font-mono">
                      {formatBytes(d.bytes_in)}
                    </td>
                    <td style={{ padding: '10px 12px', color: '#3498db', fontWeight: 600 }} className="font-mono">
                      {formatBytes(d.bytes_out)}
                    </td>
                    <td style={{ padding: '10px 12px', fontWeight: 800 }} className="font-mono">
                      {formatBytes(d.total_bytes)}
                    </td>
                    <td style={{ padding: '10px 12px', minWidth: 130 }}>
                      <ShareBar pct={d.pct_of_total} />
                    </td>
                    <td style={{ padding: '10px 12px' }}>
                      <span style={{ fontSize: 'var(--fs-xs)', color: d.speed_limit !== 'default' ? 'var(--color-warning)' : 'var(--text-muted)' }}>
                        {d.speed_limit === 'default' ? t('inherit_user') : d.speed_limit}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
  );
}
