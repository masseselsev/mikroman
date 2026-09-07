/**
 * Scale maths for the Router Health charts.
 *
 * Split out of `MetricCharts` because the two decisions it makes are the ones
 * that were silently wrong: which value a chart is scaled to, and where a point
 * sits along the time axis. Both are pure and worth testing on their own.
 */

/**
 * Resolve one reading off a point, taking the first key that is present.
 *
 * The preference order matters: a bucket carries its mean and its peak, and the
 * peak is what the axis has to reach. Falls back through the list so a response
 * from a backend that predates the peak fields still scales on what it has.
 */
function reading(point, keys) {
  for (const key of keys) {
    const value = point?.[key];
    if (value !== null && value !== undefined && Number.isFinite(value)) return value;
  }
  return null;
}

/**
 * The largest value across `points` for any of `keys` (per point, first present
 * key wins), never below `floor`.
 *
 * Scaling on the mean alone is what flattened the peaks: on the 30-day range a
 * four-hour bucket averages a 340 Mbps minute down beside nothing, and because
 * the axis is derived from the plotted numbers, the chart then *also* squashed
 * every real spike that did survive into the bottom few pixels.
 */
export function chartMax(points, keys, floor = 0) {
  let max = floor;
  for (const point of points || []) {
    const value = reading(point, keys);
    if (value !== null && value > max) max = value;
  }
  return max;
}

/**
 * Smallest value across `points` for any of `keys`, never above `ceiling`.
 *
 * Defaults to no ceiling at all, because the useful answer here is "where did
 * this sensor actually bottom out" — passing a floor-ish default like 0 would
 * pin a 23.1 V sag reading to 0 and draw it off the bottom of the chart.
 */
export function chartMin(points, keys, ceiling = Number.POSITIVE_INFINITY) {
  let min = ceiling;
  for (const point of points || []) {
    const value = reading(point, keys);
    if (value !== null && value < min) min = value;
  }
  return min;
}

/**
 * Milliseconds for a bucket timestamp, or null when it cannot be read.
 *
 * Parsed as browser-local on purpose, exactly like `formatTimeTick` renders it:
 * the backend already shifted the sample into the router's own wall clock and
 * sent it without an offset, so reading it locally shows the same digits the
 * axis labels do. Equal shifts cancel out in every difference below.
 */
function stamp(point) {
  if (!point || !point.timestamp) return null;
  const time = new Date(point.timestamp).getTime();
  return Number.isFinite(time) ? time : null;
}

/**
 * X coordinate for every point, in SVG units.
 *
 * Positions come from the timestamps, not from the array index. Buckets are a
 * fixed width, so index spacing looks harmless until the collector stops - a
 * router offline for three days then squeezes that gap into the space of one
 * bucket and slides every later spike to the left of where it happened. Plotting
 * by time keeps a burst over the minute it was observed and leaves the hole
 * visibly open.
 *
 * Falls back to even spacing when the range cannot be measured (a single point,
 * or timestamps the parser rejects), which is also what the callers draw with
 * when there is nothing to place.
 */
export function xPositions(points, left, width) {
  const count = points?.length || 0;
  if (count === 0) return [];
  if (count === 1) return [left];

  const times = points.map(stamp);
  const first = times[0];
  const last = times[count - 1];
  if (first === null || last === null || last <= first) {
    return times.map((_, i) => left + (i / (count - 1)) * width);
  }

  const span = last - first;
  return times.map((time, i) => {
    // A hole in the data can leave an unreadable stamp mid-series; keep it on the
    // straight line between its neighbours instead of dropping it on x = 0.
    if (time === null) return left + (i / (count - 1)) * width;
    return left + ((time - first) / span) * width;
  });
}

/**
 * Index of the point nearest a horizontal position - the hover target.
 *
 * With the axis placed by time, an x no longer converts to a whole array index
 * by dividing by the width, so the nearest bucket is searched for instead.
 */
export function nearestIndex(xs, x) {
  let best = 0;
  let bestDistance = Infinity;
  for (let i = 0; i < xs.length; i++) {
    const distance = Math.abs(xs[i] - x);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = i;
    }
  }
  return best;
}

/** Nominal step between buckets in ms: what the backend says, else the median gap. */
export function expectedStepMs(bucketSeconds, points) {
  if (bucketSeconds > 0) return bucketSeconds * 1000;

  // Fallback for a response without the field: the most common spacing between
  // readable stamps. The median rather than the mean so a handful of long
  // outages cannot inflate it and hide the rest of them.
  const gaps = [];
  for (let i = 1; i < (points?.length || 0); i++) {
    const a = stamp(points[i - 1]);
    const b = stamp(points[i]);
    if (a !== null && b !== null && b > a) gaps.push(b - a);
  }
  if (gaps.length === 0) return 0;
  gaps.sort((x, y) => x - y);
  return gaps[Math.floor(gaps.length / 2)];
}

/**
 * Split a series into runs of neighbouring buckets.
 *
 * The collector stops when the machine it runs on stops, and the response then
 * simply has no bucket for those hours. Drawing one curve straight through such
 * a hole claims the link carried whatever the interpolation says it carried -
 * and a *filled* peak band makes that claim loud and large: an unmeasured
 * seventeen hours becomes a smooth mountain. So anything more than
 * `tolerance` bucket widths apart starts a new run, and the hole stays blank.
 *
 * Returns an array of index arrays, e.g. [[0, 1, 2], [6, 7]].
 */
export function timeSegments(points, bucketSeconds, tolerance = 2.5) {
  const count = points?.length || 0;
  if (count === 0) return [];
  if (count === 1) return [[0]];

  const step = expectedStepMs(bucketSeconds, points);
  const segments = [];
  let current = [0];

  for (let i = 1; i < count; i++) {
    const a = stamp(points[i - 1]);
    const b = stamp(points[i]);
    // An unreadable stamp is not evidence of an outage; keep the run going and
    // let a real gap show up on whichever pair can actually be measured.
    const gap = (a === null || b === null) ? null : b - a;
    if (step > 0 && gap !== null && gap > step * tolerance) {
      segments.push(current);
      current = [i];
    } else {
      current.push(i);
    }
  }
  segments.push(current);
  return segments;
}
