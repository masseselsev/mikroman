import React, { useCallback, useEffect, useState } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { formatBytes } from '../utils/formatters';
import { Box, RefreshCw } from 'lucide-react';

/**
 * Devices that turned out to be containers running on the router.
 *
 * A container's veth end answers ARP with a MAC and an IP, exactly like a
 * laptop, so discovery finds it and creates a device record whether we want one
 * or not. Suppressing the record would lose its traffic; leaving it in the
 * unassigned inbox asks the operator to assign a Docker image to a family
 * member, which teaches them to ignore the inbox. So they are separated at
 * discovery (`is_container`) and listed here instead, where they make sense.
 *
 * Their traffic *is* still accounted — they are ordinary forwarded clients as
 * far as the mangle counters are concerned — it simply belongs to the router
 * rather than to a person.
 */
export function ContainerWorkloads() {
  const { t } = useI18n();
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.getContainerDevices();
      setDevices(res?.data || []);
    } catch {
      setDevices([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="card">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 8 }}>
        <div style={{ minWidth: 0 }}>
          <strong style={{ fontSize: 'var(--fs-sm)', display: 'flex', alignItems: 'center', gap: 6 }}>
            <Box size={15} style={{ color: '#b07cc6' }} />
            {t('tab_container_devices')}
            <span className="badge badge-neutral" style={{ fontSize: 'var(--fs-2xs)', padding: '0 6px' }}>
              {devices.length}
            </span>
          </strong>
          <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', marginTop: 2 }}>
            {t('container_devices_hint')}
          </div>
        </div>
        <button
          className="btn btn-ghost btn-sm btn-icon"
          onClick={load}
          disabled={loading}
          title={t('ctr_refresh')}
          style={{ width: 30, height: 30, flexShrink: 0 }}
        >
          <RefreshCw size={13} className={loading ? 'spin' : undefined} />
        </button>
      </div>

      {devices.length === 0 ? (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-muted)', padding: '8px 0' }}>
          {t('no_container_devices')}
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="table" style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-color)', textAlign: 'left', fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>
                <th style={{ padding: '6px 10px' }}>{t('table_device')}</th>
                <th style={{ padding: '6px 10px' }}>{t('table_ip_mac')}</th>
                <th style={{ padding: '6px 10px' }}>{t('ctr_col_iface')}</th>
                <th style={{ padding: '6px 10px' }}>{t('today_label')}</th>
              </tr>
            </thead>
            <tbody>
              {devices.map(d => (
                <tr key={d.id} style={{ borderBottom: '1px solid var(--border-color)', fontSize: 'var(--fs-sm)' }}>
                  <td style={{ padding: '8px 10px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
                      {d.custom_name || d.hostname || d.mac_address}
                      <span className="container-badge">{t('container_badge')}</span>
                    </div>
                    <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)' }}>
                      {d.vendor || '—'}
                    </div>
                  </td>
                  <td style={{ padding: '8px 10px', fontSize: 'var(--fs-xs)' }} className="font-mono">
                    <div>{d.ip_address || '—'}</div>
                    <div style={{ color: 'var(--text-muted)' }}>{d.mac_address}</div>
                  </td>
                  <td style={{ padding: '8px 10px', fontSize: 'var(--fs-xs)' }} className="font-mono">
                    {d.last_interface || '—'}
                  </td>
                  <td style={{ padding: '8px 10px' }} className="font-mono">
                    <span style={{ color: 'var(--color-success)' }}>↓ {formatBytes(d.bytes_today_in || 0)}</span>
                    {'  '}
                    <span style={{ color: 'var(--color-primary)' }}>↑ {formatBytes(d.bytes_today_out || 0)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
