import { describe, expect, it } from 'vitest';
import { smoothAreaPath, smoothBandPath, smoothLinePath } from './sparkline';

/**
 * The property that matters here is non-overshoot.
 *
 * A bandwidth chart must never draw traffic that did not happen. Catmull-Rom -
 * the usual choice for prettifying a line - overshoots around sharp changes, so
 * a jump from 0 to a spike would be rendered dipping below zero going in and
 * cresting above the true peak coming out. Both are lies about the data. These
 * tests pin the monotone behaviour that prevents them.
 */

/** Sample the cubic Bézier segments of a path to get the real drawn extremes. */
function pathExtremes(d) {
  const nums = (s) => s.trim().split(/[ ,]+/).map(Number);
  const tokens = d.match(/[MLC][^MLC]*/g) || [];

  let current = null;
  let min = Infinity;
  let max = -Infinity;
  const note = (y) => {
    min = Math.min(min, y);
    max = Math.max(max, y);
  };

  for (const token of tokens) {
    const cmd = token[0];
    const args = nums(token.slice(1));
    if (cmd === 'M' || cmd === 'L') {
      current = { x: args[0], y: args[1] };
      note(current.y);
    } else if (cmd === 'C') {
      const [c1x, c1y, c2x, c2y, x, y] = args;
      const p0 = current;
      // Walk the curve rather than trusting the control points: a control point
      // may sit outside the range the curve actually reaches.
      for (let t = 0; t <= 1.0001; t += 0.02) {
        const mt = 1 - t;
        const py =
          mt * mt * mt * p0.y +
          3 * mt * mt * t * c1y +
          3 * mt * t * t * c2y +
          t * t * t * y;
        note(py);
      }
      void c1x; void c2x;
      current = { x, y };
    }
  }
  return { min, max };
}

const toPoints = (ys) => ys.map((y, i) => ({ x: i * 10, y }));

describe('smoothLinePath - it must not invent data', () => {
  it('never dips below the lowest sample around a sharp spike', () => {
    // The shape that breaks Catmull-Rom: flat, then a sudden jump.
    const ys = [18, 18, 18, 0, 18, 18];
    const { min } = pathExtremes(smoothLinePath(toPoints(ys)));
    expect(min).toBeGreaterThanOrEqual(Math.min(...ys) - 1e-6);
  });

  it('never rises above the highest sample around a sharp spike', () => {
    const ys = [0, 0, 0, 18, 0, 0];
    const { max } = pathExtremes(smoothLinePath(toPoints(ys)));
    expect(max).toBeLessThanOrEqual(Math.max(...ys) + 1e-6);
  });

  it('stays inside the data range for a realistic burst', () => {
    // A quiet link, a burst, then quiet again - the download tile's usual day.
    const ys = [18, 17.8, 18, 12, 3, 0.5, 9, 17, 18, 18];
    const { min, max } = pathExtremes(smoothLinePath(toPoints(ys)));
    expect(min).toBeGreaterThanOrEqual(Math.min(...ys) - 1e-6);
    expect(max).toBeLessThanOrEqual(Math.max(...ys) + 1e-6);
  });

  it('keeps a plateau flat instead of bulging off it', () => {
    const ys = [9, 9, 9, 9, 9];
    const { min, max } = pathExtremes(smoothLinePath(toPoints(ys)));
    expect(max - min).toBeLessThan(1e-6);
  });

  it('passes exactly through every sample', () => {
    // Smoothing may bend the line between points, never move the points.
    const points = toPoints([5, 12, 3, 17]);
    const d = smoothLinePath(points);
    for (const p of points) {
      expect(d).toContain(`${p.x},${p.y}`);
    }
  });
});

describe('smoothLinePath - degenerate input', () => {
  it('returns null when there is nothing to draw', () => {
    expect(smoothLinePath([])).toBeNull();
    expect(smoothLinePath(null)).toBeNull();
    expect(smoothLinePath(undefined)).toBeNull();
  });

  it('renders a single point as a move', () => {
    expect(smoothLinePath([{ x: 4, y: 7 }])).toBe('M 4,7');
  });

  it('renders two points as a straight line, since there is no curve to fit', () => {
    expect(smoothLinePath([{ x: 0, y: 0 }, { x: 10, y: 5 }])).toBe('M 0,0 L 10,5');
  });

  it('survives duplicate x values without producing NaN', () => {
    const d = smoothLinePath([{ x: 0, y: 1 }, { x: 0, y: 5 }, { x: 10, y: 2 }]);
    expect(d).not.toContain('NaN');
  });

  it('emits no NaN for any well-formed input', () => {
    const d = smoothLinePath(toPoints([0, 18, 0, 18, 0, 18]));
    expect(d).not.toContain('NaN');
    expect(d).not.toContain('Infinity');
  });
});

describe('smoothAreaPath', () => {
  it('closes the curve down to the baseline so it can be filled', () => {
    const d = smoothAreaPath(toPoints([5, 10, 4]), 18);
    expect(d.startsWith('M ')).toBe(true);
    expect(d.endsWith('Z')).toBe(true);
    expect(d).toContain(',18');
  });

  it('returns null when there is no curve', () => {
    expect(smoothAreaPath([], 18)).toBeNull();
    expect(smoothAreaPath([{ x: 0, y: 1 }], 18)).toBeNull();
  });
});

describe('smoothBandPath', () => {
  // Screen coordinates, so a smaller y is the higher reading.
  const upper = toPoints([4, 2, 9, 1]);
  const lower = toPoints([14, 13, 16, 12]);

  it('encloses the region between the two curves', () => {
    const d = smoothBandPath(upper, lower);
    expect(d.startsWith('M ')).toBe(true);
    expect(d.endsWith('Z')).toBe(true);
    // Both edges get walked: the first upper sample opens the shape, the last
    // lower sample closes it. A band that only drew one edge would not fill.
    expect(d).toContain('0,4');
    expect(d).toContain('30,12');
  });

  it('stays inside the combined range of both edges', () => {
    // The same non-overshoot rule the lines follow, applied to the fill: a band
    // that bulged past its own data would report a peak that never happened.
    const ys = [...upper.map(p => p.y), ...lower.map(p => p.y)];
    const { min, max } = pathExtremes(smoothBandPath(upper, lower));
    expect(min).toBeGreaterThanOrEqual(Math.min(...ys) - 1e-6);
    expect(max).toBeLessThanOrEqual(Math.max(...ys) + 1e-6);
  });

  it('holds a constant spread flat instead of pinching it shut', () => {
    const d = smoothBandPath(toPoints([5, 5, 5]), toPoints([10, 10, 10]));
    const { min, max } = pathExtremes(d);
    expect(min).toBeCloseTo(5, 6);
    expect(max).toBeCloseTo(10, 6);
  });

  it('returns null when either edge cannot form a curve', () => {
    expect(smoothBandPath([], [])).toBeNull();
    expect(smoothBandPath(upper, [{ x: 0, y: 1 }])).toBeNull();
    expect(smoothBandPath(null, lower)).toBeNull();
  });
});

describe('cost', () => {
  it('is cheap enough to run on every telemetry tick', () => {
    // 60 points is a full sparkline history; five of these run per tick.
    const points = toPoints(Array.from({ length: 60 }, (_, i) => Math.sin(i) * 9 + 9));
    const started = performance.now();
    for (let i = 0; i < 100; i++) smoothLinePath(points);
    const perCall = (performance.now() - started) / 100;
    // Generous bound - this is a smoke test against an accidental O(n^2), not
    // a benchmark. In practice it is tens of microseconds.
    expect(perCall).toBeLessThan(5);
  });
});
