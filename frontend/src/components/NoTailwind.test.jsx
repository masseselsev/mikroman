import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

/**
 * This project styles with CSS custom properties and its own `btn` / `card` /
 * `modal-*` classes. Tailwind is not installed and there is no build step that
 * would generate its utilities.
 *
 * Two components nonetheless shipped written entirely in Tailwind
 * (`fixed inset-0 z-50 bg-slate-900 rounded-xl ...`). Every one of those class
 * names was inert, so the backups modal rendered as unstyled text at the bottom
 * of the page rather than as an overlay, and the firmware modal was barely
 * readable. Nothing caught it: the classes are valid strings, the components
 * mount, and their tests asserted on text content only.
 *
 * So the check is on the source. If Tailwind is ever genuinely adopted, delete
 * this file in the same commit that adds the dependency.
 */

const here = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(here, '..');

/** Utilities that only ever come from Tailwind - no CSS here defines them. */
const TAILWIND_ONLY = [
  /\bbg-(slate|gray|zinc|neutral|stone|emerald|indigo|rose|amber|cyan|red|green|blue)-\d{2,3}\b/,
  /\btext-(slate|gray|zinc|neutral|stone|emerald|indigo|rose|amber|cyan|red|green|blue)-\d{2,3}\b/,
  /\bborder-(slate|gray|zinc|neutral|stone|emerald|indigo|rose|amber|cyan)-\d{2,3}\b/,
  /\bfixed inset-0\b/,
  /\b(px|py|pt|pb|pl|pr|mx|my|mt|mb|ml|mr|gap|space-[xy])-\d+(\.\d+)?\b/,
  /\b(w|h)-\d+(\.\d+)?\b/,
  /\brounded-(sm|md|lg|xl|2xl|full)\b/,
  /\bdark:[a-z-]+/,
];

function collectSourceFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...collectSourceFiles(full));
    else if (/\.jsx?$/.test(entry.name) && !/\.test\.jsx?$/.test(entry.name)) out.push(full);
  }
  return out;
}

describe('styling stays on the project’s own CSS', () => {
  it('has no Tailwind dependency to back those class names', () => {
    const pkg = JSON.parse(fs.readFileSync(path.resolve(SRC, '..', 'package.json'), 'utf8'));
    const deps = { ...(pkg.dependencies || {}), ...(pkg.devDependencies || {}) };
    expect(Object.keys(deps).some((d) => d.includes('tailwind'))).toBe(false);
  });

  it('uses no Tailwind-only utility classes in any component', () => {
    const offenders = [];
    for (const file of collectSourceFiles(SRC)) {
      const text = fs.readFileSync(file, 'utf8');
      // Only look inside className strings; prose in comments is not markup.
      for (const m of text.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\})/g)) {
        const value = m[1] || m[2] || '';
        const hit = TAILWIND_ONLY.find((re) => re.test(value));
        if (hit) offenders.push(`${path.relative(SRC, file)}: "${value.slice(0, 70)}"`);
      }
    }
    expect(offenders, `Tailwind classes with nothing to define them:\n${offenders.join('\n')}`)
      .toEqual([]);
  });
});
