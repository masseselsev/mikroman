import { describe, expect, it } from 'vitest';
import { chartMax, chartMin, expectedStepMs, nearestIndex, timeSegments, xPositions } from './chartScales';

/**
 * The two decisions the Router Health charts got wrong, and that must stay right.
 *
 * Both bugs were invisible in isolation: the axis was computed correctly from
 * the numbers it was handed, and those numbers were bucket means with the peaks
 * already averaged away. So these test the contract between them - which value
 * the scale is allowed to be derived from, and where a point belongs in time.
 */

const point = (timestamp) => ({ timestamp });

describe('chartMax - the axis reaches what actually happened', () => {
  it('scales to the peak field when the backend sent one', () => {
    const points = [
      { rx_rate_bps: 1e6, rx_peak_bps: 3e6 },
      { rx_rate_bps: 2e6, rx_peak_bps: 343e6 },
    ];
    expect(chartMax(points, ['rx_peak_bps', 'rx_rate_bps'], 1e5)).toBe(343e6);
  });

  it('falls back to the mean when the response carries no peak', () => {
    // An older backend, or a metric that never had a worst case: the mean is
    // still a real reading and must not collapse the axis to the floor.
    const points = [{ rx_rate_bps: 40e6 }, { rx_rate_bps: 12e6 }];
    expect(chartMax(points, ['rx_peak_bps', 'rx_rate_bps'], 1e5)).toBe(40e6);
  });

  it('keeps the floor for an idle link, so the axis still has a height', () => {
    const idle = [{ rx_rate_bps: 0, rx_peak_bps: 0 }];
    expect(chartMax(idle, ['rx_peak_bps', 'rx_rate_bps'], 1e5)).toBe(1e5);
    expect(chartMax([], ['rx_peak_bps', 'rx_rate_bps'], 1e5)).toBe(1e5);
  });

  it('skips missing and non-numeric readings instead of poisoning the scale', () => {
    const points = [
      { temperature: null, temperature_peak: null },
      { temperature: 48, temperature_peak: 55 },
      { temperature: 'n/a', temperature_peak: undefined },
    ];
    expect(chartMax(points, ['temperature_peak', 'temperature'], 0)).toBe(55);
  });
});

describe('chartMin', () => {
  it('finds the true bottom of a sensor sweep', () => {
    const points = [{ voltage: 24.0, voltage_min: 23.1 }, { voltage: 24.1, voltage_min: 23.9 }];
    expect(chartMin(points, ['voltage_min', 'voltage'])).toBe(23.1);
  });

  it('has no opinion at all when there is nothing to measure', () => {
    // Callers guard this; a default of 0 would quietly report a dead battery.
    expect(chartMin([], ['voltage_min', 'voltage'])).toBe(Number.POSITIVE_INFINITY);
  });
});

describe('xPositions', () => {
  it('places points by their own timestamps', () => {
    const points = [
      point('2026-09-07T00:00:00'),
      point('2026-09-07T12:00:00'),
      point('2026-09-08T00:00:00'),
    ];
    const xs = xPositions(points, 100, 200);
    expect(xs[0]).toBe(100);
    expect(xs[1]).toBeCloseTo(200, 6);
    expect(xs[2]).toBe(300);
  });

  it('leaves a collector outage open as a hole instead of squeezing it shut', () => {
    // Two buckets close together, then three weeks of nothing, then one more.
    const points = [
      point('2026-01-01T00:00:00'),
      point('2026-01-01T02:00:00'),
      point('2026-01-22T00:00:00'),
    ];
    const xs = xPositions(points, 0, 1000);
    // Even-by-index spacing would put the second point at 500, half the axis
    // away from where it happened. By time it sits against the first one.
    expect(xs[1]).toBeLessThan(60);
    expect(xs[2]).toBe(1000);
  });

  it('keeps an unreadable stamp mid-series on the straight line', () => {
    const xs = xPositions(
      [point('2026-01-01T00:00:00'), point('garbage'), point('2026-01-03T00:00:00')],
      0, 100
    );
    expect(xs[1]).toBe(50);
    expect(xs.every(x => Number.isFinite(x))).toBe(true);
  });

  it('falls back to even spacing when the axis cannot be measured', () => {
    expect(xPositions([point('nope'), point('also nope')], 0, 100)).toEqual([0, 100]);
    expect(xPositions([point('2026-09-07T00:00:00')], 0, 100)).toEqual([0]);
    expect(xPositions([], 0, 100)).toEqual([]);
  });
});

describe('nearestIndex', () => {
  it('picks the bucket nearest the pointer, not the bucket nearest a guess', () => {
    const xs = [0, 10, 300];
    expect(nearestIndex(xs, 12)).toBe(1);
    expect(nearestIndex(xs, 250)).toBe(2);
    expect(nearestIndex(xs, -500)).toBe(0);
  });

  it('handles an axis with nothing on it', () => {
    expect(nearestIndex([], 5)).toBe(0);
  });
});

describe('timeSegments', () => {
  const HOUR = 3600 * 1000;
  const at = (offsetMs) => new Date(Date.UTC(2026, 8, 1) + offsetMs).toISOString().slice(0, 19);
  const series = (offsets) => offsets.map(offset => ({ timestamp: at(offset) }));

  it('keeps contiguous buckets in one run', () => {
    expect(timeSegments(series([0, HOUR, 2 * HOUR, 3 * HOUR]), 3600)).toEqual([[0, 1, 2, 3]]);
  });

  it('breaks across a freeze, so it is not drawn as a ramp', () => {
    // Two hours of data, then the host slept, then two more hours.
    expect(timeSegments(series([0, HOUR, 11 * HOUR, 12 * HOUR]), 3600)).toEqual([[0, 1], [2, 3]]);
  });

  it('tolerates one missing bucket instead of fragmenting the line', () => {
    // A two-hour gap against a one-hour step is under the 2.5x tolerance: that
    // is a partial bucket at the edge of the window, not an outage.
    expect(timeSegments(series([0, HOUR, 3 * HOUR, 4 * HOUR]), 3600)).toEqual([[0, 1, 2, 3]]);
  });

  it('derives the step from the data when the API sends none', () => {
    expect(timeSegments(series([0, HOUR, 2 * HOUR, 20 * HOUR, 21 * HOUR]), 0)).toEqual([[0, 1, 2], [3, 4]]);
  });

  it('does not split on a stamp it cannot read', () => {
    const points = [{ timestamp: at(0) }, { timestamp: 'nonsense' }, { timestamp: at(2 * HOUR) }];
    expect(timeSegments(points, 3600)).toEqual([[0, 1, 2]]);
  });

  it('handles the degenerate shapes', () => {
    expect(timeSegments([], 3600)).toEqual([]);
    expect(timeSegments(series([0]), 3600)).toEqual([[0]]);
  });
});

describe('expectedStepMs', () => {
  it('trusts the bucket width the API reports', () => {
    expect(expectedStepMs(14400, [])).toBe(14400 * 1000);
  });

  it('takes the median gap, so a few long outages cannot inflate it away', () => {
    const points = [0, 1, 2, 3, 4].map(h => ({ timestamp: new Date(Date.UTC(2026, 8, 1) + h * 3600e3).toISOString().slice(0, 19) }));
    expect(expectedStepMs(0, points)).toBe(3600 * 1000);
    expect(expectedStepMs(0, [])).toBe(0);
  });
});
