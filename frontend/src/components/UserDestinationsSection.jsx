import React, { useState, useEffect } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import { formatBytes } from '../utils/formatters';
import { Globe, ArrowUpDown, Search, RefreshCw } from 'lucide-react';

export function UserDestinationsSection({ userId, deviceId = null }) {
  const { t, lang } = useI18n();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [sortBy, setSortBy] = useState('total_bytes');
  const [sortOrder, setSortOrder] = useState('desc');
  const [search, setSearch] = useState('');

  const loadDestinations = async () => {
    if (!userId || typeof api.getUserDestinations !== 'function') return;
    setLoading(true);
    try {
      const res = await api.getUserDestinations(userId, {
        sort_by: sortBy,
        order: sortOrder,
        device_id: deviceId || undefined,
        search: search.trim() || undefined,
        limit: 50,
      });
      if (res?.data) {
        setItems(res.data);
      }
    } catch (err) {
      console.error('Failed to load user destinations:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadDestinations();
  }, [userId, deviceId, sortBy, sortOrder]);

  const handleHeaderClick = (column) => {
    if (sortBy === column) {
      setSortOrder(sortOrder === 'desc' ? 'asc' : 'desc');
    } else {
      setSortBy(column);
      setSortOrder('desc');
    }
  };

  const handleSearchSubmit = (e) => {
    e.preventDefault();
    loadDestinations();
  };

  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, flexWrap: 'wrap', gap: 8 }}>
        <h4 style={{ fontSize: 'var(--fs-sm)', fontWeight: 700, margin: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Globe size={15} style={{ color: 'var(--color-primary)' }} />
          <span>{t('destinations_title')}</span>
        </h4>

        <form onSubmit={handleSearchSubmit} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <div style={{ position: 'relative' }}>
            <input
              type="text"
              className="form-input"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('search_connections_placeholder')}
              style={{
                fontSize: 'var(--fs-xs)',
                padding: '3px 8px',
                height: 26,
                width: 180,
              }}
            />
          </div>
          <button type="submit" className="btn btn-secondary btn-sm" style={{ padding: '3px 8px', fontSize: 'var(--fs-xs)' }}>
            <Search size={12} />
          </button>
        </form>
      </div>

      <div
        className="card panel-flush"
        style={{
          maxHeight: 240,
          overflowY: 'auto',
          border: '1px solid var(--border-color)',
          background: 'var(--bg-secondary)',
          borderRadius: 'var(--radius-sm)',
        }}
      >
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--fs-xs)' }}>
          <thead>
            <tr
              style={{
                position: 'sticky',
                top: 0,
                background: 'var(--bg-card)',
                borderBottom: '1px solid var(--border-color)',
                textAlign: 'left',
                color: 'var(--text-secondary)',
                zIndex: 1,
              }}
            >
              <th style={{ padding: '6px 10px' }}>{t('col_destination')}</th>
              <th
                style={{ padding: '6px 10px', textAlign: 'right', cursor: 'pointer', userSelect: 'none' }}
                onClick={() => handleHeaderClick('hit_count')}
              >
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                  {t('col_hits')} <ArrowUpDown size={11} />
                </span>
              </th>
              <th
                style={{ padding: '6px 10px', textAlign: 'right', cursor: 'pointer', userSelect: 'none' }}
                onClick={() => handleHeaderClick('total_bytes')}
              >
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                  {t('col_volume')} <ArrowUpDown size={11} />
                </span>
              </th>
              <th
                style={{ padding: '6px 10px', textAlign: 'right', cursor: 'pointer', userSelect: 'none' }}
                onClick={() => handleHeaderClick('bytes_in')}
              >
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                  {t('col_download')} <ArrowUpDown size={11} />
                </span>
              </th>
              <th
                style={{ padding: '6px 10px', textAlign: 'right', cursor: 'pointer', userSelect: 'none' }}
                onClick={() => handleHeaderClick('bytes_out')}
              >
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                  {t('col_upload')} <ArrowUpDown size={11} />
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={5} style={{ textAlign: 'center', padding: 24, color: 'var(--text-muted)' }}>
                  <RefreshCw size={16} className="spin" style={{ margin: '0 auto' }} />
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={5} style={{ textAlign: 'center', padding: 20, color: 'var(--text-muted)' }}>
                  {t('no_destinations_found')}
                </td>
              </tr>
            ) : (
              items.map((row) => (
                <tr key={row.id} style={{ borderBottom: '1px solid var(--border-color)' }} className="table-row-hover">
                  <td style={{ padding: '6px 10px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span title={row.country_name || row.country_code} style={{ fontSize: '1rem', lineHeight: 1 }}>
                        {row.flag_emoji || '🌐'}
                      </span>
                      <div>
                        <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                          {row.domain || row.destination_ip}
                        </div>
                        {row.domain && (
                          <div style={{ fontSize: 'var(--fs-3xs)', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                            {row.destination_ip}
                          </div>
                        )}
                      </div>
                    </div>
                  </td>
                  <td style={{ padding: '6px 10px', textAlign: 'right', fontFamily: 'monospace' }}>
                    {row.hit_count}
                  </td>
                  <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 600, fontFamily: 'monospace' }}>
                    {formatBytes(row.total_bytes)}
                  </td>
                  <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--color-success)', fontFamily: 'monospace' }}>
                    {formatBytes(row.bytes_in)}
                  </td>
                  <td style={{ padding: '6px 10px', textAlign: 'right', color: 'var(--color-primary)', fontFamily: 'monospace' }}>
                    {formatBytes(row.bytes_out)}
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
