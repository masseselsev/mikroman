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
 * Dynamic zoom & pan with mouse wheel, automatic multi-country clustering at low zoom,
 * smooth separation into individual country nodes upon zooming in, and a structured
 * 5-country-per-column scrollable data grid.
 */
export function WorldConnectionsModal({
  isOpen,
  onClose,
  connections = [],
  routerLocation = null,
  target = null,
  routerName = null,
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

  // Equirectangular projection mapping: width 1000, height 500
  const project = (lat, lng) => {
    const x = ((lng + 180) / 360) * 1000;
    const y = ((90 - lat) / 180) * 500;
    return { x: Math.max(15, Math.min(985, x)), y: Math.max(15, Math.min(485, y)) };
  };

  // Resolve Origin (Router / Instance Location)
  const originGeo = useMemo(() => {
    if (routerLocation?.lat != null && routerLocation?.lng != null) {
      return {
        lat: routerLocation.lat,
        lng: routerLocation.lng,
        name: routerLocation.countryName || 'Router Gateway',
        countryCode: routerLocation.countryCode || '',
        publicIP: routerLocation.publicIP || '',
        isFallback: false,
      };
    }
    // Fallback coordinates (Central Europe: 50.1109, 8.6821)
    return {
      lat: 50.1109,
      lng: 8.6821,
      name: 'Router Gateway',
      countryCode: '',
      publicIP: '',
      isFallback: true,
    };
  }, [routerLocation]);

  const originPoint = useMemo(() => {
    return project(originGeo.lat, originGeo.lng);
  }, [originGeo]);

  const resolvedRouterName = useMemo(() => {
    return routerName || routerLocation?.routerName || routerLocation?.countryName || t('world_map_gateway');
  }, [routerName, routerLocation?.routerName, routerLocation?.countryName, t]);

  const originLabel = useMemo(() => {
    if (target?.type === 'device') {
      return target.name || `Device #${target.id}`;
    }
    if (target?.type === 'user') {
      return target.name || `User #${target.id}`;
    }
    return resolvedRouterName;
  }, [target, resolvedRouterName]);

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
    const nextZoom = Math.max(1, Math.min(20, targetZoom));
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
  const handleWheel = useCallback((e) => {
    e.preventDefault();
    const el = mapContainerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const width = rect.width > 0 ? rect.width : 1000;
    const height = rect.height > 0 ? rect.height : 500;

    const rx = Math.max(0, Math.min(1, ((e.clientX || 0) - (rect.left || 0)) / width));
    const ry = Math.max(0, Math.min(1, ((e.clientY || 0) - (rect.top || 0)) / height));

    const currentVbW = 1000 / zoom;
    const currentVbH = 500 / zoom;
    const cursorSvgX = pan.x + rx * currentVbW;
    const cursorSvgY = pan.y + ry * currentVbH;

    const factor = e.deltaY < 0 ? 1.25 : 0.8;
    const nextZoom = Math.max(1, Math.min(20, zoom * factor));
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
  }, [zoom, pan, clampPan]);

  useEffect(() => {
    const el = mapContainerRef.current;
    if (!el || !isOpen) return;

    el.addEventListener('wheel', handleWheel, { passive: false });
    return () => el.removeEventListener('wheel', handleWheel);
  }, [handleWheel, isOpen]);

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

  // Dynamic zoom-based clustering of country nodes
  // Nearby countries are grouped when zoomed out, separating cleanly as user zooms in
  const displayNodes = useMemo(() => {
    const projected = filteredGroups.map((g) => {
      const pt = project(g.lat, g.lng);
      return {
        ...g,
        x: pt.x,
        y: pt.y,
      };
    });

    // Collision threshold in SVG coordinate units
    // At zoom 1: ~36 units (clusters tightly packed regions like Europe)
    // At higher zoom: scales down smoothly so even adjacent small nations (NL/BE/LU/CH) completely separate
    const clusterDist = Math.max(0.5, 36 / Math.pow(zoom, 0.95));

    const clusters = [];
    const visited = new Set();
    const sorted = [...projected].sort((a, b) => b.count - a.count);

    for (let i = 0; i < sorted.length; i++) {
      const item = sorted[i];
      if (visited.has(item.code)) continue;

      const clusterMembers = [item];
      visited.add(item.code);

      for (let j = i + 1; j < sorted.length; j++) {
        const other = sorted[j];
        if (visited.has(other.code)) continue;
        const d = Math.hypot(item.x - other.x, item.y - other.y);
        if (d < clusterDist) {
          clusterMembers.push(other);
          visited.add(other.code);
        }
      }

      if (clusterMembers.length === 1) {
        clusters.push({
          isCluster: false,
          group: item,
          x: item.x,
          y: item.y,
          count: item.count,
        });
      } else {
        const totalCount = clusterMembers.reduce((sum, m) => sum + m.count, 0);
        const avgX = clusterMembers.reduce((sum, m) => sum + m.x * m.count, 0) / (totalCount || 1);
        const avgY = clusterMembers.reduce((sum, m) => sum + m.y * m.count, 0) / (totalCount || 1);

        clusters.push({
          isCluster: true,
          id: `cluster-${clusterMembers.map(m => m.code).sort().join('-')}`,
          members: clusterMembers,
          x: avgX,
          y: avgY,
          count: totalCount,
          countryCodes: clusterMembers.map(m => m.code),
        });
      }
    }

    return clusters;
  }, [filteredGroups, zoom]);

  if (!isOpen) return null;

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
            paddingBottom: 14,
            borderBottom: '1px solid var(--border-color, rgba(255,255,255,0.08))',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div
              style={{
                width: 36,
                height: 36,
                borderRadius: 8,
                background: 'rgba(59, 130, 246, 0.15)',
                color: 'var(--color-primary, #3b82f6)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Globe size={20} />
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <h3 style={{ margin: 0, fontSize: 'var(--fs-lg, 16px)', fontWeight: 600, color: 'var(--text-primary, #f8fafc)' }}>
                  {t('world_map_title')}
                </h3>
                <span
                  style={{
                    fontSize: 'var(--fs-2xs, 11px)',
                    fontWeight: 600,
                    padding: '2px 8px',
                    borderRadius: 12,
                    background: 'var(--bg-card-hover, rgba(255,255,255,0.08))',
                    color: 'var(--text-secondary, #94a3b8)',
                  }}
                >
                  {countryGroups.length} {t('world_map_countries')} • {geoConnections.length} {t('connections_count')}
                </span>
                {target ? (
                  <span
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 5,
                      fontSize: 'var(--fs-2xs, 11px)',
                      fontWeight: 600,
                      padding: '2px 8px',
                      borderRadius: 10,
                      background: target.type === 'user' ? 'rgba(59, 130, 246, 0.15)' : 'rgba(16, 185, 129, 0.15)',
                      color: target.type === 'user' ? 'var(--color-primary, #3b82f6)' : 'var(--color-success, #10b981)',
                      border: `1px solid ${target.type === 'user' ? 'rgba(59, 130, 246, 0.3)' : 'rgba(16, 185, 129, 0.3)'}`,
                    }}
                    data-testid="world-map-target-badge"
                  >
                    {target.type === 'user' ? (
                      <>👤 {target.name || `User #${target.id}`}</>
                    ) : (
                      <>💻 {target.name || `Device #${target.id}`}</>
                    )}
                  </span>
                ) : (
                  <span
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 6,
                      fontSize: 'var(--fs-2xs, 11px)',
                      fontWeight: 600,
                      padding: '2px 8px',
                      borderRadius: 10,
                      background: 'rgba(16, 185, 129, 0.12)',
                      color: '#34d399',
                      border: '1px solid rgba(16, 185, 129, 0.3)',
                    }}
                    data-testid="world-map-target-badge"
                  >
                    <span>🌐 {resolvedRouterName}</span>
                    {originGeo.countryCode && (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, opacity: 0.85, fontSize: '10px' }}>
                        <CountryFlag code={originGeo.countryCode} size={12} />
                        <span>{originGeo.countryName || originGeo.countryCode}</span>
                      </span>
                    )}
                  </span>
                )}
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 4, fontSize: 'var(--fs-xs, 12px)' }}>
                <span style={{ color: 'var(--color-primary, #3b82f6)', display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                  <ArrowUpRight size={13} /> {formatSpeed(totalUpload, speedUnit)}
                </span>
                <span style={{ color: 'var(--color-success, #10b981)', display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                  <ArrowDownLeft size={13} /> {formatSpeed(totalDownload, speedUnit)}
                </span>
              </div>
            </div>
          </div>

          {/* Search Box & Close Button */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ position: 'relative', width: 220 }}>
              <Search
                size={14}
                style={{
                  position: 'absolute',
                  left: 10,
                  top: '50%',
                  transform: 'translateY(-50%)',
                  color: 'var(--text-muted, #64748b)',
                }}
              />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t('search_connections_placeholder')}
                style={{
                  width: '100%',
                  padding: '6px 28px 6px 30px',
                  borderRadius: 6,
                  border: '1px solid var(--border-color, rgba(255,255,255,0.12))',
                  background: 'var(--bg-card-hover, rgba(255,255,255,0.04))',
                  color: 'var(--text-primary, #f8fafc)',
                  fontSize: 'var(--fs-xs, 12px)',
                  outline: 'none',
                  boxSizing: 'border-box',
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
            onWheel={handleWheel}
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
                <radialGradient id="clusterGlow" cx="50%" cy="50%" r="50%">
                  <stop offset="0%" stopColor="#8b5cf6" stopOpacity="0.85" />
                  <stop offset="60%" stopColor="#6366f1" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="#6366f1" stopOpacity="0" />
                </radialGradient>
                <radialGradient id="clusterGlowSelected" cx="50%" cy="50%" r="50%">
                  <stop offset="0%" stopColor="#f59e0b" stopOpacity="0.9" />
                  <stop offset="60%" stopColor="#d97706" stopOpacity="0.4" />
                  <stop offset="100%" stopColor="#d97706" stopOpacity="0" />
                </radialGradient>
                <pattern id="gridDots" width="20" height="20" patternUnits="userSpaceOnUse">
                  <circle cx="2" cy="2" r="1" fill="rgba(255, 255, 255, 0.04)" />
                </pattern>
                <radialGradient id="originGlow" cx="50%" cy="50%" r="50%">
                  <stop offset="0%" stopColor="#34d399" stopOpacity="0.85" />
                  <stop offset="60%" stopColor="#059669" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="#059669" stopOpacity="0" />
                </radialGradient>
                <style>{`
                  @keyframes mapConnectionFlow {
                    from {
                      stroke-dashoffset: 0;
                    }
                    to {
                      stroke-dashoffset: -20;
                    }
                  }
                  .map-flow-line {
                    animation: mapConnectionFlow 1.3s linear infinite;
                  }
                  .map-flow-line-selected {
                    animation: mapConnectionFlow 0.85s linear infinite;
                  }
                `}</style>
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

              {/* Dynamic Directional Connection Flow Lines */}
              {geoConnections.length > 0 && (
                <g className="connection-flow-lines">
                  {displayNodes.map((node) => {
                    const isSelected = node.isCluster
                      ? activeSelected && node.countryCodes.includes(activeSelected.code)
                      : activeSelected && activeSelected.code === node.group.code;

                    const x1 = originPoint.x;
                    const y1 = originPoint.y;
                    const x2 = node.x;
                    const y2 = node.y;

                    const dist = Math.hypot(x2 - x1, y2 - y1);
                    if (dist < 4) return null;

                    const mx = (x1 + x2) / 2;
                    const my = (y1 + y2) / 2;
                    const lift = Math.min(50, Math.max(12, dist * 0.14));
                    const cx = mx;
                    const cy = Math.max(12, my - lift);

                    const pathD = `M ${x1.toFixed(1)} ${y1.toFixed(1)} Q ${cx.toFixed(1)} ${cy.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`;

                    const totalConns = geoConnections.length || 1;
                    const ratio = node.count / totalConns;
                    const pct = ratio * 100;
                    const scaleFactor = Math.pow(zoom, 0.65);

                    // Dynamic stroke width: base 1.2px + up to 5.5px proportional to sqrt(ratio), scaled by zoom
                    const strokeWidth = Math.max(0.6, (1.2 + 5.5 * Math.sqrt(ratio)) / scaleFactor);
                    const strokeColor = isSelected ? '#f59e0b' : '#38bdf8';
                    const opacity = isSelected ? 0.95 : Math.min(0.85, Math.max(0.35, 0.35 + ratio * 0.5));
                    const nodeKey = node.isCluster ? node.id : node.group.code;
                    const titleText = node.isCluster
                      ? `${node.members.map((m) => m.name).join(', ')}: ${node.count} (${pct.toFixed(1)}%)`
                      : `${node.group.name} (${node.group.code}): ${node.count} (${pct.toFixed(1)}%)`;

                    return (
                      <g key={`flow-${nodeKey}`} data-testid={`flow-line-${nodeKey}`}>
                        <title>{titleText}</title>
                        {/* Subtle glow underlay for selected or high-share lines */}
                        {(isSelected || ratio > 0.2) && (
                          <path
                            d={pathD}
                            fill="none"
                            stroke={strokeColor}
                            strokeWidth={strokeWidth * 2.2}
                            opacity={isSelected ? 0.35 : 0.2}
                            strokeLinecap="round"
                          />
                        )}
                        {/* Animated Directional Flow Line */}
                        <path
                          d={pathD}
                          fill="none"
                          stroke={strokeColor}
                          strokeWidth={strokeWidth}
                          strokeDasharray={`${6 / scaleFactor} ${4 / scaleFactor}`}
                          opacity={opacity}
                          strokeLinecap="round"
                          className={isSelected ? 'map-flow-line-selected' : 'map-flow-line'}
                        />
                      </g>
                    );
                  })}
                </g>
              )}

              {/* Origin Gateway Node (Router / Selected Instance) */}
              {geoConnections.length > 0 && (
                <g
                  className="map-origin-node"
                  data-testid="map-origin-node"
                  style={{ cursor: 'pointer', transition: 'all 0.25s ease-out' }}
                  onClick={() => {
                    setZoomAndCenter(Math.min(20, zoom * 1.5), { x: originPoint.x, y: originPoint.y });
                  }}
                >
                  <title>{`${t('world_map_origin')}: ${originLabel}\n${originGeo.publicIP ? `${originGeo.publicIP}\n` : ''}${geoConnections.length} ${t('connections_count')}`}</title>
                  {/* Origin Glowing Aura */}
                  <circle
                    cx={originPoint.x}
                    cy={originPoint.y}
                    r={18 / Math.pow(zoom, 0.65)}
                    fill="url(#originGlow)"
                  />
                  {/* Outer Pulsing Ring */}
                  <circle
                    cx={originPoint.x}
                    cy={originPoint.y}
                    r={12 / Math.pow(zoom, 0.65)}
                    fill="none"
                    stroke="#10b981"
                    strokeWidth={Math.max(0.2, 1.5 / Math.pow(zoom, 0.65))}
                    strokeDasharray={`${4 / Math.pow(zoom, 0.65)} ${2 / Math.pow(zoom, 0.65)}`}
                    opacity={0.85}
                  />
                  {/* Central Core Circle */}
                  <circle
                    cx={originPoint.x}
                    cy={originPoint.y}
                    r={7.5 / Math.pow(zoom, 0.65)}
                    fill="#059669"
                    stroke="#ffffff"
                    strokeWidth={Math.max(0.25, 2 / Math.pow(zoom, 0.65))}
                  />
                  {/* Center Dot */}
                  <circle
                    cx={originPoint.x}
                    cy={originPoint.y}
                    r={2.8 / Math.pow(zoom, 0.65)}
                    fill="#ffffff"
                  />
                </g>
              )}

              {/* Active Connection Nodes (Dynamic Clusters & Individual Country Markers) */}
              {displayNodes.map((node) => {
                const scaleFactor = Math.pow(zoom, 0.65);

                if (node.isCluster) {
                  const isSelected = activeSelected && node.countryCodes.includes(activeSelected.code);
                  const nodeRadius = Math.min(22, Math.max(1.2, (8 + Math.log2(node.count + 1) * 2.5) / scaleFactor));

                  return (
                    <g
                      key={node.id}
                      onClick={(e) => {
                        if (hasDraggedRef.current) return;
                        e.stopPropagation();
                        setZoomAndCenter(Math.min(20, zoom * 1.8), { x: node.x, y: node.y });
                        if (node.members.length > 0) {
                          setSelectedCountryCode(node.members[0].code);
                        }
                      }}
                      style={{ cursor: 'pointer', transition: 'all 0.25s ease-out' }}
                      className="map-node map-cluster-node"
                      data-testid={node.id}
                    >
                      <title>{`${node.members.map((m) => `${m.name} (${m.count})`).join(', ')}\n${node.count} ${t('connections_count')} (${((node.count / (geoConnections.length || 1)) * 100).toFixed(1)}%)\nClick to zoom in`}</title>

                      {/* Cluster Glowing Aura */}
                      <circle
                        cx={node.x}
                        cy={node.y}
                        r={nodeRadius * 2.4}
                        fill={isSelected ? 'url(#clusterGlowSelected)' : 'url(#clusterGlow)'}
                      />

                      {/* Outer Pulse Ring */}
                      <circle
                        cx={node.x}
                        cy={node.y}
                        r={nodeRadius * 1.6}
                        fill="none"
                        stroke={isSelected ? '#f59e0b' : '#8b5cf6'}
                        strokeWidth={Math.max(0.2, 1.5 / scaleFactor)}
                        strokeDasharray={`${4 / scaleFactor} ${2 / scaleFactor}`}
                        opacity={0.85}
                      />

                      {/* Central Cluster Core */}
                      <circle
                        cx={node.x}
                        cy={node.y}
                        r={nodeRadius}
                        fill={isSelected ? '#d97706' : '#6d28d9'}
                        stroke="#ffffff"
                        strokeWidth={Math.max(0.25, 2 / scaleFactor)}
                      />

                      {/* Socket Count Badge inside circle */}
                      <text
                        x={node.x}
                        y={node.y + nodeRadius * 0.35}
                        textAnchor="middle"
                        fill="#ffffff"
                        fontSize={`${(nodeRadius * 0.9).toFixed(2)}px`}
                        fontWeight="bold"
                        pointerEvents="none"
                      >
                        {node.count}
                      </text>

                      {/* Cluster Label with Member Count */}
                      <g transform={`translate(${node.x + nodeRadius + 3 / scaleFactor}, ${node.y + 3 / scaleFactor})`}>
                        <rect
                          x={-2 / scaleFactor}
                          y={-10 / scaleFactor}
                          width={(14 + node.members.length.toString().length * 6 + 46) / scaleFactor}
                          height={14 / scaleFactor}
                          rx={3 / scaleFactor}
                          fill="rgba(24, 16, 45, 0.92)"
                          stroke={isSelected ? '#f59e0b' : '#8b5cf6'}
                          strokeWidth={Math.max(0.15, 1 / scaleFactor)}
                        />
                        <text
                          x={4 / scaleFactor}
                          y={0.5 / scaleFactor}
                          fill="#e9d5ff"
                          fontSize={`${Math.max(0.5, 7.5 / scaleFactor)}px`}
                          fontWeight="700"
                          letterSpacing="0.2px"
                          pointerEvents="none"
                        >
                          ✦ {node.members.length} {t('world_map_countries')}
                        </text>
                      </g>
                    </g>
                  );
                }

                // Individual Country Node
                const g = node.group;
                const isSelected = activeSelected && activeSelected.code === g.code;
                const nodeRadius = Math.min(18, Math.max(0.9, (5 + Math.log2(g.count + 1) * 2.5) / scaleFactor));

                return (
                  <g
                    key={g.code}
                    onClick={(e) => {
                      if (hasDraggedRef.current) return;
                      e.stopPropagation();
                      setSelectedCountryCode(g.code);
                    }}
                    style={{ cursor: 'pointer', transition: 'all 0.25s ease-out' }}
                    className="map-node"
                    data-testid={`map-node-${g.code}`}
                  >
                    <title>{`${g.name} (${g.code}): ${g.count} ${t('connections_count')} (${((g.count / (geoConnections.length || 1)) * 100).toFixed(1)}%)`}</title>
                    {/* Glowing Aura Ring */}
                    <circle
                      cx={node.x}
                      cy={node.y}
                      r={nodeRadius * 2.2}
                      fill={isSelected ? 'url(#nodeGlowSelected)' : 'url(#nodeGlow)'}
                    />

                    {/* Outer Pulse */}
                    <circle
                      cx={node.x}
                      cy={node.y}
                      r={nodeRadius * 1.5}
                      fill="none"
                      stroke={isSelected ? '#f59e0b' : '#38bdf8'}
                      strokeWidth={Math.max(0.2, 1.5 / scaleFactor)}
                      opacity={isSelected ? 0.9 : 0.6}
                    />

                    {/* Central Core Circle */}
                    <circle
                      cx={node.x}
                      cy={node.y}
                      r={nodeRadius}
                      fill={isSelected ? '#f59e0b' : '#0284c7'}
                      stroke="#ffffff"
                      strokeWidth={Math.max(0.25, 2 / scaleFactor)}
                    />

                    {/* Socket Count Badge inside circle */}
                    <text
                      x={node.x}
                      y={node.y + nodeRadius * 0.35}
                      textAnchor="middle"
                      fill="#ffffff"
                      fontSize={`${(nodeRadius * 0.9).toFixed(2)}px`}
                      fontWeight="bold"
                      pointerEvents="none"
                    >
                      {g.count}
                    </text>

                    {/* Map Badge with ISO code */}
                    <g transform={`translate(${node.x + nodeRadius + 3 / scaleFactor}, ${node.y + 3 / scaleFactor})`}>
                      <rect
                        x={-2 / scaleFactor}
                        y={-10 / scaleFactor}
                        width={(g.code.length * 6.5 + 10) / scaleFactor}
                        height={14 / scaleFactor}
                        rx={3 / scaleFactor}
                        fill="rgba(10, 15, 29, 0.9)"
                        stroke={isSelected ? '#f59e0b' : 'rgba(255, 255, 255, 0.25)'}
                        strokeWidth={Math.max(0.15, 1 / scaleFactor)}
                      />
                      <text
                        x={4 / scaleFactor}
                        y={0.5 / scaleFactor}
                        fill="#ffffff"
                        fontSize={`${Math.max(0.5, 7.5 / scaleFactor)}px`}
                        fontWeight="700"
                        letterSpacing="0.4px"
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

          {/* Selected Region Summary Card & Scrollable 5-Country Grid Table */}
          <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 14, minHeight: 160 }}>
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
                            ISO: {activeSelected.code} • {activeSelected.count} {t('connections_count')} ({((activeSelected.count / (geoConnections.length || 1)) * 100).toFixed(1)}%)
                          </div>
                          <div style={{ marginTop: 4, height: 3, borderRadius: 2, background: 'rgba(255,255,255,0.08)', overflow: 'hidden', width: 140 }}>
                            <div
                              style={{
                                height: '100%',
                                width: `${Math.min(100, (activeSelected.count / (geoConnections.length || 1)) * 100)}%`,
                                background: 'linear-gradient(90deg, #38bdf8, #3b82f6)',
                                borderRadius: 2,
                              }}
                            />
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

            {/* Right Country Table: exactly 5 countries per column, horizontally scrollable */}
            <div
              style={{
                background: 'var(--bg-card-hover, rgba(255,255,255,0.04))',
                border: '1px solid var(--border-color, rgba(255,255,255,0.08))',
                borderRadius: 8,
                padding: '8px 10px',
                overflowX: 'auto',
                overflowY: 'hidden',
                display: 'grid',
                gridTemplateRows: 'repeat(5, 26px)',
                gridAutoFlow: 'column',
                gridAutoColumns: 'minmax(180px, 220px)',
                gap: '5px 8px',
                alignContent: 'start',
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
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        padding: '2px 8px',
                        borderRadius: 6,
                        background: isSelected ? 'rgba(59, 130, 246, 0.22)' : 'rgba(255, 255, 255, 0.03)',
                        border: isSelected ? '1px solid var(--color-primary, #3b82f6)' : '1px solid rgba(255, 255, 255, 0.06)',
                        cursor: 'pointer',
                        color: 'inherit',
                        height: 26,
                        boxSizing: 'border-box',
                        textAlign: 'left',
                        transition: 'all 0.15s ease',
                      }}
                      title={`${g.name} (${g.code}): ${g.count} ${t('connections_count')}, ↓ ${formatSpeed(g.downloadRate, speedUnit)}`}
                    >
                      <CountryFlag code={g.code} size={15} />
                      <span
                        style={{
                          flex: 1,
                          fontSize: 'var(--fs-xs, 12px)',
                          fontWeight: isSelected ? 600 : 500,
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          color: isSelected ? '#ffffff' : 'var(--text-primary, #f8fafc)',
                        }}
                      >
                        {g.name}
                      </span>
                      <span
                        style={{
                          fontSize: '10px',
                          fontFamily: 'monospace',
                          color: 'var(--text-muted, #64748b)',
                          marginRight: 2,
                        }}
                      >
                        {formatSpeed(g.downloadRate, speedUnit)}
                      </span>
                      <span
                        style={{
                          fontSize: '10px',
                          fontWeight: 700,
                          padding: '0 5px',
                          borderRadius: 6,
                          background: isSelected ? 'var(--color-primary, #3b82f6)' : 'rgba(255, 255, 255, 0.08)',
                          color: '#ffffff',
                          minWidth: 16,
                          textAlign: 'center',
                        }}
                      >
                        {g.count} • {((g.count / (geoConnections.length || 1)) * 100).toFixed(0)}%
                      </span>
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

export default WorldConnectionsModal;
