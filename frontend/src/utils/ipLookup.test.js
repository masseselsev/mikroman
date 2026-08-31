import { describe, expect, it } from 'vitest';
import { buildLookupUrl, templateErrorKey } from './ipLookup';

/**
 * These carry the most weight in the frontend suite. A URL template is stored
 * user input that ends up as the `href` of a link the user clicks, and a
 * `javascript:` URL there executes in the page's origin with access to the API.
 *
 * The backend validates the same templates, but this is deliberately an
 * independent check rather than a convenience: the side that touches the DOM
 * must not assume the other side sanitised anything.
 */
describe('buildLookupUrl - dangerous schemes', () => {
  it.each([
    'javascript:alert(document.cookie)//{ip}',
    "javascript:fetch('/api/v1/routers').then(r=>r.text())//{ip}",
    'JaVaScRiPt:alert(1)//{ip}',
    "data:text/html,<script>alert('{ip}')</script>",
    'vbscript:msgbox("{ip}")',
    'file:///etc/passwd?{ip}',
  ])('refuses %s', (template) => {
    expect(buildLookupUrl(template, '1.2.3.4')).toBeNull();
  });

  it('is not fooled by a placeholder in front of the colon', () => {
    // A scheme check performed on the raw template, before substitution, would
    // see no scheme at all here.
    expect(buildLookupUrl('{ip}javascript:alert(1)', '1.2.3.4')).toBeNull();
  });

  it('refuses embedded credentials', () => {
    // Following the link would hand these to the remote site.
    expect(buildLookupUrl('https://user:secret@example.com/{ip}', '1.2.3.4')).toBeNull();
  });
});

describe('buildLookupUrl - unusable templates', () => {
  it('refuses a template with no placeholder', () => {
    expect(buildLookupUrl('https://2ip.io/', '1.2.3.4')).toBeNull();
  });

  it('refuses empty input on either side', () => {
    expect(buildLookupUrl('', '1.2.3.4')).toBeNull();
    expect(buildLookupUrl('https://2ip.io/{ip}/', '')).toBeNull();
    expect(buildLookupUrl(null, null)).toBeNull();
  });

  it('refuses an unparseable URL', () => {
    expect(buildLookupUrl('not a url {ip}', '1.2.3.4')).toBeNull();
  });
});

describe('buildLookupUrl - valid templates', () => {
  it('substitutes the address', () => {
    expect(buildLookupUrl('https://2ip.io/{ip}/', '188.113.204.70'))
      .toBe('https://2ip.io/188.113.204.70/');
  });

  it('allows plain http for an internal tool', () => {
    expect(buildLookupUrl('http://tool.lan/lookup?addr={ip}', '10.0.0.1'))
      .toBe('http://tool.lan/lookup?addr=10.0.0.1');
  });

  it('replaces every occurrence, not just the first', () => {
    const url = buildLookupUrl('https://example.com/{ip}/compare/{ip}', '1.2.3.4');
    expect(url).toBe('https://example.com/1.2.3.4/compare/1.2.3.4');
  });

  it('percent-encodes an IPv6 literal', () => {
    expect(buildLookupUrl('https://ipinfo.io/{ip}', '2001:db8::1'))
      .toContain('2001%3Adb8%3A%3A1');
  });

  it('cannot be broken out of by a hostile address', () => {
    // The address arrives from an external echo service, so it is untrusted.
    const url = buildLookupUrl('https://2ip.io/{ip}/', '1.2.3.4/../../evil?x=<script>');
    expect(url.startsWith('https://2ip.io/')).toBe(true);
    expect(url).not.toContain('<script>');
  });
});

describe('templateErrorKey', () => {
  it('reports an empty template', () => {
    expect(templateErrorKey('')).toBe('ip_lookup_err_empty');
    expect(templateErrorKey('   ')).toBe('ip_lookup_err_empty');
  });

  it('reports a missing placeholder', () => {
    expect(templateErrorKey('https://2ip.io/')).toBe('ip_lookup_err_placeholder');
  });

  it('reports a bad scheme', () => {
    expect(templateErrorKey('javascript:alert(1)//{ip}')).toBe('ip_lookup_err_scheme');
    expect(templateErrorKey('2ip.io/{ip}')).toBe('ip_lookup_err_scheme');
  });

  it('accepts a valid template', () => {
    expect(templateErrorKey('https://2ip.io/{ip}/')).toBeNull();
  });
});
