import React from 'react';

/**
 * A compact figure tile: icon, label, value, one line of context.
 *
 * The analytics range totals used to be four full-size cards, each 220px wide
 * with a 44px icon and a 2xl figure. On a laptop that strip alone consumed the
 * first screen and pushed the breakdown - the part of the page anyone actually
 * came for - below the fold. These are a reference strip, not the subject of
 * the page, so they are sized like one.
 *
 * Layout lives in `.stat-strip` / `.stat-tile` in index.css rather than inline,
 * so the four tiles cannot drift apart as they are edited.
 */
export function StatTile({ icon, tone, tint, label, value, valueColor, sub, title, onClick }) {
  const clickable = typeof onClick === 'function';
  return (
    <div
      className={`stat-tile${clickable ? ' stat-tile-clickable' : ''}`}
      title={title}
      onClick={onClick}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
    >
      <div className="stat-tile-icon" style={{ background: tint, color: tone }}>
        {icon}
      </div>
      <div className="stat-tile-body">
        <div className="stat-tile-label">{label}</div>
        <div className="stat-tile-value font-mono" style={valueColor ? { color: valueColor } : undefined}>
          {value}
        </div>
        {sub ? <div className="stat-tile-sub">{sub}</div> : null}
      </div>
    </div>
  );
}
