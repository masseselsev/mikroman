import React from 'react';

/**
 * CountryFlag - Crisp Vector SVG Country Flags
 *
 * Reliably renders country flags across Linux, Windows, macOS, Android, and iOS.
 * Resolves the common OS/browser limitation where Unicode Regional Indicator
 * symbols (emoji flags) fallback to two-letter country codes (e.g. "US", "DE").
 */
export function CountryFlag({ code, size = 18, style = {}, className = '' }) {
  const c = (code || '').toUpperCase().trim();
  const width = size * 1.33; // Standard 4:3 flag ratio
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

  switch (c) {
    case 'US':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="United States">
          <rect width="640" height="480" fill="#bd3d44" />
          <path d="M0,37h640v37H0zM0,111h640v37H0zM0,185h640v37H0zM0,258h640v37H0zM0,332h640v37H0zM0,406h640v37H0z" fill="#fff" />
          <rect width="256" height="258" fill="#192f5d" />
          <g fill="#fff" transform="scale(1.2) translate(8, 8)">
            {[0, 1, 2, 3].map(row => (
              <g key={row} transform={`translate(0, ${row * 40})`}>
                {[0, 1, 2, 3, 4].map(col => (
                  <circle key={col} cx={col * 40 + 15} cy="15" r="5" />
                ))}
              </g>
            ))}
          </g>
        </svg>
      );

    case 'DE':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Germany">
          <rect width="640" height="160" fill="#000" />
          <rect y="160" width="640" height="160" fill="#d00" />
          <rect y="320" width="640" height="160" fill="#ffce00" />
        </svg>
      );

    case 'NL':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Netherlands">
          <rect width="640" height="160" fill="#ae1c28" />
          <rect y="160" width="640" height="160" fill="#fff" />
          <rect y="320" width="640" height="160" fill="#21468b" />
        </svg>
      );

    case 'RU':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Russia">
          <rect width="640" height="160" fill="#fff" />
          <rect y="160" width="640" height="160" fill="#0039a6" />
          <rect y="320" width="640" height="160" fill="#d52b1e" />
        </svg>
      );

    case 'FR':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="France">
          <rect width="213.3" height="480" fill="#0055a4" />
          <rect x="213.3" width="213.4" height="480" fill="#fff" />
          <rect x="426.7" width="213.3" height="480" fill="#ef4135" />
        </svg>
      );

    case 'GB':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="United Kingdom">
          <rect width="640" height="480" fill="#012169" />
          <path d="M0,0 L640,480 M640,0 L0,480" stroke="#fff" strokeWidth="60" />
          <path d="M0,0 L640,480 M640,0 L0,480" stroke="#c8102e" strokeWidth="40" />
          <path d="M320,0 V480 M0,240 H640" stroke="#fff" strokeWidth="100" />
          <path d="M320,0 V480 M0,240 H640" stroke="#c8102e" strokeWidth="60" />
        </svg>
      );

    case 'SG':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Singapore">
          <rect width="640" height="240" fill="#ed2939" />
          <rect y="240" width="640" height="240" fill="#fff" />
          <g fill="#fff" transform="translate(100, 120)">
            <circle cx="0" cy="0" r="60" />
            <circle cx="20" cy="0" r="54" fill="#ed2939" />
            <g transform="translate(40, -10)">
              {[0, 1, 2, 3, 4].map(i => {
                const angle = (i * 72 - 90) * (Math.PI / 180);
                return (
                  <circle
                    key={i}
                    cx={Math.cos(angle) * 24}
                    cy={Math.sin(angle) * 24}
                    r="6"
                    fill="#fff"
                  />
                );
              })}
            </g>
          </g>
        </svg>
      );

    case 'JP':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Japan">
          <rect width="640" height="480" fill="#fff" />
          <circle cx="320" cy="240" r="144" fill="#bc002d" />
        </svg>
      );

    case 'AU':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Australia">
          <rect width="640" height="480" fill="#00008b" />
          <g transform="scale(0.5)">
            <rect width="640" height="480" fill="#012169" />
            <path d="M0,0 L640,480 M640,0 L0,480" stroke="#fff" strokeWidth="60" />
            <path d="M0,0 L640,480 M640,0 L0,480" stroke="#c8102e" strokeWidth="40" />
            <path d="M320,0 V480 M0,240 H640" stroke="#fff" strokeWidth="100" />
            <path d="M320,0 V480 M0,240 H640" stroke="#c8102e" strokeWidth="60" />
          </g>
          <g fill="#fff">
            <circle cx="160" cy="360" r="28" />
            <circle cx="480" cy="120" r="14" />
            <circle cx="540" cy="200" r="14" />
            <circle cx="480" cy="380" r="14" />
            <circle cx="420" cy="240" r="14" />
          </g>
        </svg>
      );

    case 'FI':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Finland">
          <rect width="640" height="480" fill="#fff" />
          <rect x="180" width="90" height="480" fill="#002f6c" />
          <rect y="195" width="640" height="90" fill="#002f6c" />
        </svg>
      );

    case 'SE':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Sweden">
          <rect width="640" height="480" fill="#006aa7" />
          <rect x="200" width="80" height="480" fill="#fecc00" />
          <rect y="200" width="640" height="80" fill="#fecc00" />
        </svg>
      );

    case 'PL':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Poland">
          <rect width="640" height="240" fill="#fff" />
          <rect y="240" width="640" height="240" fill="#dc143c" />
        </svg>
      );

    case 'UA':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Ukraine">
          <rect width="640" height="240" fill="#0057b7" />
          <rect y="240" width="640" height="240" fill="#ffd700" />
        </svg>
      );

    case 'UZ':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Uzbekistan">
          <rect width="640" height="155" fill="#0099b5" />
          <rect y="155" width="640" height="10" fill="#ce1126" />
          <rect y="165" width="640" height="150" fill="#fff" />
          <rect y="315" width="640" height="10" fill="#ce1126" />
          <rect y="325" width="640" height="155" fill="#1eb53a" />
          <circle cx="80" cy="78" r="30" fill="#fff" />
          <circle cx="92" cy="78" r="26" fill="#0099b5" />
        </svg>
      );

    case 'KZ':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Kazakhstan">
          <rect width="640" height="480" fill="#00afca" />
          <circle cx="320" cy="220" r="50" fill="#fec50c" />
          <path d="M250,290 Q320,260 390,290 Q320,310 250,290 Z" fill="#fec50c" />
        </svg>
      );

    case 'BR':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Brazil">
          <rect width="640" height="480" fill="#009739" />
          <polygon points="320,50 580,240 320,430 60,240" fill="#fedd00" />
          <circle cx="320" cy="240" r="105" fill="#012169" />
          <path d="M220,240 Q320,220 420,250" stroke="#fff" strokeWidth="14" fill="none" />
        </svg>
      );

    case 'CA':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Canada">
          <rect width="160" height="480" fill="#d80621" />
          <rect x="160" width="320" height="480" fill="#fff" />
          <rect x="480" width="160" height="480" fill="#d80621" />
          <path d="M320,130 L340,190 L380,180 L360,220 L400,240 L370,260 L380,280 L340,270 L330,330 L310,330 L300,270 L260,280 L270,260 L240,240 L280,220 L260,180 L300,190 Z" fill="#d80621" />
        </svg>
      );

    case 'LOCAL':
      return (
        <svg width={width} height={height} viewBox="0 0 24 24" style={flagStyle} className={className} aria-label="Local Network" fill="none" stroke="#10b981" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect width="24" height="24" fill="rgba(16, 185, 129, 0.15)" stroke="none" />
          <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" transform="scale(0.7) translate(5, 5)" />
        </svg>
      );

    default:
      return (
        <svg width={width} height={height} viewBox="0 0 24 24" style={flagStyle} className={className} aria-label={code || 'World'} fill="none" stroke="#3b82f6" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect width="24" height="24" fill="rgba(59, 130, 246, 0.15)" stroke="none" />
          <circle cx="12" cy="12" r="10" transform="scale(0.8) translate(3, 3)" />
          <line x1="2" y1="12" x2="22" y2="12" transform="scale(0.8) translate(3, 3)" />
          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" transform="scale(0.8) translate(3, 3)" />
        </svg>
      );
  }
}

export default CountryFlag;
