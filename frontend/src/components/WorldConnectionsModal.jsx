import React, { useState, useMemo, useCallback, useRef, useEffect } from 'react';
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
  Plus,
  Minus,
  RotateCcw,
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

  // Map Zoom and Pan State
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0, panX: 0, panY: 0 });
  const mapContainerRef = useRef(null);
  const hasDraggedRef = useRef(false);

  // Filter connections to only those with valid geo coordinates
  const geoConnections = useMemo(() => {
    return (connections || []).filter(
      (c) => c && c.lat != null && c.lng != null && c.country_code !== 'LOCAL'
    );
  }, [connections]);

  // Aggregate by country code (Unknown 'UN' always pinned to Antarctica: -78.0, 0.0)
  const countryGroups = useMemo(() => {
    const groups = {};
    for (const c of geoConnections) {
      const code = c.country_code || 'UN';
      const isUnknown = code === 'UN';
      if (!groups[code]) {
        groups[code] = {
          code,
          name: c.country_name || (isUnknown ? 'Unknown' : code),
          lat: isUnknown ? -78.0 : c.lat,
          lng: isUnknown ? 0.0 : c.lng,
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

  // Clamping helper for Pan
  const clampPan = useCallback((newPan, currentZoom) => {
    const vbW = 1000 / currentZoom;
    const vbH = 500 / currentZoom;
    const maxX = Math.max(0, 1000 - vbW);
    const maxY = Math.max(0, 500 - vbH);
    return {
      x: Math.max(0, Math.min(maxX, newPan.x)),
      y: Math.max(0, Math.min(maxY, newPan.y)),
    };
  }, []);

  const setZoomAndCenter = useCallback((targetZoom, centerPoint = null) => {
    const nextZoom = Math.max(1, Math.min(6, targetZoom));
    if (nextZoom === 1) {
      setZoom(1);
      setPan({ x: 0, y: 0 });
      return;
    }

    const currentVbW = 1000 / zoom;
    const currentVbH = 500 / zoom;
    const nextVbW = 1000 / nextZoom;
    const nextVbH = 500 / nextZoom;

    const cx = centerPoint ? centerPoint.x : pan.x + currentVbW / 2;
    const cy = centerPoint ? centerPoint.y : pan.y + currentVbH / 2;

    const nextPan = {
      x: cx - nextVbW / 2,
      y: cy - nextVbH / 2,
    };

    setZoom(nextZoom);
    setPan(clampPan(nextPan, nextZoom));
  }, [zoom, pan, clampPan]);

  const handleZoomIn = () => setZoomAndCenter(zoom * 1.5);
  const handleZoomOut = () => setZoomAndCenter(zoom / 1.5);
  const handleResetZoom = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  // Mouse wheel zoom on map canvas
  useEffect(() => {
    const el = mapContainerRef.current;
    if (!el) return;

    const onWheel = (e) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;

      const rx = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const ry = Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height));

      const currentVbW = 1000 / zoom;
      const currentVbH = 500 / zoom;
      const cursorSvgX = pan.x + rx * currentVbW;
      const cursorSvgY = pan.y + ry * currentVbH;

      const factor = e.deltaY < 0 ? 1.25 : 0.8;
      const nextZoom = Math.max(1, Math.min(6, zoom * factor));
      if (nextZoom === 1) {
        setZoom(1);
        setPan({ x: 0, y: 0 });
        return;
      }

      const nextVbW = 1000 / nextZoom;
      const nextVbH = 500 / nextZoom;
      const nextPan = {
        x: cursorSvgX - rx * nextVbW,
        y: cursorSvgY - ry * nextVbH,
      };

      setZoom(nextZoom);
      setPan(clampPan(nextPan, nextZoom));
    };

    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [zoom, pan, clampPan]);

  // Mouse Drag / Pan handlers
  const handleMouseDown = (e) => {
    if (e.button !== 0 || zoom <= 1) return;
    setIsDragging(true);
    hasDraggedRef.current = false;
    setDragStart({ x: e.clientX, y: e.clientY, panX: pan.x, panY: pan.y });
  };

  const handleMouseMove = (e) => {
    if (!isDragging || zoom <= 1) return;
    const el = mapContainerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    const dxPix = e.clientX - dragStart.x;
    const dyPix = e.clientY - dragStart.y;
    if (Math.abs(dxPix) > 3 || Math.abs(dyPix) > 3) {
      hasDraggedRef.current = true;
    }

    const currentVbW = 1000 / zoom;
    const currentVbH = 500 / zoom;
    const dx = dxPix * (currentVbW / rect.width);
    const dy = dyPix * (currentVbH / rect.height);

    setPan(clampPan({
      x: dragStart.panX - dx,
      y: dragStart.panY - dy,
    }, zoom));
  };

  const handleMouseUp = () => {
    setIsDragging(false);
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
            ref={mapContainerRef}
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
            style={{
              position: 'relative',
              width: '100%',
              background: '#070b16',
              borderRadius: 8,
              border: '1px solid rgba(255, 255, 255, 0.08)',
              overflow: 'hidden',
              boxShadow: 'inset 0 2px 12px rgba(0,0,0,0.7)',
              cursor: isDragging ? 'grabbing' : zoom > 1 ? 'grab' : 'default',
              userSelect: 'none',
            }}
          >
            {/* Zoom / Pan Controls Overlay */}
            <div
              style={{
                position: 'absolute',
                top: 12,
                right: 12,
                display: 'flex',
                flexDirection: 'column',
                gap: 4,
                background: 'rgba(10, 15, 29, 0.85)',
                backdropFilter: 'blur(8px)',
                padding: 4,
                borderRadius: 8,
                border: '1px solid rgba(255, 255, 255, 0.15)',
                zIndex: 10,
                boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
              }}
            >
              <button
                type="button"
                onClick={handleZoomIn}
                title="Zoom in"
                disabled={zoom >= 6}
                style={{
                  background: 'none',
                  border: 'none',
                  color: zoom >= 6 ? 'var(--text-muted, #475569)' : '#ffffff',
                  cursor: zoom >= 6 ? 'default' : 'pointer',
                  padding: 4,
                  borderRadius: 4,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Plus size={14} />
              </button>
              <div
                style={{
                  fontSize: '9px',
                  fontWeight: 600,
                  textAlign: 'center',
                  color: 'var(--text-secondary, #94a3b8)',
                  padding: '2px 0',
                  userSelect: 'none',
                  fontFamily: 'monospace',
                }}
              >
                {Math.round(zoom * 100)}%
              </div>
              <button
                type="button"
                onClick={handleZoomOut}
                title="Zoom out"
                disabled={zoom <= 1}
                style={{
                  background: 'none',
                  border: 'none',
                  color: zoom <= 1 ? 'var(--text-muted, #475569)' : '#ffffff',
                  cursor: zoom <= 1 ? 'default' : 'pointer',
                  padding: 4,
                  borderRadius: 4,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                }}
              >
                <Minus size={14} />
              </button>
              {zoom > 1 && (
                <button
                  type="button"
                  onClick={handleResetZoom}
                  title="Reset view"
                  style={{
                    background: 'none',
                    border: 'none',
                    color: '#38bdf8',
                    cursor: 'pointer',
                    padding: 4,
                    borderRadius: 4,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <RotateCcw size={12} />
                </button>
              )}
            </div>

            <svg
              viewBox={`${pan.x} ${pan.y} ${1000 / zoom} ${500 / zoom}`}
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
                const scaleFactor = Math.pow(zoom, 0.45);
                const nodeRadius = Math.min(18, Math.max(5.5, (5 + Math.log2(g.count + 1) * 3) / scaleFactor));
                const badgeScale = Math.pow(zoom, 0.35);

                return (
                  <g
                    key={g.code}
                    onClick={(e) => {
                      if (hasDraggedRef.current) return;
                      e.stopPropagation();
                      setSelectedCountryCode(g.code);
                    }}
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
                      strokeWidth={1.5 / badgeScale}
                      opacity={isSelected ? 0.9 : 0.6}
                    />

                    {/* Central Core Circle */}
                    <circle
                      cx={x}
                      cy={y}
                      r={nodeRadius}
                      fill={isSelected ? '#f59e0b' : '#0284c7'}
                      stroke="#ffffff"
                      strokeWidth={2 / badgeScale}
                    />

                    {/* Socket Count Badge inside circle */}
                    <text
                      x={x}
                      y={y + (3.5 / badgeScale)}
                      textAnchor="middle"
                      fill="#ffffff"
                      fontSize={`${Math.max(6, (nodeRadius > 10 ? 10 : 8) / badgeScale)}px`}
                      fontWeight="bold"
                      pointerEvents="none"
                    >
                      {g.count}
                    </text>

                    {/* Map Badge with ISO code */}
                    <g transform={`translate(${x + nodeRadius + (4 / badgeScale)}, ${y + (4 / badgeScale)})`}>
                      <rect
                        x={-2 / badgeScale}
                        y={-11 / badgeScale}
                        width={(g.code.length * 8 + 14) / badgeScale}
                        height={16 / badgeScale}
                        rx={4 / badgeScale}
                        fill="rgba(10, 15, 29, 0.9)"
                        stroke={isSelected ? '#f59e0b' : 'rgba(255, 255, 255, 0.25)'}
                        strokeWidth={1 / badgeScale}
                      />
                      <text
                        x={5 / badgeScale}
                        y={1 / badgeScale}
                        fill="#ffffff"
                        fontSize={`${Math.max(7, 10 / badgeScale)}px`}
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
