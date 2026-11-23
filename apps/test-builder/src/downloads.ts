// Content checks for downloaded files. CSV/text is read as-is; PDF text is pulled out of the
// (possibly FlateDecode-compressed) content streams - enough to assert a statement's contents.
import { readFileSync } from 'node:fs';
import { inflateSync } from 'node:zlib';

export function pdfText(buf: Buffer): string {
  const out: string[] = [];
  const re = /stream\r?\n([\s\S]*?)\r?\nendstream/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(buf.toString('latin1')))) {
    const raw = Buffer.from(m[1]!, 'latin1');
    let content: string;
    try { content = inflateSync(raw).toString('latin1'); } catch { content = raw.toString('latin1'); }
    // Text-showing operators: (literal) Tj  and  [ (a) (b) ] TJ
    for (const t of content.matchAll(/\(((?:[^()\\]|\\.)*)\)\s*T[jJ]?/g)) {
      out.push(t[1]!.replace(/\\([\\()])/g, '$1'));
    }
  }
  return out.join('\n');
}

/** Human-readable text of a downloaded file, by extension and magic bytes. */
export function extractText(path: string): string {
  const buf = readFileSync(path);
  if (buf.subarray(0, 5).toString('latin1') === '%PDF-') return pdfText(buf);
  return buf.toString('utf8');
}
