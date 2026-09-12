import React, { useState, useMemo } from 'react';
import { useI18n } from '../context/I18nContext';
import { useSpeedUnit } from '../context/SpeedUnitContext';
import { formatBytes, formatSpeed } from '../utils/formatters';
import { CountryFlag } from './CountryFlag';
import { WORLD_LAND_PATH } from './worldMapData';
import {
  X,
  Globe,
  ArrowUpRight,
  ArrowDownLeft,
  Search,
} from 'lucide-react';

/**
 * World Connections Map Modal
 *
 * Visualizes active live connections on a high-fidelity 1000x500 Equirectangular SVG world map.
 * Groups connections by country/region, displaying glowing pulsating nodes,
 * socket counts, bandwidth consumption, and top remote destinations.
 */
export function WorldConnectionsModal({
  isOpen,
  onClose,
  connections = [],
}) {
  const { t } = useI18n();
  const { speedUnit } = useSpeedUnit();
  const [selectedCountryCode, setSelectedCountryCode] = useState(null);
  const [search, setSearch] = useState('');

  // Filter connections to only those with valid geo coordinates
  const geoConnections = useMemo(() => {
    return (connections || []).filter(
      (c) => c && c.lat != null && c.lng != null && c.country_code !== 'LOCAL'
    );
  }, [connections]);

  // Aggregate by country code
  const countryGroups = useMemo(() => {
    const groups = {};
    for (const c of geoConnections) {
      const code = c.country_code || 'UN';
      if (!groups[code]) {
        groups[code] = {
          code,
          name: c.country_name || code,
          lat: c.lat,
          lng: c.lng,
          count: 0,
          uploadRate: 0,
          downloadRate: 0,
          totalBytes: 0,
          domains: new Set(),
          connections: [],
        };
      }
      const g = groups[code];
      g.count += 1;
      g.uploadRate += c.orig_rate || 0;
      g.downloadRate += c.repl_rate || 0;
      g.totalBytes += c.total_bytes || 0;
      if (c.domain) g.domains.add(c.domain);
      else if (c.dst_ip) g.domains.add(c.dst_ip);
      g.connections.push(c);
    }
    return Object.values(groups).sort((a, b) => b.count - a.count);
  }, [geoConnections]);

  // Filter country groups by search
  const filteredGroups = useMemo(() => {
    const s = search.trim().toLowerCase();
    if (!s) return countryGroups;
    return countryGroups.filter((g) => {
      return (
        g.name.toLowerCase().includes(s) ||
        g.code.toLowerCase().includes(s) ||
        Array.from(g.domains).some((d) => d.toLowerCase().includes(s))
      );
    });
  }, [countryGroups, search]);

  const activeSelected = useMemo(() => {
    if (!selectedCountryCode) return countryGroups[0] || null;
    return countryGroups.find((g) => g.code === selectedCountryCode) || countryGroups[0] || null;
  }, [countryGroups, selectedCountryCode]);

  const totalUpload = useMemo(
    () => geoConnections.reduce((acc, c) => acc + (c.orig_rate || 0), 0),
    [geoConnections]
  );
  const totalDownload = useMemo(
    () => geoConnections.reduce((acc, c) => acc + (c.repl_rate || 0), 0),
    [geoConnections]
  );

  if (!isOpen) return null;

  // Equirectangular projection mapping: width 1000, height 500
  const project = (lat, lng) => {
    const x = ((lng + 180) / 360) * 1000;
    const y = ((90 - lat) / 180) * 500;
    return { x: Math.max(15, Math.min(985, x)), y: Math.max(15, Math.min(485, y)) };
  };

  return (
    <div className="modal-backdrop" onClick={onClose} style={{ zIndex: 1100 }}>
      <div
        className="modal-card"
        onClick={(e) => e.stopPropagation()}
        style={{
          maxWidth: 1140,
          width: '96vw',
          maxHeight: '92vh',
          display: 'flex',
          flexDirection: 'column',
          padding: '20px 24px',
          background: 'var(--bg-card, #111827)',
          borderRadius: 12,
          boxShadow: '0 20px 50px rgba(0,0,0,0.5)',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div
          className="modal-header"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 14,
            paddingBottom: 12,
            borderBottom: '1px solid var(--border-color, rgba(255,255,255,0.1))',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div
              style={{
                background: 'rgba(59, 130, 246, 0.15)',
                color: 'var(--color-primary, #3b82f6)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                padding: 8,
                borderRadius: 8,
              }}
            >
              <Globe size={24} />
            </div>
            <div>
              <h3 style={{ margin: 0, fontSize: '1.25rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: 10 }}>
                {t('world_map_title')}
                <span
                  style={{
                    fontSize: 'var(--fs-xs, 12px)',
                    fontWeight: 500,
                    padding: '2px 8px',
                    borderRadius: 12,
                    background: 'var(--bg-card-hover, rgba(255,255,255,0.08))',
                    color: 'var(--text-secondary, #94a3b8)',
                  }}
                >
                  {countryGroups.length} {t('world_map_countries')} • {geoConnections.length} {t('connections_count')}
                </span>
              </h3>
              <div
                style={{
                  fontSize: 'var(--fs-xs, 12px)',
                  color: 'var(--text-muted, #64748b)',
                  display: 'flex',
                  gap: 14,
                  marginTop: 4,
                }}
              >
                <span>
                  <ArrowUpRight size={12} style={{ verticalAlign: -1, color: 'var(--color-primary, #3b82f6)' }} />{' '}
                  {formatSpeed(totalUpload, speedUnit)}
                </span>
                <span>
                  <ArrowDownLeft size={12} style={{ verticalAlign: -1, color: 'var(--color-success, #10b981)' }} />{' '}
                  {formatSpeed(totalDownload, speedUnit)}
                </span>
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {/* Search Filter */}
            <div style={{ position: 'relative', width: 220 }}>
              <Search
                size={14}
                style={{
                  position: 'absolute',
                  left: 9,
                  top: '50%',
                  transform: 'translateY(-50%)',
                  color: 'var(--text-muted, #64748b)',
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
                  fontSize: 'var(--fs-xs, 12px)',
                  height: 32,
                  borderRadius: 6,
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
                    color: 'var(--text-muted, #64748b)',
                    cursor: 'pointer',
                    padding: 0,
                  }}
                >
                  <X size={12} />
                </button>
              )}
            </div>

            <button
              className="btn-icon"
              onClick={onClose}
              title={t('close')}
              style={{
                background: 'var(--bg-card-hover, rgba(255,255,255,0.08))',
                border: 'none',
                borderRadius: 6,
                padding: 6,
                cursor: 'pointer',
                color: 'var(--text-secondary, #94a3b8)',
              }}
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Map Body */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14, flex: 1, overflow: 'hidden' }}>
          {/* SVG Canvas Container */}
          <div
            style={{
              position: 'relative',
              width: '100%',
              background: '#070b16',
              borderRadius: 8,
              border: '1px solid rgba(255, 255, 255, 0.08)',
              overflow: 'hidden',
              boxShadow: 'inset 0 2px 12px rgba(0,0,0,0.7)',
            }}
          >
            <svg
              viewBox="0 0 1000 500"
              style={{ width: '100%', height: 'auto', display: 'block' }}
              aria-label="World Connections Map"
            >
              <defs>
                <radialGradient id="nodeGlow" cx="50%" cy="50%" r="50%">
                  <stop offset="0%" stopColor="#38bdf8" stopOpacity="0.8" />
                  <stop offset="60%" stopColor="#0284c7" stopOpacity="0.3" />
                  <stop offset="100%" stopColor="#0284c7" stopOpacity="0" />
                </radialGradient>
                <radialGradient id="nodeGlowSelected" cx="50%" cy="50%" r="50%">
                  <stop offset="0%" stopColor="#f59e0b" stopOpacity="0.9" />
                  <stop offset="60%" stopColor="#d97706" stopOpacity="0.4" />
                  <stop offset="100%" stopColor="#d97706" stopOpacity="0" />
                </radialGradient>
                <pattern id="gridDots" width="20" height="20" patternUnits="userSpaceOnUse">
                  <circle cx="2" cy="2" r="1" fill="rgba(255, 255, 255, 0.04)" />
                </pattern>
              </defs>

              {/* High-tech Canvas Background */}
              <rect width="1000" height="500" fill="#070b16" />
              <rect width="1000" height="500" fill="url(#gridDots)" />

              {/* Graticule Grid */}
              <g stroke="rgba(255, 255, 255, 0.05)" strokeWidth="1" strokeDasharray="3 3">
                <line x1="0" y1="83.3" x2="1000" y2="83.3" />
                <line x1="0" y1="166.7" x2="1000" y2="166.7" />
                <line x1="0" y1="250" x2="1000" y2="250" stroke="rgba(59, 130, 246, 0.2)" strokeDasharray="none" />
                <line x1="0" y1="333.3" x2="1000" y2="333.3" />
                <line x1="0" y1="416.7" x2="1000" y2="416.7" />

                <line x1="166.7" y1="0" x2="166.7" y2="500" />
                <line x1="333.3" y1="0" x2="333.3" y2="500" />
                <line x1="500" y1="0" x2="500" y2="500" stroke="rgba(59, 130, 246, 0.2)" strokeDasharray="none" />
                <line x1="666.7" y1="0" x2="666.7" y2="500" />
                <line x1="833.3" y1="0" x2="833.3" y2="500" />
              </g>

              {/* Authentic Natural Earth 110m Vector Landmasses */}
              <g fill="#162032" stroke="#2a3b55" strokeWidth="0.8" strokeLinejoin="round" strokeLinecap="round">
                <path d={WORLD_LAND_PATH} />
              </g>

              {/* Active Connection Nodes */}
              {filteredGroups.map((g) => {
                const { x, y } = project(g.lat, g.lng);
                const isSelected = activeSelected && activeSelected.code === g.code;
                const nodeRadius = Math.min(18, Math.max(7, 5 + Math.log2(g.count + 1) * 3));

                return (
                  <g
                    key={g.code}
                    onClick={() => setSelectedCountryCode(g.code)}
                    style={{ cursor: 'pointer' }}
                    className="map-node"
                    data-testid={`map-node-${g.code}`}
                  >
                    {/* Glowing Aura Ring */}
                    <circle
                      cx={x}
                      cy={y}
                      r={nodeRadius * 2.2}
                      fill={isSelected ? 'url(#nodeGlowSelected)' : 'url(#nodeGlow)'}
                    />

                    {/* Outer Pulse */}
                    <circle
                      cx={x}
                      cy={y}
                      r={nodeRadius * 1.5}
                      fill="none"
                      stroke={isSelected ? '#f59e0b' : '#38bdf8'}
                      strokeWidth="1.5"
                      opacity={isSelected ? 0.9 : 0.6}
                    />

                    {/* Central Core Circle */}
                    <circle
                      cx={x}
                      cy={y}
                      r={nodeRadius}
                      fill={isSelected ? '#f59e0b' : '#0284c7'}
                      stroke="#ffffff"
                      strokeWidth="2"
                    />

                    {/* Socket Count Badge inside circle */}
                    <text
                      x={x}
                      y={y + 3.5}
                      textAnchor="middle"
                      fill="#ffffff"
                      fontSize={nodeRadius > 10 ? '10px' : '8px'}
                      fontWeight="bold"
                      pointerEvents="none"
                    >
                      {g.count}
                    </text>

                    {/* Map Badge with ISO code */}
                    <g transform={`translate(${x + nodeRadius + 4}, ${y + 4})`}>
                      <rect
                        x="-2"
                        y="-11"
                        width={g.code.length * 8 + 14}
                        height="16"
                        rx="4"
                        fill="rgba(10, 15, 29, 0.9)"
                        stroke={isSelected ? '#f59e0b' : 'rgba(255, 255, 255, 0.25)'}
                        strokeWidth="1"
                      />
                      <text
                        x="5"
                        y="1"
                        fill="#ffffff"
                        fontSize="10px"
                        fontWeight="700"
                        letterSpacing="0.5px"
                        pointerEvents="none"
                      >
                        {g.code}
                      </text>
                    </g>
                  </g>
                );
              })}
            </svg>

            {/* Empty overlay if no Geo connections */}
            {geoConnections.length === 0 && (
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background: 'rgba(10, 15, 29, 0.75)',
                  backdropFilter: 'blur(4px)',
                  color: 'var(--text-muted, #94a3b8)',
                  textAlign: 'center',
                  padding: 20,
                  fontSize: 'var(--fs-sm, 13px)',
                }}
              >
                <div>
                  <Globe size={36} style={{ marginBottom: 10, opacity: 0.5 }} />
                  <div>{t('world_map_no_geo')}</div>
                </div>
              </div>
            )}
          </div>

          {/* Selected Region Summary Card & Country List Strip */}
          <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 14, minHeight: 140 }}>
            {/* Left Detail Card */}
            <div
              style={{
                background: 'var(--bg-card-hover, rgba(255,255,255,0.04))',
                border: '1px solid var(--border-color, rgba(255,255,255,0.08))',
                borderRadius: 8,
                padding: '12px 14px',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
              }}
            >
              {activeSelected ? (
                <>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <CountryFlag code={activeSelected.code} size={24} />
                        <div>
                          <div style={{ fontWeight: 600, fontSize: 'var(--fs-md, 14px)', color: 'var(--text-primary, #f8fafc)' }}>
                            {activeSelected.name}
                          </div>
                          <div style={{ fontSize: 'var(--fs-2xs, 11px)', color: 'var(--text-muted, #64748b)' }}>
                            ISO: {activeSelected.code} • {activeSelected.count} {t('connections_count')}
                          </div>
                        </div>
                      </div>
                      <span
                        style={{
                          background: 'rgba(59, 130, 246, 0.15)',
                          color: 'var(--color-primary, #3b82f6)',
                          fontSize: 'var(--fs-xs, 12px)',
                          fontWeight: 600,
                          padding: '2px 8px',
                          borderRadius: 10,
                        }}
                      >
                        {formatBytes(activeSelected.totalBytes)}
                      </span>
                    </div>

                    <div style={{ marginTop: 10, display: 'flex', gap: 12, fontSize: 'var(--fs-xs, 12px)' }}>
                      <div>
                        <span style={{ color: 'var(--text-muted, #64748b)' }}>↑ </span>
                        <span style={{ color: 'var(--color-primary, #3b82f6)', fontWeight: 500 }}>
                          {formatSpeed(activeSelected.uploadRate, speedUnit)}
                        </span>
                      </div>
                      <div>
                        <span style={{ color: 'var(--text-muted, #64748b)' }}>↓ </span>
                        <span style={{ color: 'var(--color-success, #10b981)', fontWeight: 500 }}>
                          {formatSpeed(activeSelected.downloadRate, speedUnit)}
                        </span>
                      </div>
                    </div>

                    {/* Top targets */}
                    <div style={{ marginTop: 10 }}>
                      <div style={{ fontSize: 'var(--fs-2xs, 11px)', color: 'var(--text-muted, #64748b)', marginBottom: 4 }}>
                        {t('world_map_active_endpoints')}:
                      </div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                        {Array.from(activeSelected.domains)
                          .slice(0, 4)
                          .map((d, i) => (
                            <span
                              key={i}
                              style={{
                                fontSize: '10px',
                                fontFamily: 'monospace',
                                background: 'rgba(255,255,255,0.06)',
                                padding: '2px 6px',
                                borderRadius: 4,
                                color: 'var(--text-secondary, #94a3b8)',
                              }}
                            >
                              {d}
                            </span>
                          ))}
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <div style={{ color: 'var(--text-muted, #64748b)', fontSize: 'var(--fs-xs, 12px)', margin: 'auto' }}>
                  {t('no_connections_found')}
                </div>
              )}
            </div>

            {/* Right Country Horizontal Ranking List */}
            <div
              style={{
                background: 'var(--bg-card-hover, rgba(255,255,255,0.04))',
                border: '1px solid var(--border-color, rgba(255,255,255,0.08))',
                borderRadius: 8,
                padding: '10px 12px',
                overflowX: 'auto',
                display: 'flex',
                gap: 10,
                alignItems: 'center',
              }}
            >
              {filteredGroups.length === 0 ? (
                <div style={{ color: 'var(--text-muted, #64748b)', fontSize: 'var(--fs-xs, 12px)', margin: 'auto' }}>
                  {t('no_connections_found')}
                </div>
              ) : (
                filteredGroups.map((g) => {
                  const isSelected = activeSelected && activeSelected.code === g.code;
                  return (
                    <button
                      key={g.code}
                      onClick={() => setSelectedCountryCode(g.code)}
                      style={{
                        minWidth: 155,
                        textAlign: 'left',
                        padding: '8px 10px',
                        borderRadius: 6,
                        background: isSelected ? 'rgba(59, 130, 246, 0.18)' : 'rgba(255,255,255,0.03)',
                        border: isSelected ? '1px solid var(--color-primary, #3b82f6)' : '1px solid rgba(255,255,255,0.06)',
                        cursor: 'pointer',
                        color: 'inherit',
                        transition: 'all 0.15s ease',
                        flexShrink: 0,
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                        <CountryFlag code={g.code} size={18} />
                        <span
                          style={{
                            fontSize: '10px',
                            fontWeight: 700,
                            padding: '1px 5px',
                            borderRadius: 8,
                            background: isSelected ? 'var(--color-primary, #3b82f6)' : 'rgba(255,255,255,0.1)',
                            color: '#ffffff',
                          }}
                        >
                          {g.count}
                        </span>
                      </div>
                      <div style={{ fontWeight: 600, fontSize: 'var(--fs-xs, 12px)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {g.name}
                      </div>
                      <div style={{ fontSize: '10px', color: 'var(--text-muted, #64748b)', marginTop: 2 }}>
                        ↓ {formatSpeed(g.downloadRate, speedUnit)}
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
