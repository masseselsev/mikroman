import React from 'react';

/**
 * The pieces the user and device breakdown tables share.
 *
 * Both tables are the same table with different columns: a sortable header, a
 * share bar in the last cell, and one sort comparator. Keeping them here means
 * the two tabs cannot drift into sorting or rendering percentages differently
 * from one another, which is the sort of difference nobody notices for months.
 */

/** Proportion of the range's traffic, as a bar plus the figure itself. */
export function ShareBar({ pct }) {
  const value = Number(pct) || 0;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
      <div style={{
        flex: 1,
        height: 5,
        minWidth: 40,
        background: 'var(--bg-secondary)',
        borderRadius: 'var(--radius-xs)',
        overflow: 'hidden'
      }}>
        <div style={{
          width: `${Math.min(Math.max(value, 0), 100)}%`,
          height: '100%',
          background: 'var(--color-primary)',
          borderRadius: 'var(--radius-xs)'
        }} />
      </div>
      <span className="font-mono" style={{
        fontSize: 'var(--fs-xs)',
        fontWeight: 700,
        color: value > 0 ? 'var(--color-primary)' : 'var(--text-muted)',
        minWidth: 42,
        textAlign: 'right'
      }}>
        {value.toFixed(1)}%
      </span>
    </div>
  );
}

/**
 * Clickable table header that reports and toggles the active sort.
 */
export function SortHeader({ label, field, sort, onSort, align = 'left' }) {
  const active = sort.field === field;
  return (
    <th
      onClick={() => onSort(field)}
      style={{
        padding: '8px 12px',
        cursor: 'pointer',
        userSelect: 'none',
        textAlign: align,
        color: active ? 'var(--color-primary)' : undefined,
        whiteSpace: 'nowrap'
      }}
      title={label}
    >
      {label}
      <span style={{ opacity: active ? 1 : 0.25, marginLeft: 4 }}>
        {active && sort.dir === 'asc' ? '▲' : '▼'}
      </span>
    </th>
  );
}

/** Sort a copy of `rows` by the active field, numbers and strings alike. */
/** Sort a copy of `rows` by the active field, numbers and strings alike. */
export function sortRows(rows, sort) {
  const sorted = [...rows];
  sorted.sort((a, b) => {
    const av = a[sort.field];
    const bv = b[sort.field];
    if (typeof av === 'string' || typeof bv === 'string') {
      const cmp = String(av ?? '').localeCompare(String(bv ?? ''));
      return sort.dir === 'asc' ? cmp : -cmp;
    }
    const cmp = (Number(av) || 0) - (Number(bv) || 0);
    return sort.dir === 'asc' ? cmp : -cmp;
  });
  return sorted;
}
