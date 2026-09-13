import React from 'react';
import * as Flags from 'country-flag-icons/react/3x2';

let regionNames;
try {
  regionNames = new Intl.DisplayNames(['en'], { type: 'region' });
} catch {
  regionNames = null;
}

/**
 * CountryFlag - Crisp Vector SVG Country Flags
 *
 * Reliably renders vector country flags for all ISO-3166-1 alpha-2 countries and territories (260+).
 * Custom representations for 'UN' (Global Internet) and 'LOCAL' (Local Network).
 * Falls back gracefully to a stylized two-letter ISO badge for unrecognized regions.
 */
export function CountryFlag({ code, size = 18, style = {}, className = '' }) {
  const c = (code || '').toUpperCase().trim();
  const width = Math.round(size * 1.33); // Standard 4:3 / 3:2 flag ratio
  const height = size;

  const flagStyle = {
    display: 'inline-block',
    verticalAlign: 'middle',
    borderRadius: 3,
    overflow: 'hidden',
    boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
    flexShrink: 0,
    ...style,
  };

  if (c === 'UN') {
    return (
      <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Global Internet" role="img">
        <rect width="640" height="480" fill="#4b92db" />
        <circle cx="320" cy="240" r="130" fill="none" stroke="#fff" strokeWidth="12" />
        <ellipse cx="320" cy="240" rx="75" ry="130" fill="none" stroke="#fff" strokeWidth="10" />
        <line x1="190" y1="240" x2="450" y2="240" stroke="#fff" strokeWidth="10" />
        <line x1="225" y1="175" x2="415" y2="175" stroke="#fff" strokeWidth="8" />
        <line x1="225" y1="305" x2="415" y2="305" stroke="#fff" strokeWidth="8" />
      </svg>
    );
  }

  if (c === 'LOCAL') {
    return (
      <svg width={width} height={height} viewBox="0 0 24 24" style={flagStyle} className={className} aria-label="Local Network" role="img" fill="none" stroke="#10b981" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect width="24" height="24" fill="rgba(16, 185, 129, 0.15)" stroke="none" />
        <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" transform="scale(0.7) translate(5, 5)" />
      </svg>
    );
  }

  const FlagComponent = Flags[c];
  if (FlagComponent) {
    let name = c;
    if (regionNames) {
      try {
        name = regionNames.of(c) || c;
      } catch {
        name = c;
      }
    }
    return (
      <FlagComponent
        width={width}
        height={height}
        style={flagStyle}
        className={className}
        title={name}
        aria-label={name}
        role="img"
      />
    );
  }

  // Graceful ISO code badge fallback for unlisted countries
  if (c && c.length === 2) {
    return (
      <svg width={width} height={height} viewBox="0 0 64 48" style={flagStyle} className={className} aria-label={code} role="img">
        <rect width="64" height="48" fill="#1e293b" />
        <rect x="2" y="2" width="60" height="44" fill="none" stroke="rgba(255, 255, 255, 0.25)" strokeWidth="2" rx="3" />
        <text
          x="32"
          y="32"
          fill="#38bdf8"
          fontSize="22px"
          fontWeight="900"
          textAnchor="middle"
          fontFamily="monospace"
          letterSpacing="1px"
        >
          {c}
        </text>
      </svg>
    );
  }

  return (
    <svg width={width} height={height} viewBox="0 0 24 24" style={flagStyle} className={className} aria-label={code || 'World'} role="img" fill="none" stroke="#3b82f6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect width="24" height="24" fill="rgba(59, 130, 246, 0.15)" stroke="none" />
      <circle cx="12" cy="12" r="10" transform="scale(0.8) translate(3, 3)" />
      <line x1="2" y1="12" x2="22" y2="12" transform="scale(0.8) translate(3, 3)" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" transform="scale(0.8) translate(3, 3)" />
    </svg>
  );
}

export default CountryFlag;
