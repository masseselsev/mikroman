import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { api } from './client';

/**
 * Every `api.something(...)` a component calls must actually exist on the client.
 *
 * This has now broken shipped features twice. `api.createUser` was dropped in a
 * refactor and "Add User" silently did nothing - the click handler threw
 * `api.createUser is not a function` into a console nobody had open. Then the
 * whole backups modal shipped calling seven methods (`getRouterBackups`,
 * `triggerRouterBackup`, ...) that were never added to the client at all.
 *
 * Component tests cannot catch this: they all `vi.mock('../api/client')`, so a
 * mocked method exists whether or not the real one does. So this test reads the
 * source instead and checks the two halves against each other.
 */

const here = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(here, '..');

function collectSourceFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      out.push(...collectSourceFiles(full));
    } else if (/\.jsx?$/.test(entry.name) && !/\.test\.jsx?$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

/** Every distinct `api.<name>` reference in the app, with where it was found. */
function collectApiReferences() {
  const refs = new Map();
  for (const file of collectSourceFiles(SRC)) {
    if (file.endsWith(path.join('api', 'client.js'))) continue;
    const text = fs.readFileSync(file, 'utf8');
    // `api.getUsers(`, and also `api\n  .getUsers(` as written in some handlers.
    for (const m of text.matchAll(/\bapi\s*\.\s*([A-Za-z_$][\w$]*)/g)) {
      const name = m[1];
      if (!refs.has(name)) refs.set(name, []);
      const rel = path.relative(SRC, file);
      if (!refs.get(name).includes(rel)) refs.get(name).push(rel);
    }
  }
  return refs;
}

describe('api client contract', () => {
  const refs = collectApiReferences();

  it('finds the call sites it is meant to be checking', () => {
    // A regex that silently matches nothing would make this suite vacuous.
    expect(refs.size).toBeGreaterThan(20);
    expect(refs.has('getUsers')).toBe(true);
  });

  it('exposes every method the app calls', () => {
    const missing = [...refs.entries()]
      .filter(([name]) => typeof api[name] !== 'function')
      .map(([name, files]) => `api.${name}  <- ${files.join(', ')}`);

    expect(missing, `Missing from api/client.js:\n${missing.join('\n')}`).toEqual([]);
  });
});
