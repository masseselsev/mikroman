import React from 'react';
import { useI18n } from '../../context/I18nContext';
import { formatBytes } from '../../utils/formatters';
import { ShareBar, SortHeader, sortRows } from './tableParts';
import { Waypoints } from 'lucide-react';

/**
 * Breakdown tab 4: one row per interface, rebuilt from the sampled counters.
 *
 * The reason this tab exists is watching a VPN / overlay link - WireGuard,
 * ZeroTier, a GRE tunnel - on its own, separately from the physical WAN. Those
 * interfaces sort to the top and carry a "tunnel" badge; the WAN interfaces
 * that make up the gateway total carry a "WAN" badge so nobody adds the two
 * together. Alongside the selected range each row shows its current-cycle and
 * all-time volume, the same three windows as the user and device tabs.
 */
export function InterfacesTab({ interfaces, sort, toggleSort }) {
  const { t } = useI18n();
  const rows = interfaces || [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', display: 'flex', alignItems: 'flex-start', gap: 6 }}>
        <Waypoints size={13} style={{ flexShrink: 0, marginTop: 2 }} />
        <span>{t('iface_tab_hint')}</span>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table className="table" style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-color)', textAlign: 'left', fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
              <SortHeader label={t('iface_col_name')} field="interface_name" sort={sort} onSort={toggleSort} />
              <SortHeader label={`${t('total_download')} (RX)`} field="bytes_in" sort={sort} onSort={toggleSort} />
              <SortHeader label={`${t('total_upload')} (TX)`} field="bytes_out" sort={sort} onSort={toggleSort} />
              <SortHeader label={t('iface_col_range')} field="total_bytes" sort={sort} onSort={toggleSort} />
              <SortHeader label={t('iface_col_cycle')} field="cycle_bytes" sort={sort} onSort={toggleSort} />
              <SortHeader label={t('iface_col_alltime')} field="all_time_bytes" sort={sort} onSort={toggleSort} />
              <SortHeader label={t('share_of_traffic')} field="pct_of_total" sort={sort} onSort={toggleSort} />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={7} style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 'var(--fs-sm)' }}>
                  {t('iface_none')}
                </td>
              </tr>
            ) : (
              sortRows(rows, sort).map(i => (
                <tr key={i.interface_name} style={{ borderBottom: '1px solid var(--border-color)', fontSize: 'var(--fs-sm)' }}>
                  <td style={{ padding: '10px 12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                      <span className="font-mono" style={{ fontWeight: 700 }}>{i.interface_name}</span>
                      {i.is_tunnel && (
                        <span className="badge" style={{
                          fontSize: 'var(--fs-3xs)', padding: '0 5px',
                          background: 'rgba(59, 130, 246, 0.15)', color: 'var(--color-primary)',
                          border: '1px solid rgba(59, 130, 246, 0.3)'
                        }}>
                          {t('iface_tunnel_badge')}
                        </span>
                      )}
                      {i.is_monitored && (
                        <span className="badge" style={{
                          fontSize: 'var(--fs-3xs)', padding: '0 5px',
                          background: 'rgba(100, 116, 139, 0.18)', color: 'var(--text-muted)',
                          border: '1px solid rgba(100, 116, 139, 0.3)'
                        }}>
                          {t('iface_wan_badge')}
                        </span>
                      )}
                    </div>
                  </td>
                  <td style={{ padding: '10px 12px', color: 'var(--color-success)', fontWeight: 600 }} className="font-mono">
                    {formatBytes(i.bytes_in)}
                  </td>
                  <td style={{ padding: '10px 12px', color: '#3498db', fontWeight: 600 }} className="font-mono">
                    {formatBytes(i.bytes_out)}
                  </td>
                  <td style={{ padding: '10px 12px', fontWeight: 800 }} className="font-mono">
                    {formatBytes(i.total_bytes)}
                  </td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-secondary)' }} className="font-mono">
                    {formatBytes(i.cycle_bytes)}
                  </td>
                  <td style={{ padding: '10px 12px', color: 'var(--text-secondary)' }} className="font-mono">
                    {formatBytes(i.all_time_bytes)}
                  </td>
                  <td style={{ padding: '10px 12px', minWidth: 130 }}>
                    <ShareBar pct={i.pct_of_total} />
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
