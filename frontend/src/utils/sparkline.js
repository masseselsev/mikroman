/**
 * Smooth path generation for the telemetry sparklines.
 *
 * Deliberately *not* an SVG filter or a CSS effect. Those are compositor work
 * on every frame, and an animated `filter` is what pinned this app's GPU copy
 * engine at 69% for as long as the page was open. This is plain arithmetic run
 * once per telemetry tick: for a 60-point line it is a few hundred multiplies,
 * and the browser rasterises a `<path>` exactly as cheaply as the `<polyline>`
 * it replaces. The cost is not measurable.
 *
 * The interpolation is monotone cubic Hermite (Fritsch–Carlson), not the more
 * common Catmull-Rom. That choice matters for a metrics graph: Catmull-Rom
 * overshoots around sharp changes, so a spike from 0 to 300 Kbps would be drawn
 * dipping below zero on the way in and cresting above the true peak on the way
 * out. A bandwidth chart must not draw traffic that did not happen. Monotone
 * interpolation is guaranteed to stay within the range of the data it connects.
 */

/**
 * Cubic Bézier control points for a monotone spline through `points`.
 * Returns an SVG path string, or null when there is nothing to draw.
 */
export function smoothLinePath(points) {
  if (!Array.isArray(points) || points.length === 0) return null;
  if (points.length === 1) {
    const { x, y } = points[0];
    return `M ${fmt(x)},${fmt(y)}`;
  }
  if (points.length === 2) {
    // Two points define a straight line; there is no curve to fit.
    return `M ${fmt(points[0].x)},${fmt(points[0].y)} L ${fmt(points[1].x)},${fmt(points[1].y)}`;
  }

  const tangents = monotoneTangents(points);
  let d = `M ${fmt(points[0].x)},${fmt(points[0].y)}`;

  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i];
    const p1 = points[i + 1];
    const h = p1.x - p0.x;
    // A third of the interval is the standard Hermite-to-Bézier conversion.
    const c1x = p0.x + h / 3;
    const c1y = p0.y + (tangents[i] * h) / 3;
    const c2x = p1.x - h / 3;
    const c2y = p1.y - (tangents[i + 1] * h) / 3;
    d += ` C ${fmt(c1x)},${fmt(c1y)} ${fmt(c2x)},${fmt(c2y)} ${fmt(p1.x)},${fmt(p1.y)}`;
  }

  return d;
}

/**
 * The same curve, closed down to a baseline so it can be filled.
 */
export function smoothAreaPath(points, baselineY) {
  const line = smoothLinePath(points);
  if (!line || points.length < 2) return null;
  const first = points[0];
  const last = points[points.length - 1];
  return `${line} L ${fmt(last.x)},${fmt(baselineY)} L ${fmt(first.x)},${fmt(baselineY)} Z`;
}

/**
 * Fritsch–Carlson tangents: the step that makes the curve non-overshooting.
 */
function monotoneTangents(points) {
  const n = points.length;
  const secants = new Array(n - 1);

  for (let i = 0; i < n - 1; i++) {
    const dx = points[i + 1].x - points[i].x;
    // Guard against duplicate x values, which would divide by zero.
    secants[i] = dx === 0 ? 0 : (points[i + 1].y - points[i].y) / dx;
  }

  const tangents = new Array(n);
  tangents[0] = secants[0];
  tangents[n - 1] = secants[n - 2];
  for (let i = 1; i < n - 1; i++) {
    tangents[i] = (secants[i - 1] + secants[i]) / 2;
  }

  // Flat segments first, in their own pass. Doing this inside the clamping loop
  // let a later segment's zeroing be read before it was applied.
  for (let i = 0; i < n - 1; i++) {
    if (secants[i] === 0) {
      // A flat segment must stay flat, or the curve would bulge off a plateau.
      tangents[i] = 0;
      tangents[i + 1] = 0;
    }
  }

  for (let i = 0; i < n - 1; i++) {
    if (secants[i] === 0) continue;

    let alpha = tangents[i] / secants[i];
    let beta = tangents[i + 1] / secants[i];

    // A negative ratio means the tangent points against the segment: a local
    // extremum. Flatten it so the curve turns at the sample rather than sailing
    // past it. The ratios must be updated too - computing the magnitude below
    // from the pre-clamp values let the curve overshoot its own data, which is
    // exactly the defect this interpolation exists to prevent.
    if (alpha < 0) {
      tangents[i] = 0;
      alpha = 0;
    }
    if (beta < 0) {
      tangents[i + 1] = 0;
      beta = 0;
    }

    const magnitude = alpha * alpha + beta * beta;
    if (magnitude > 9) {
      const tau = 3 / Math.sqrt(magnitude);
      tangents[i] = tau * alpha * secants[i];
      tangents[i + 1] = tau * beta * secants[i];
    }
  }

  return tangents;
}

/** Two decimals is well past sub-pixel; more only bloats the path string. */
function fmt(value) {
  return Number.isFinite(value) ? Number(value.toFixed(2)) : 0;
}
