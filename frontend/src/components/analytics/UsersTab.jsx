import React from 'react';
import { useI18n } from '../../context/I18nContext';
import { formatBytes } from '../../utils/formatters';
import { ShareBar, SortHeader, sortRows } from './tableParts';

/**
 * Breakdown tab 2: one row per profile, sortable.
 *
 * Deliberately has no search box. A household has a handful of profiles, and
 * the column sort is enough to find any of them; filtering belongs on the
 * device tab, where the list can run to dozens of rows.
 */
export function UsersTab({ users, userSort, toggleUserSort }) {
  const { t } = useI18n();

  return (
      <div style={{ overflowX: 'auto' }}>
        <table className="table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-color)', textAlign: 'left', fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
              <SortHeader label={t('table_user')} field="user_name" sort={userSort} onSort={toggleUserSort} />
              {/* device count for this user - not the "Unassigned Devices" tab label */}
              <SortHeader label={t('table_devices')} field="device_count" sort={userSort} onSort={toggleUserSort} />
              <SortHeader label={`${t('total_download')} (RX)`} field="bytes_in" sort={userSort} onSort={toggleUserSort} />
              <SortHeader label={`${t('total_upload')} (TX)`} field="bytes_out" sort={userSort} onSort={toggleUserSort} />
              <SortHeader label={t('total_combined')} field="total_bytes" sort={userSort} onSort={toggleUserSort} />
              <SortHeader label={t('share_of_traffic')} field="pct_of_total" sort={userSort} onSort={toggleUserSort} />
            </tr>
          </thead>
          <tbody>
            {sortRows(users, userSort).map(u => (
              <tr key={u.user_id} style={{ borderBottom: '1px solid var(--border-color)', fontSize: 'var(--fs-sm)' }}>
                <td style={{ padding: '10px 12px', fontWeight: 700 }}>{u.user_name}</td>
                <td style={{ padding: '10px 12px', color: 'var(--text-muted)' }}>{u.device_count}</td>
                <td style={{ padding: '10px 12px', color: 'var(--color-success)', fontWeight: 600 }} className="font-mono">
                  {formatBytes(u.bytes_in)}
                </td>
                <td style={{ padding: '10px 12px', color: '#3498db', fontWeight: 600 }} className="font-mono">
                  {formatBytes(u.bytes_out)}
                </td>
                <td style={{ padding: '10px 12px', fontWeight: 800 }} className="font-mono">
                  {formatBytes(u.total_bytes)}
                </td>
                <td style={{ padding: '10px 12px', minWidth: 130 }}>
                  <ShareBar pct={u.pct_of_total} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
  );
}
