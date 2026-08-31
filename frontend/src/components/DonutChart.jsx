import React from 'react';

/**
 * A small dependency-free donut chart: concentric SVG arcs drawn with
 * stroke-dasharray, plus an optional legend.
 *
 * Kept deliberately plain to match the hand-rolled bars and sparklines already
 * in this codebase - no charting library is pulled in for two pie charts.
 *
 * Props:
 *   segments   [{ label, value, color }]  - values need not be pre-normalised
 *   size       outer pixel diameter (default 168)
 *   thickness  ring width in px (default 26)
 *   centerLabel / centerSub  text stacked in the hole
 *   formatValue(value) -> string  for legend amounts (optional)
 */
export function DonutChart({
  segments = [],
  size = 168,
  thickness = 26,
  centerLabel,
  centerSub,
  formatValue,
}) {
  const total = segments.reduce((s, seg) => s + Math.max(0, seg.value || 0), 0);
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;

  // Walk the segments, converting each to a dash of the right length at the
  // right offset. A zero total renders a single muted ring so the panel keeps
  // its shape instead of collapsing.
  let acc = 0;
  const arcs = total > 0
    ? segments
        .filter(seg => seg.value > 0)
        .map((seg, i) => {
          const frac = seg.value / total;
          const dash = frac * circumference;
          const arc = {
            key: `${seg.label}-${i}`,
            color: seg.color,
            dasharray: `${dash} ${circumference - dash}`,
            dashoffset: -acc * circumference,
          };
          acc += frac;
          return arc;
        })
    : [];

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        style={{ flexShrink: 0, transform: 'rotate(-90deg)' }}
        role="img"
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--bg-input)"
          strokeWidth={thickness}
        />
        {arcs.map(a => (
          <circle
            key={a.key}
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={a.color}
            strokeWidth={thickness}
            strokeDasharray={a.dasharray}
            strokeDashoffset={a.dashoffset}
            strokeLinecap="butt"
          />
        ))}
        {(centerLabel || centerSub) && (
          <g style={{ transform: 'rotate(90deg)', transformOrigin: 'center' }}>
            {centerLabel && (
              <text
                x="50%"
                y={centerSub ? '46%' : '52%'}
                textAnchor="middle"
                dominantBaseline="middle"
                style={{ fill: 'var(--text-primary)', fontSize: 15, fontWeight: 800, fontFamily: 'var(--font-mono)' }}
              >
                {centerLabel}
              </text>
            )}
            {centerSub && (
              <text
                x="50%"
                y="60%"
                textAnchor="middle"
                dominantBaseline="middle"
                style={{ fill: 'var(--text-muted)', fontSize: 9, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}
              >
                {centerSub}
              </text>
            )}
          </g>
        )}
      </svg>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0, flex: 1 }}>
        {segments.length === 0 && (
          <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--text-muted)' }}>—</span>
        )}
        {segments.map((seg, i) => {
          const pct = total > 0 ? (seg.value / total) * 100 : 0;
          return (
            <div key={`${seg.label}-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-2xs)', minWidth: 0 }}>
              <span style={{ width: 9, height: 9, borderRadius: 2, background: seg.color, flexShrink: 0 }} />
              <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-secondary)' }}>
                {seg.label}
              </span>
              {formatValue && (
                <span className="font-mono" style={{ color: 'var(--text-muted)', flexShrink: 0 }}>
                  {formatValue(seg.value)}
                </span>
              )}
              <span className="font-mono" style={{ color: 'var(--text-primary)', fontWeight: 700, flexShrink: 0, minWidth: 38, textAlign: 'right' }}>
                {pct.toFixed(1)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// A fixed, colour-blind-friendlyish palette cycled for chart segments. The last
// entry is reserved by callers for an aggregated "Other" slice.
export const DONUT_PALETTE = [
  '#0b72c9', '#10b981', '#f59e0b', '#a855f7', '#ef4444',
  '#06b6d4', '#84cc16', '#ec4899', '#f97316', '#6366f1',
];
export const DONUT_OTHER_COLOR = '#64748b';
