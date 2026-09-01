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
