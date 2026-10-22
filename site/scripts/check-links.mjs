// Fails when a built page links to an internal page, asset or #anchor that does not exist.
// Usage: node scripts/check-links.mjs <dist-dir> <base-path>
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { join, relative, dirname, posix } from 'node:path';

const [dist = 'dist', base = '/'] = process.argv.slice(2);
const pages = [];
const walk = (dir) => {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path);
    else if (name.endsWith('.html')) pages.push(path);
  }
};
walk(dist);

const ids = new Map();
const idsOf = (file) => {
  if (!ids.has(file)) {
    const html = readFileSync(file, 'utf8');
    ids.set(file, new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1])));
  }
  return ids.get(file);
};

const resolveFile = (urlPath) => {
  if (!urlPath.startsWith(base)) return null;
  const rest = decodeURIComponent(urlPath.slice(base.length));
  const candidates = [join(dist, rest), join(dist, rest, 'index.html')];
  return candidates.find((c) => existsSync(c) && statSync(c).isFile()) ?? null;
};

const failures = [];
let checked = 0;
for (const page of pages) {
  const html = readFileSync(page, 'utf8');
  const pageUrl = base + relative(dist, page).split('\\').join('/').replace(/index\.html$/, '');
  for (const [, attr, raw] of html.matchAll(/\s(href|src)="([^"]+)"/g)) {
    if (/^(https?:|mailto:|data:|javascript:)/.test(raw) || raw.startsWith('//')) continue;
    const [pathPart, hash] = raw.split('#');
    let target = pathPart === '' ? pageUrl : pathPart.startsWith('/') ? pathPart : posix.join(dirname(pageUrl + 'x'), pathPart);
    target = target.split('?')[0];
    checked += 1;
    const file = resolveFile(target);
    if (!file) {
      failures.push(`${relative(dist, page)}: ${attr}="${raw}" -> missing ${target}`);
      continue;
    }
    if (hash && file.endsWith('.html') && !idsOf(file).has(decodeURIComponent(hash))) {
      failures.push(`${relative(dist, page)}: ${attr}="${raw}" -> no #${hash} in ${relative(dist, file)}`);
    }
  }
}

if (failures.length) {
  console.error(`${failures.length} broken internal link(s):\n` + failures.join('\n'));
  process.exit(1);
}
console.log(`${checked} internal links and assets resolved across ${pages.length} pages.`);
