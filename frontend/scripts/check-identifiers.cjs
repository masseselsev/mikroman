#!/usr/bin/env node
/**
 * Report JSX components used in a file but never imported or defined there.
 *
 * Vite happily bundles `<Foo />` where `Foo` is undefined: it is a runtime
 * ReferenceError, not a build error, so the build passes and the page renders
 * blank. That is the exact failure mode of moving markup between files, which
 * is what splitting a large component consists of.
 *
 *   node frontend/scripts/check-identifiers.cjs
 *
 * Exits non-zero on the first file with a missing identifier.
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..', 'src');
const JSX = /<([A-Z][A-Za-z0-9_]*)/g;
const IMPORTS = /import\s+(?:\{([^}]*)\}|(\w+))\s+from/gs;
const DEFINED = /(?:function|const|class)\s+([A-Za-z_$][\w$]*)/g;
// `function Card({ icon: Icon })` renames a prop into a component position.
const DESTRUCTURED = /(?:\{|,)\s*\w+\s*:\s*([A-Z][A-Za-z0-9_]*)/g;
// Function parameters or direct destructuring: `(Icon, ...)`, `({ id, Icon })`
const ARGS = /(?:\{|,|\()\s*([A-Z][A-Za-z0-9_]*)\s*(?:,|\}|:|\))/g;

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (entry.name.endsWith('.jsx') && !entry.name.includes('.test.')) out.push(full);
  }
  return out;
}

function matchAll(re, src, group = 1) {
  const found = [];
  let m;
  re.lastIndex = 0;
  while ((m = re.exec(src)) !== null) if (m[group]) found.push(m[group]);
  return found;
}

let failures = 0;
for (const file of walk(ROOT)) {
  const src = fs.readFileSync(file, 'utf8');
  const known = new Set(['React', 'Fragment']);

  IMPORTS.lastIndex = 0;
  let m;
  while ((m = IMPORTS.exec(src)) !== null) {
    if (m[1]) {
      for (const piece of m[1].split(',')) {
        const name = piece.trim().split(/\s+as\s+/).pop().trim();
        if (name) known.add(name);
      }
    }
    if (m[2]) known.add(m[2]);
  }
  for (const name of matchAll(DEFINED, src)) known.add(name);
  for (const name of matchAll(DESTRUCTURED, src)) known.add(name);
  for (const name of matchAll(ARGS, src)) known.add(name);

  const missing = [...new Set(matchAll(JSX, src))].filter((n) => !known.has(n)).sort();
  if (missing.length) {
    failures++;
    console.error(`${path.relative(process.cwd(), file)}: missing ${missing.join(', ')}`);
  }
}

if (failures) {
  console.error(`\n${failures} file(s) reference components they never import.`);
  process.exit(1);
}
console.log('All JSX components resolve.');
