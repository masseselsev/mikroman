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

    case 'IN':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="India">
          <rect width="640" height="160" fill="#ff9933" />
          <rect y="160" width="640" height="160" fill="#fff" />
          <rect y="320" width="640" height="160" fill="#128807" />
          <circle cx="320" cy="240" r="55" fill="none" stroke="#000080" strokeWidth="6" />
          <circle cx="320" cy="240" r="10" fill="#000080" />
          {[0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330].map(deg => (
            <line key={deg} x1="320" y1="185" x2="320" y2="295" stroke="#000080" strokeWidth="3" transform={`rotate(${deg} 320 240)`} />
          ))}
        </svg>
      );

    case 'ES':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Spain">
          <rect width="640" height="120" fill="#c60b1e" />
          <rect y="120" width="640" height="240" fill="#ffc400" />
          <rect y="360" width="640" height="120" fill="#c60b1e" />
          <circle cx="200" cy="240" r="35" fill="#c60b1e" opacity="0.85" />
        </svg>
      );

    case 'CH':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Switzerland">
          <rect width="640" height="480" fill="#d52b1e" />
          <rect x="270" y="110" width="100" height="260" fill="#fff" />
          <rect x="190" y="190" width="260" height="100" fill="#fff" />
        </svg>
      );

    case 'CN':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="China">
          <rect width="640" height="480" fill="#de2910" />
          <polygon points="130,70 142,108 182,108 150,132 162,170 130,146 98,170 110,132 78,108 118,108" fill="#ffde00" />
          <polygon points="210,50 216,68 235,68 220,80 225,98 210,87 195,98 200,80 185,68 204,68" fill="#ffde00" transform="scale(0.7) translate(100, 10)" />
          <polygon points="250,90 256,108 275,108 260,120 265,138 250,127 235,138 240,120 225,108 244,108" fill="#ffde00" transform="scale(0.7) translate(120, 30)" />
          <polygon points="250,150 256,168 275,168 260,180 265,198 250,187 235,198 240,180 225,168 244,168" fill="#ffde00" transform="scale(0.7) translate(120, 60)" />
          <polygon points="210,200 216,218 235,218 220,230 225,248 210,237 195,248 200,230 185,218 204,218" fill="#ffde00" transform="scale(0.7) translate(100, 90)" />
        </svg>
      );

    case 'IE':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Ireland">
          <rect width="213.3" height="480" fill="#169b62" />
          <rect x="213.3" width="213.4" height="480" fill="#fff" />
          <rect x="426.7" width="213.3" height="480" fill="#ff883e" />
        </svg>
      );

    case 'IT':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Italy">
          <rect width="213.3" height="480" fill="#009246" />
          <rect x="213.3" width="213.4" height="480" fill="#fff" />
          <rect x="426.7" width="213.3" height="480" fill="#ce2b37" />
        </svg>
      );

    case 'AT':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Austria">
          <rect width="640" height="160" fill="#c8102e" />
          <rect y="160" width="640" height="160" fill="#fff" />
          <rect y="320" width="640" height="160" fill="#c8102e" />
        </svg>
      );

    case 'BE':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Belgium">
          <rect width="213.3" height="480" fill="#000" />
          <rect x="213.3" width="213.4" height="480" fill="#fdda24" />
          <rect x="426.7" width="213.3" height="480" fill="#ef3340" />
        </svg>
      );

    case 'TR':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Turkey">
          <rect width="640" height="480" fill="#e30a17" />
          <circle cx="280" cy="240" r="120" fill="#fff" />
          <circle cx="310" cy="240" r="96" fill="#e30a17" />
          <polygon points="400,240 420,246 430,230 428,250 445,260 426,263 420,280 412,264 394,260 408,250" fill="#fff" />
        </svg>
      );

    case 'KR':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="South Korea">
          <rect width="640" height="480" fill="#fff" />
          <circle cx="320" cy="240" r="100" fill="#cd2e3a" />
          <path d="M 220 240 A 50 50 0 0 1 320 240 A 50 50 0 0 0 420 240 A 100 100 0 0 1 220 240 Z" fill="#0047a0" />
        </svg>
      );

    case 'DK':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Denmark">
          <rect width="640" height="480" fill="#c60c30" />
          <rect x="180" width="70" height="480" fill="#fff" />
          <rect y="205" width="640" height="70" fill="#fff" />
        </svg>
      );

    case 'NO':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Norway">
          <rect width="640" height="480" fill="#ba0c2f" />
          <rect x="160" width="100" height="480" fill="#fff" />
          <rect y="190" width="640" height="100" fill="#fff" />
          <rect x="185" width="50" height="480" fill="#00205b" />
          <rect y="215" width="640" height="50" fill="#00205b" />
        </svg>
      );

    case 'CZ':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Czech Republic">
          <rect width="640" height="240" fill="#fff" />
          <rect y="240" width="640" height="240" fill="#d7141a" />
          <polygon points="0,0 320,240 0,480" fill="#11457e" />
        </svg>
      );

    case 'RO':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Romania">
          <rect width="213.3" height="480" fill="#002b7f" />
          <rect x="213.3" width="213.4" height="480" fill="#fcd116" />
          <rect x="426.7" width="213.3" height="480" fill="#ce1126" />
        </svg>
      );

    case 'BG':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Bulgaria">
          <rect width="640" height="160" fill="#fff" />
          <rect y="160" width="640" height="160" fill="#00966e" />
          <rect y="320" width="640" height="160" fill="#d62612" />
        </svg>
      );

    case 'GR':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Greece">
          <rect width="640" height="480" fill="#005bae" />
          {[53, 160, 267, 373].map(y => (
            <rect key={y} y={y} width="640" height="53" fill="#fff" />
          ))}
          <rect width="267" height="267" fill="#005bae" />
          <rect x="106" width="55" height="267" fill="#fff" />
          <rect y="106" width="267" height="55" fill="#fff" />
        </svg>
      );

    case 'PT':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Portugal">
          <rect width="256" height="480" fill="#046a38" />
          <rect x="256" width="384" height="480" fill="#da291c" />
          <circle cx="256" cy="240" r="80" fill="#ffcd00" />
          <rect x="236" y="210" width="40" height="60" fill="#fff" rx="4" />
          <rect x="246" y="220" width="20" height="40" fill="#002b7f" rx="2" />
        </svg>
      );

    case 'IL':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Israel">
          <rect width="640" height="480" fill="#fff" />
          <rect y="45" width="640" height="60" fill="#0038b8" />
          <rect y="375" width="640" height="60" fill="#0038b8" />
          <polygon points="320,170 380,274 260,274" fill="none" stroke="#0038b8" strokeWidth="12" />
          <polygon points="320,310 380,206 260,206" fill="none" stroke="#0038b8" strokeWidth="12" />
        </svg>
      );

    case 'AE':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="United Arab Emirates">
          <rect width="640" height="160" fill="#00732f" />
          <rect y="160" width="640" height="160" fill="#fff" />
          <rect y="320" width="640" height="160" fill="#000" />
          <rect width="160" height="480" fill="#f00" />
        </svg>
      );

    case 'SA':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Saudi Arabia">
          <rect width="640" height="480" fill="#006c35" />
          <rect x="180" y="200" width="280" height="30" fill="#fff" rx="6" />
          <rect x="190" y="270" width="260" height="14" fill="#fff" rx="4" />
          <polygon points="170,277 200,265 200,289" fill="#fff" />
        </svg>
      );

    case 'HK':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Hong Kong">
          <rect width="640" height="480" fill="#de2910" />
          <circle cx="320" cy="240" r="90" fill="#fff" />
          <circle cx="320" cy="240" r="60" fill="#de2910" />
          <circle cx="320" cy="240" r="30" fill="#fff" />
        </svg>
      );

    case 'TW':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Taiwan">
          <rect width="640" height="480" fill="#fe0000" />
          <rect width="320" height="240" fill="#000095" />
          <circle cx="160" cy="120" r="50" fill="#fff" />
          <circle cx="160" cy="120" r="35" fill="#000095" />
          <circle cx="160" cy="120" r="28" fill="#fff" />
        </svg>
      );

    case 'AR':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Argentina">
          <rect width="640" height="160" fill="#74acdf" />
          <rect y="160" width="640" height="160" fill="#fff" />
          <rect y="320" width="640" height="160" fill="#74acdf" />
          <circle cx="320" cy="240" r="35" fill="#f6b40e" stroke="#85340a" strokeWidth="2" />
        </svg>
      );

    case 'MX':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Mexico">
          <rect width="213.3" height="480" fill="#006847" />
          <rect x="213.3" width="213.4" height="480" fill="#fff" />
          <rect x="426.7" width="213.3" height="480" fill="#ce1126" />
          <circle cx="320" cy="240" r="40" fill="#996633" opacity="0.8" />
        </svg>
      );

    case 'NZ':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="New Zealand">
          <rect width="640" height="480" fill="#012169" />
          <g transform="scale(0.5)">
            <rect width="640" height="480" fill="#012169" />
            <path d="M0,0 L640,480 M640,0 L0,480" stroke="#fff" strokeWidth="60" />
            <path d="M0,0 L640,480 M640,0 L0,480" stroke="#c8102e" strokeWidth="40" />
            <path d="M320,0 V480 M0,240 H640" stroke="#fff" strokeWidth="100" />
            <path d="M320,0 V480 M0,240 H640" stroke="#c8102e" strokeWidth="60" />
          </g>
          <g fill="#c8102e" stroke="#fff" strokeWidth="4">
            <polygon points="480,100 486,118 505,118 490,130 495,148 480,137 465,148 470,130 455,118 474,118" />
            <polygon points="540,180 545,195 560,195 548,205 552,220 540,211 528,220 532,205 520,195 535,195" />
            <polygon points="480,360 486,378 505,378 490,390 495,408 480,397 465,408 470,390 455,378 474,378" />
            <polygon points="420,240 425,255 440,255 428,265 432,280 420,271 408,280 412,265 400,255 415,255" />
          </g>
        </svg>
      );

    case 'HU':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Hungary">
          <rect width="640" height="160" fill="#ce2939" />
          <rect y="160" width="640" height="160" fill="#fff" />
          <rect y="320" width="640" height="160" fill="#477050" />
        </svg>
      );

    case 'EE':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Estonia">
          <rect width="640" height="160" fill="#0072ce" />
          <rect y="160" width="640" height="160" fill="#000" />
          <rect y="320" width="640" height="160" fill="#fff" />
        </svg>
      );

    case 'LV':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Latvia">
          <rect width="640" height="192" fill="#9e3039" />
          <rect y="192" width="640" height="96" fill="#fff" />
          <rect y="288" width="640" height="192" fill="#9e3039" />
        </svg>
      );

    case 'LT':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Lithuania">
          <rect width="640" height="160" fill="#fdb913" />
          <rect y="160" width="640" height="160" fill="#006a44" />
          <rect y="320" width="640" height="160" fill="#c1272d" />
        </svg>
      );

    case 'IS':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Iceland">
          <rect width="640" height="480" fill="#02529c" />
          <rect x="160" width="100" height="480" fill="#fff" />
          <rect y="190" width="640" height="100" fill="#fff" />
          <rect x="185" width="50" height="480" fill="#dc1e35" />
          <rect y="215" width="640" height="50" fill="#dc1e35" />
        </svg>
      );

    case 'TH':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Thailand">
          <rect width="640" height="80" fill="#a51931" />
          <rect y="80" width="640" height="80" fill="#f4f5f8" />
          <rect y="160" width="640" height="160" fill="#2d2a4a" />
          <rect y="320" width="640" height="80" fill="#f4f5f8" />
          <rect y="400" width="640" height="80" fill="#a51931" />
        </svg>
      );

    case 'VN':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Vietnam">
          <rect width="640" height="480" fill="#da251d" />
          <polygon points="320,110 355,218 469,218 377,285 412,393 320,326 228,393 263,285 171,218 285,218" fill="#ff0" />
        </svg>
      );

    case 'ID':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Indonesia">
          <rect width="640" height="240" fill="#f00" />
          <rect y="240" width="640" height="240" fill="#fff" />
        </svg>
      );

    case 'MY':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Malaysia">
          <rect width="640" height="480" fill="#fff" />
          {[0, 68, 137, 205, 274, 342, 411].map(y => (
            <rect key={y} y={y} width="640" height="34" fill="#cc0000" />
          ))}
          <rect width="320" height="274" fill="#000066" />
          <circle cx="160" cy="137" r="80" fill="#ffcc00" />
          <circle cx="180" cy="137" r="70" fill="#000066" />
          <polygon points="200,137 215,145 208,128 225,130 212,118 227,112 210,108 220,95 204,100 208,85 195,95 192,80 185,96 175,82 175,100" fill="#ffcc00" />
        </svg>
      );

    case 'PH':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Philippines">
          <rect width="640" height="240" fill="#0038a8" />
          <rect y="240" width="640" height="240" fill="#ce1126" />
          <polygon points="0,0 360,240 0,480" fill="#fff" />
          <circle cx="120" cy="240" r="40" fill="#fcd116" />
        </svg>
      );

    case 'GE':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Georgia">
          <rect width="640" height="480" fill="#fff" />
          <rect x="270" width="100" height="480" fill="#f00" />
          <rect y="190" width="640" height="100" fill="#f00" />
          <g fill="#f00">
            <polygon points="125,80 145,80 145,110 175,110 175,130 145,130 145,160 125,160 125,130 95,130 95,110 125,110" />
            <polygon points="495,80 515,80 515,110 545,110 545,130 515,130 515,160 495,160 495,130 465,130 465,110 495,110" />
            <polygon points="125,320 145,320 145,350 175,350 175,370 145,370 145,400 125,400 125,370 95,370 95,350 125,350" />
            <polygon points="495,320 515,320 515,350 545,350 545,370 515,370 515,400 495,400 495,370 465,370 465,350 495,350" />
          </g>
        </svg>
      );

    case 'AM':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Armenia">
          <rect width="640" height="160" fill="#d90012" />
          <rect y="160" width="640" height="160" fill="#0033a0" />
          <rect y="320" width="640" height="160" fill="#f2a800" />
        </svg>
      );

    case 'AZ':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Azerbaijan">
          <rect width="640" height="160" fill="#0092bc" />
          <rect y="160" width="640" height="160" fill="#e4002b" />
          <rect y="320" width="640" height="160" fill="#009739" />
          <circle cx="310" cy="240" r="45" fill="#fff" />
          <circle cx="320" cy="240" r="38" fill="#e4002b" />
          <polygon points="350,240 355,245 362,242 358,248 364,253 357,254 357,261 352,256 346,260 348,253 343,248 349,247" fill="#fff" />
        </svg>
      );

    case 'AQ':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Antarctica">
          <rect width="640" height="480" fill="#002244" />
          <polygon points="0,480 320,160 640,480" fill="#ffffff" />
          <polygon points="320,240 420,400 220,400" fill="#002244" />
        </svg>
      );

    case 'UN':
      return (
        <svg width={width} height={height} viewBox="0 0 640 480" style={flagStyle} className={className} aria-label="Global Internet">
          <rect width="640" height="480" fill="#4b92db" />
          <circle cx="320" cy="240" r="130" fill="none" stroke="#fff" strokeWidth="12" />
          <ellipse cx="320" cy="240" rx="75" ry="130" fill="none" stroke="#fff" strokeWidth="10" />
          <line x1="190" y1="240" x2="450" y2="240" stroke="#fff" strokeWidth="10" />
          <line x1="225" y1="175" x2="415" y2="175" stroke="#fff" strokeWidth="8" />
          <line x1="225" y1="305" x2="415" y2="305" stroke="#fff" strokeWidth="8" />
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
      // Graceful ISO code badge fallback for unlisted countries
      if (c && c.length === 2 && c !== 'UN') {
        return (
          <svg width={width} height={height} viewBox="0 0 64 48" style={flagStyle} className={className} aria-label={code}>
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
