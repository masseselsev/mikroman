import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { translations } from './translations';

/**
 * The UI is authored in English and every string has a Russian counterpart of
 * a comparable length. A key present in one block but not the other renders as
 * the raw key id to half the users, and nothing else catches it - `t()` just
 * returns the id on a miss.
 */
describe('translation parity', () => {
  const en = Object.keys(translations.en);
  const ru = Object.keys(translations.ru);

  it('every English key has a Russian translation', () => {
    const missing = en.filter((k) => !(k in translations.ru));
    expect(missing).toEqual([]);
  });

  it('every Russian key has an English original', () => {
    const extra = ru.filter((k) => !(k in translations.en));
    expect(extra).toEqual([]);
  });

  it('no value is left as an empty string', () => {
    for (const [lang, dict] of Object.entries(translations)) {
      for (const [key, value] of Object.entries(dict)) {
        expect(value, `${lang}.${key}`).not.toBe('');
      }
    }
  });
});

/**
 * A key written twice in the same block is invisible to the parity check above:
 * a JS object literal simply keeps the last value, so both blocks still have the
 * key and the counts still match. That is how a second `range_24h: "24H"` once
 * silently retitled the metric charts' "24 Hours" range button. The source has
 * to be read as text to see it.
 */
describe('translation source hygiene', () => {
  const file = path.resolve(path.dirname(fileURLToPath(import.meta.url)), 'translations.js');
  const source = fs.readFileSync(file, 'utf8');

  // Split at the `ru:` block opener so each language is scanned on its own.
  const ruAt = source.indexOf('\n  ru: {');
  const blocks = { en: source.slice(0, ruAt), ru: source.slice(ruAt) };

  for (const [lang, block] of Object.entries(blocks)) {
    it(`declares every ${lang} key exactly once`, () => {
      const seen = new Map();
      const duplicates = [];
      for (const m of block.matchAll(/^\s{4}([A-Za-z_$][\w$]*)\s*:/gm)) {
        const key = m[1];
        if (seen.has(key)) duplicates.push(key);
        seen.set(key, true);
      }
      expect(seen.size, `${lang} block looks empty - the scanner regex is wrong`).toBeGreaterThan(50);
      expect(duplicates).toEqual([]);
    });
  }
});
