/**
 * Client-side half of the external IP-lookup feature.
 *
 * The backend already validates every stored template, so this is deliberately
 * a second, independent check rather than a convenience. The template travels
 * from the database into an `href`, and a `javascript:` URL there executes in
 * the page's origin — so the side that touches the DOM must not assume the
 * other side sanitised anything.
 */

export const IP_PLACEHOLDER = '{ip}';

const ALLOWED_PROTOCOLS = ['http:', 'https:'];

/**
 * Turn a template and an address into a URL that is safe to put in an href.
 * Returns null for anything that fails the check, so callers render plain text
 * rather than a link.
 */
export function buildLookupUrl(template, ip) {
  if (!template || !ip) return null;
  if (!template.includes(IP_PLACEHOLDER)) return null;

  const candidate = template.replace(
    new RegExp(escapeRegExp(IP_PLACEHOLDER), 'g'),
    encodeURIComponent(String(ip).trim())
  );

  let parsed;
  try {
    parsed = new URL(candidate);
  } catch {
    return null;
  }

  // The check that matters. `new URL()` happily parses `javascript:alert(1)`.
  if (!ALLOWED_PROTOCOLS.includes(parsed.protocol)) return null;
  if (!parsed.hostname) return null;
  // Credentials in the URL would be handed to the remote site on click.
  if (parsed.username || parsed.password) return null;

  return parsed.toString();
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Validate a template the user is typing, for inline feedback in Settings.
 * Returns null when valid, or a translation key naming what is wrong.
 */
export function templateErrorKey(template) {
  if (!template || !template.trim()) return 'ip_lookup_err_empty';
  if (!template.includes(IP_PLACEHOLDER)) return 'ip_lookup_err_placeholder';
  // Probe with a sample address: a scheme check on the raw template can be
  // fooled by the placeholder sitting in front of the colon.
  if (!buildLookupUrl(template, '192.0.2.1')) return 'ip_lookup_err_scheme';
  return null;
}
