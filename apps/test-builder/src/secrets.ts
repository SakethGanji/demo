// Secret hygiene: scrubbing before anything reaches a planner, and leak scanning of artifacts.
import { readFileSync, writeFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, extname } from 'node:path';
import { createHash } from 'node:crypto';
import JSZip from 'jszip';

export const MASK = '********';

/** Every encoding of a secret we expect to see in artifacts. */
export function variants(secret: string): string[] {
  const v = new Set<string>([
    secret,
    encodeURIComponent(secret),
    JSON.stringify(secret).slice(1, -1),
    Buffer.from(secret, 'utf8').toString('base64'),
  ]);
  return [...v].filter((x) => x.length >= 4);
}

/** Replace credential values with {{key}} placeholders (what the planner sees). */
export function scrub(text: string, creds: Record<string, string>): string {
  let out = text;
  // Longest values first so overlapping values don't partially survive.
  for (const [k, v] of Object.entries(creds).sort((a, b) => b[1].length - a[1].length)) {
    if (!v) continue;
    for (const variant of variants(v)) out = out.split(variant).join(`{{${k}}}`);
  }
  return out;
}

export function scrubDeep<T>(value: T, creds: Record<string, string>): T {
  return JSON.parse(scrub(JSON.stringify(value), creds)) as T;
}

function count(hay: string, needle: string): number {
  let n = 0;
  for (let i = hay.indexOf(needle); i !== -1; i = hay.indexOf(needle, i + needle.length)) n++;
  return n;
}

const TEXT_EXT = new Set(['.har', '.xml', '.json', '.txt', '.log', '.md', '.html', '.trace', '.network', '.stacks', '.ts', '.js', '.css', '.yml', '.yaml', '']);
const BINARY_EXT = new Set(['.png', '.jpg', '.jpeg', '.webm', '.mp4', '.gif', '.woff', '.woff2']);

function looksText(buf: Buffer): boolean {
  const n = Math.min(buf.length, 4096);
  for (let i = 0; i < n; i++) if (buf[i] === 0) return false;
  return true;
}

export interface LeakHit { file: string; entry?: string; occurrences: number }
export interface LeakReport { scannedFiles: number; scannedZipEntries: number; occurrences: number; hits: LeakHit[] }

export function walk(dir: string): string[] {
  const out: string[] = [];
  for (const e of readdirSync(dir)) {
    const p = join(dir, e);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

/** Scan every text-readable artifact (and zip entries, e.g. traces) for any secret variant. */
export async function scanForLeaks(dir: string, secrets: string[], exclude: string[] = []): Promise<LeakReport> {
  const needles = secrets.flatMap(variants);
  const rep: LeakReport = { scannedFiles: 0, scannedZipEntries: 0, occurrences: 0, hits: [] };
  for (const file of walk(dir)) {
    if (exclude.includes(file)) continue;
    const ext = extname(file).toLowerCase();
    const buf = readFileSync(file);
    if (ext === '.zip') {
      rep.scannedFiles++;
      const zip = await JSZip.loadAsync(buf);
      for (const entry of Object.values(zip.files)) {
        if (entry.dir) continue;
        const eb = await entry.async('nodebuffer');
        if (BINARY_EXT.has(extname(entry.name).toLowerCase()) || !looksText(eb)) continue;
        rep.scannedZipEntries++;
        const s = eb.toString('utf8');
        const n = needles.reduce((a, nd) => a + count(s, nd), 0);
        if (n) { rep.hits.push({ file: relative(dir, file), entry: entry.name, occurrences: n }); rep.occurrences += n; }
      }
      continue;
    }
    if (BINARY_EXT.has(ext) || (!TEXT_EXT.has(ext) && !looksText(buf))) continue;
    rep.scannedFiles++;
    const s = buf.toString('utf8');
    const n = needles.reduce((a, nd) => a + count(s, nd), 0);
    if (n) { rep.hits.push({ file: relative(dir, file), occurrences: n }); rep.occurrences += n; }
  }
  return rep;
}

function maskString(s: string, needles: string[]): string {
  let out = s;
  for (const nd of needles) out = out.split(nd).join(MASK);
  return out;
}

const SHA1_RESOURCE = /^resources\/([0-9a-f]{40})(\.[\w]+)?$/;

/**
 * Mask secrets in place in HAR files and inside trace zips. Playwright records `fill` values and
 * request bodies verbatim in traces/HARs, so this step is required, not cosmetic.
 *
 * Trace resources are content-addressed (`resources/<sha1>[.ext]`, referenced by sha1 from
 * *.trace / *.network). A masked resource therefore gets renamed to the sha1 of its new content and
 * every reference is rewritten, so the masked trace stays internally consistent (name == sha1(content))
 * and still opens in the trace viewer (proved by src/traceCheck.ts).
 */
export async function maskArtifacts(dir: string, secrets: string[]): Promise<string[]> {
  const needles = secrets.flatMap(variants).sort((a, b) => b.length - a.length);
  const changed: string[] = [];
  for (const file of walk(dir)) {
    const ext = extname(file).toLowerCase();
    if (ext === '.zip') {
      const zip = await JSZip.loadAsync(readFileSync(file));
      let dirty = false;
      const texts = new Map<string, string>();
      const renames = new Map<string, string>(); // old sha1 -> new sha1
      for (const entry of Object.values(zip.files)) {
        if (entry.dir) continue;
        const eb = await entry.async('nodebuffer');
        if (BINARY_EXT.has(extname(entry.name).toLowerCase()) || !looksText(eb)) continue;
        const s = eb.toString('utf8');
        const m = maskString(s, needles);
        texts.set(entry.name, m);
        if (m === s) continue;
        dirty = true;
        const r = SHA1_RESOURCE.exec(entry.name);
        if (r) {
          const fresh = createHash('sha1').update(m, 'utf8').digest('hex');
          renames.set(r[1]!, fresh);
          zip.remove(entry.name);
          texts.delete(entry.name);
          zip.file(`resources/${fresh}${r[2] ?? ''}`, m);
        } else {
          zip.file(entry.name, m);
        }
      }
      if (renames.size) {
        for (const [name, s] of texts) {
          let out = s;
          for (const [o, n] of renames) out = out.split(o).join(n);
          if (out !== s) zip.file(name, out);
        }
      }
      if (dirty) {
        writeFileSync(file, await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' }));
        changed.push(relative(dir, file));
      }
    } else if (!BINARY_EXT.has(ext)) {
      const buf = readFileSync(file);
      if (!looksText(buf)) continue;
      const s = buf.toString('utf8');
      const m = maskString(s, needles);
      if (m !== s) { writeFileSync(file, m); changed.push(relative(dir, file)); }
    }
  }
  return changed;
}
