import React, { useState, useEffect, useRef, useMemo } from 'react';
import { useI18n } from '../context/I18nContext';
import { api } from '../api/client';
import {
  X,
  Activity,
  RefreshCw,
  Pause,
  Play,
  Search,
  XCircle,
  Lock,
  Globe,
  ArrowUpRight,
  ArrowDownLeft,
} from 'lucide-react';
import { formatBytes, formatSpeed } from '../utils/formatters';

/**
 * Live connection tracker.
 *
 * Renders two ways from one implementation. `inline` is the Connections tab -
 * the connection list is a place you sit and watch, so it belongs in the tab
 * strip with the other views, not behind a dialog. The overlay form stays for
 * the per-device entry points (a device row's activity button, the device
 * modal), which open it already filtered to one machine.
 */
export function LiveConnectionsModal({
  isOpen,
  onClose,
  initialDeviceId = null,
  initialRouterId = null,
  inline = false,
}) {
  const { t } = useI18n();
  const [connections, setConnections] = useState([]);
  // The count of connections that matched the *router-side* filters (device,
  // in future protocol/search) before `limit` truncated the list - not the
  // same thing as `connections.length`. The badge uses this to say "250 of
  // 812" instead of just "250", which used to read as "there are exactly 250
  // connections" on a router that in fact had several times that many.
  const [totalMatched, setTotalMatched] = useState(0);
  const [loading, setLoading] = useState(false);
  const [isAutoRefresh, setIsAutoRefresh] = useState(true);
  const [search, setSearch] = useState('');
  const [protocolFilter, setProtocolFilter] = useState('all');
  const [selectedDeviceId, setSelectedDeviceId] = useState(initialDeviceId || '');
  const [killPendingId, setKillPendingId] = useState(null);
  const [killingId, setKillingId] = useState(null);
  const [error, setError] = useState(null);

  const timerRef = useRef(null);

  const fetchConnections = async (showLoading = false) => {
    if (showLoading) setLoading(true);
    setError(null);
    try {
      const params = {};
      if (initialRouterId) params.router_id = initialRouterId;
      if (selectedDeviceId) params.device_id = selectedDeviceId;
      const res = await api.getLiveConnections(params);
      if (res?.data) {
        setConnections(res.data.items || []);
        setTotalMatched(res.data.total ?? (res.data.items || []).length);
      }
    } catch (err) {
      // Deliberately does not clear `connections`: a transient fetch failure
      // (the router briefly unreachable, a slow response) used to wipe the
      // table every time this rejected, because the backend swallowed the
      // same failure into a fake "200 OK, zero connections" and this branch
      // was never reached at all. Now that it reaches here, the right thing
      // is to keep showing the last known-good list and just flag the error.
      console.error('Failed to load live connections:', err);
      setError(err.message || 'Failed to fetch live connections');
    } finally {
      if (showLoading) setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      fetchConnections(true);
    } else {
      setConnections([]);
      setKillPendingId(null);
    }
  }, [isOpen, selectedDeviceId, initialRouterId]);

  // Polling loop
  useEffect(() => {
    if (!isOpen || !isAutoRefresh) {
      if (timerRef.current) clearInterval(timerRef.current);
      return;
    }
    timerRef.current = setInterval(() => {
      fetchConnections(false);
    }, 3000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isOpen, isAutoRefresh, selectedDeviceId, initialRouterId]);

  const handleKill = async (conn) => {
    setKillingId(conn.id);
    setError(null);
    try {
      await api.killConnection(conn.id, {
        router_id: initialRouterId || undefined,
        src_ip: conn.src_ip,
        dst_ip: conn.dst_ip,
      });
      setKillPendingId(null);
      // Remove immediately from UI
      setConnections((prev) => prev.filter((c) => c.id !== conn.id));
    } catch (err) {
      setError(err.message || 'Failed to kill connection');
    } finally {
      setKillingId(null);
    }
  };

  // Filter connections client-side
  const filteredConnections = useMemo(() => {
    const s = search.trim().toLowerCase();
    return connections.filter((c) => {
      // Protocol filter
      if (protocolFilter === 'tcp' && c.protocol !== 'tcp') return false;
      if (protocolFilter === 'udp' && c.protocol !== 'udp') return false;
      if (protocolFilter === 'web' && c.dst_port !== 80 && c.dst_port !== 443) return false;
      if (protocolFilter === 'dns' && c.dst_port !== 53) return false;

      // Search match
      if (s) {
        const text = `${c.src_ip} ${c.dst_ip} ${c.domain || ''} ${c.device_name || ''} ${c.user_name || ''} ${c.country_name || ''} ${c.country_code || ''}`.toLowerCase();
        if (!text.includes(s)) return false;
      }
      return true;
    });
  }, [connections, search, protocolFilter]);

  // Aggregate rates
  const totalUploadRate = useMemo(
    () => filteredConnections.reduce((acc, c) => acc + (c.orig_rate || 0), 0),
    [filteredConnections]
  );
  const totalDownloadRate = useMemo(
    () => filteredConnections.reduce((acc, c) => acc + (c.repl_rate || 0), 0),
    [filteredConnections]
  );

  // `totalMatched` comes from the router-side filters only (device, if any);
  // it knows nothing about the protocol pill or the search box, both applied
  // here in the browser. Comparing it against the raw fetch (`connections`,
  // before those two) is only meaningful when neither is active - otherwise
  // "3 of 812" would misread as "812 connections match your search".
  const isClientFiltered = protocolFilter !== 'all' || search.trim() !== '';
  const isTruncated = !isClientFiltered && totalMatched > connections.length;

  if (!isOpen) return null;

  const body = (
    <>
        {/* Header */}
        <div className="modal-header" style={{ marginBottom: 12, alignItems: 'center' }}>
          <div className="modal-title-group" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div
              className="modal-icon"
              style={{
                background: 'rgba(59, 130, 246, 0.15)',
                color: 'var(--color-primary)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                padding: 6,
                borderRadius: 8,
              }}
            >
              <Activity size={22} className={isAutoRefresh ? 'pulse-slow' : ''} />
            </div>
            <div>
              <h3 style={{ margin: 0, fontSize: '1.2rem', display: 'flex', alignItems: 'center', gap: 8 }}>
                {t('live_connections_title')}
                <span
                  title={isTruncated ? t('connections_truncated_hint', { total: totalMatched }) : undefined}
                  style={{
                    fontSize: 'var(--fs-xs)',
                    fontWeight: 500,
                    padding: '2px 8px',
                    borderRadius: 12,
                    background: 'var(--bg-card-hover)',
                    color: 'var(--text-secondary)',
                  }}
                >
                  {filteredConnections.length}
                  {isTruncated ? ` / ${totalMatched}` : ''} {t('connections_count')}
                </span>
              </h3>
              <div
                className="modal-subtitle"
                style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)', display: 'flex', gap: 10, marginTop: 3 }}
              >
                <span>
                  <ArrowUpRight size={12} style={{ verticalAlign: -1, color: 'var(--color-primary)' }} />{' '}
                  {formatSpeed(totalUploadRate)}
                </span>
                <span>
                  <ArrowDownLeft size={12} style={{ verticalAlign: -1, color: 'var(--color-success)' }} />{' '}
                  {formatSpeed(totalDownloadRate)}
                </span>
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {/* Auto-refresh toggle */}
            <button
              className={`btn btn-sm ${isAutoRefresh ? 'btn-ghost text-primary' : 'btn-ghost'}`}
              onClick={() => setIsAutoRefresh(!isAutoRefresh)}
              title={isAutoRefresh ? t('paused') : t('auto_refresh')}
              style={{ display: 'flex', alignItems: 'center', gap: 5 }}
            >
              {isAutoRefresh ? <Pause size={14} /> : <Play size={14} />}
              <span style={{ fontSize: 'var(--fs-xs)' }}>
                {isAutoRefresh ? t('auto_refresh') : t('paused')}
              </span>
            </button>

            {/* Manual refresh */}
            <button
              className="btn btn-sm btn-ghost"
              onClick={() => fetchConnections(true)}
              disabled={loading}
              title="Refresh"
            >
              <RefreshCw size={14} className={loading ? 'spin' : ''} />
            </button>

            {!inline && (
              <button className="btn-icon" onClick={onClose} style={{ marginLeft: 6 }}>
                <X size={18} />
              </button>
            )}
          </div>
        </div>

        {error && (
          <div className="alert alert-danger" style={{ marginBottom: 12, fontSize: 'var(--fs-xs)' }}>
            {error}
          </div>
        )}

        {/* Filter and Search Bar */}
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 10,
            marginBottom: 12,
            padding: '8px 10px',
            background: 'var(--bg-card-hover)',
            borderRadius: 'var(--radius-sm)',
          }}
        >
          {/* Protocol pills */}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {[
              { key: 'all', label: t('protocol_all') },
              { key: 'tcp', label: 'TCP' },
              { key: 'udp', label: 'UDP' },
              { key: 'web', label: 'Web (80/443)' },
              { key: 'dns', label: 'DNS (53)' },
            ].map((p) => (
              <button
                key={p.key}
                className={`btn btn-xs ${protocolFilter === p.key ? 'btn-primary' : 'btn-ghost'}`}
                style={{ borderRadius: 14, padding: '2px 10px' }}
                onClick={() => setProtocolFilter(p.key)}
              >
                {p.label}
              </button>
            ))}
          </div>

          {/* Search box */}
          <div style={{ position: 'relative', width: 260 }}>
            <Search
              size={14}
              style={{
                position: 'absolute',
                left: 9,
                top: '50%',
                transform: 'translateY(-50%)',
                color: 'var(--text-muted)',
              }}
            />
            <input
              type="text"
              className="form-input"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('search_connections_placeholder')}
              style={{
                paddingLeft: 28,
                paddingRight: search ? 28 : 8,
                paddingTop: 4,
                paddingBottom: 4,
                fontSize: 'var(--fs-xs)',
                height: 30,
              }}
            />
            {search && (
              <button
                type="button"
                onClick={() => setSearch('')}
                style={{
                  position: 'absolute',
                  right: 8,
                  top: '50%',
                  transform: 'translateY(-50%)',
                  background: 'none',
                  border: 'none',
                  color: 'var(--text-muted)',
                  cursor: 'pointer',
                  padding: 0,
                }}
              >
                <X size={12} />
              </button>
            )}
          </div>
        </div>

        {/* Connections Table */}
        <div
          style={{
            flex: 1,
            overflowY: 'auto',
            minHeight: 280,
            border: '1px solid var(--border-color)',
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
                  zIndex: 2,
                  textAlign: 'left',
                  color: 'var(--text-secondary)',
                }}
              >
                <th style={{ padding: '8px 10px', width: 90 }}>Proto</th>
                <th style={{ padding: '8px 10px' }}>Source Client</th>
                <th style={{ padding: '8px 10px' }}>Destination Target</th>
                <th style={{ padding: '8px 10px', textAlign: 'right', width: 130 }}>Rate</th>
                <th style={{ padding: '8px 10px', textAlign: 'right', width: 90 }}>Volume</th>
                <th style={{ padding: '8px 10px', textAlign: 'center', width: 70 }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {filteredConnections.length === 0 ? (
                <tr>
                  <td
                    colSpan={6}
                    style={{
                      textAlign: 'center',
                      padding: '40px 10px',
                      color: 'var(--text-muted)',
                    }}
                  >
                    {loading ? (
                      <RefreshCw size={20} className="spin" style={{ margin: '0 auto' }} />
                    ) : (
                      t('no_connections_found')
                    )}
                  </td>
                </tr>
              ) : (
                filteredConnections.map((c) => {
                  const isTcp = c.protocol === 'tcp';
                  const isPending = killPendingId === c.id;
                  const isKilling = killingId === c.id;

                  return (
                    <tr
                      key={c.id}
                      style={{
                        borderBottom: '1px solid var(--border-color)',
                        transition: 'background 0.1s',
                      }}
                      className="table-row-hover"
                    >
                      {/* Protocol */}
                      <td style={{ padding: '8px 10px', whiteSpace: 'nowrap' }}>
                        <span
                          style={{
                            display: 'inline-block',
                            padding: '2px 6px',
                            borderRadius: 4,
                            fontSize: 'var(--fs-2xs)',
                            fontWeight: 700,
                            textTransform: 'uppercase',
                            background: isTcp ? 'rgba(34, 197, 94, 0.15)' : 'rgba(59, 130, 246, 0.15)',
                            color: isTcp ? 'var(--color-success)' : 'var(--color-primary)',
                          }}
                        >
                          {c.protocol}
                        </span>
                        {c.tcp_state && (
                          <div
                            style={{
                              fontSize: '9px',
                              color: 'var(--text-muted)',
                              textTransform: 'lowercase',
                              marginTop: 2,
                            }}
                          >
                            {c.tcp_state}
                          </div>
                        )}
                      </td>

                      {/* Source */}
                      <td style={{ padding: '8px 10px' }}>
                        <div style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                          {c.device_name || c.src_ip}
                        </div>
                        <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                          {c.src_ip}:{c.src_port || '*'}
                          {c.user_name && ` (${c.user_name})`}
                        </div>
                      </td>

                      {/* Destination */}
                      <td style={{ padding: '8px 10px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span style={{ fontSize: '1rem', lineHeight: 1 }} title={c.country_name || c.country_code}>
                            {c.flag_emoji || '🌐'}
                          </span>
                          <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>
                            {c.domain || c.dst_ip}
                          </span>
                        </div>
                        <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                          {c.dst_ip}:{c.dst_port || '*'}
                          {c.country_code && c.country_code !== 'LOCAL' && ` • ${c.country_name || c.country_code}`}
                        </div>
                      </td>

                      {/* Live Rates */}
                      <td style={{ padding: '8px 10px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                        <div style={{ color: 'var(--color-primary)', fontSize: 'var(--fs-2xs)' }}>
                          ↑ {formatSpeed(c.orig_rate)}
                        </div>
                        <div style={{ color: 'var(--color-success)', fontSize: 'var(--fs-2xs)' }}>
                          ↓ {formatSpeed(c.repl_rate)}
                        </div>
                      </td>

                      {/* Total Volume */}
                      <td style={{ padding: '8px 10px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                        <span style={{ fontWeight: 500 }}>{formatBytes(c.total_bytes)}</span>
                      </td>

                      {/* Action */}
                      <td style={{ padding: '8px 10px', textAlign: 'center', position: 'relative' }}>
                        {c.is_immune ? (
                          <span title={t('immune_connection_hint')} style={{ color: 'var(--text-muted)', cursor: 'help' }}>
                            <Lock size={15} />
                          </span>
                        ) : isPending ? (
                          <div
                            style={{
                              position: 'absolute',
                              right: 10,
                              top: '50%',
                              transform: 'translateY(-50%)',
                              background: 'var(--bg-card)',
                              border: '1px solid var(--color-danger)',
                              borderRadius: 6,
                              padding: '4px 6px',
                              boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
                              zIndex: 10,
                              display: 'flex',
                              alignItems: 'center',
                              gap: 4,
                              whiteSpace: 'nowrap',
                            }}
                          >
                            <button
                              className="btn btn-xs btn-danger"
                              onClick={() => handleKill(c)}
                              disabled={isKilling}
                              style={{ padding: '2px 6px' }}
                            >
                              {isKilling ? '...' : t('kill_confirm_yes')}
                            </button>
                            <button
                              className="btn btn-xs btn-ghost"
                              onClick={() => setKillPendingId(null)}
                              style={{ padding: '2px 6px' }}
                            >
                              {t('cancel')}
                            </button>
                          </div>
                        ) : (
                          <button
                            className="btn-icon"
                            onClick={() => setKillPendingId(c.id)}
                            title={t('kill_connection')}
                            style={{ color: 'var(--color-danger)', padding: 4 }}
                          >
                            <XCircle size={16} />
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
    </>
  );

  if (inline) {
    return (
      // `maxHeight` bounds the flex column so the table's own `flex: 1;
      // overflow-y: auto` below can compute a real height and scroll inside
      // itself. Without it, a flex child with no ancestor of a definite
      // height just grows to fit all its rows - up to 250 of them - and the
      // whole page scrolled instead of this card, with no visible edge to
      // say where the list actually ended.
      <div className="card" style={{ display: 'flex', flexDirection: 'column', padding: '18px 20px', maxHeight: '70vh' }}>
        {body}
      </div>
    );
  }

  return (
    <div className="modal-backdrop" onClick={onClose} style={{ zIndex: 1050 }}>
      <div
        className="modal-card"
        onClick={(e) => e.stopPropagation()}
        style={{
          maxWidth: 1040,
          width: '95vw',
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          padding: '20px 24px',
        }}
      >
        {body}
      </div>
    </div>
  );
}

