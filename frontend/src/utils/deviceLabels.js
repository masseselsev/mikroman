/**
 * Display labels for a device row.
 *
 * The vendor string the backend stores carries a randomization marker inline:
 * `"Google Pixel (Private MAC)"`, `"Apple (Private MAC)"`, and for a device it
 * cannot attribute at all, `"Private MAC (Randomized)"`.
 *
 * That marker is already shown on the row as a PRIVATE badge, driven by the
 * same `is_randomized_mac` flag. Rendering it twice is not merely redundant:
 * `"(Private MAC)"` is thirteen characters — half of `"Google Pixel (Private
 * MAC)"` — and it was the duplicate half that pushed the vendor past the end of
 * the line and got it truncated to `"Google Pixel ..."`. The badge is the
 * compact form; the line keeps the part that identifies the hardware.
 */

// Both the parenthetical suffix and the standalone "unknown but randomized"
// label the backend falls back to.
const RANDOMIZED_SUFFIX = /\s*\((?:private mac|randomized)\)\s*$/i;
const RANDOMIZED_ONLY = /^\s*private mac(?:\s*\(randomized\))?\s*$/i;

/**
 * The vendor as it should appear on a device row, or null when there is nothing
 * left worth showing.
 *
 * A device whose vendor is *only* the randomization marker has no hardware
 * identity to display, so it returns null and the caller omits the field rather
 * than repeating what the badge already says.
 */
export function displayVendor(vendor) {
  if (!vendor) return null;

  const text = String(vendor).trim();
  if (!text) return null;
  if (RANDOMIZED_ONLY.test(text)) return null;

  const trimmed = text.replace(RANDOMIZED_SUFFIX, '').trim();
  return trimmed || null;
}
