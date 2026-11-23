// Minimal real PDF writer: Helvetica text lines, FlateDecode-compressed content streams, valid xref.
import { deflateSync } from 'node:zlib';

const esc = (s: string) => s.replace(/[\\()]/g, (c) => '\\' + c).replace(/[^\x20-\x7e]/g, '?');

export function pdf(lines: string[], title: string): Buffer {
  const perPage = 48;
  const pages: string[][] = [];
  for (let i = 0; i < lines.length; i += perPage) pages.push(lines.slice(i, i + perPage));
  if (!pages.length) pages.push([]);
  const objs: (string | Buffer)[] = [];
  // 1 catalog, 2 pages, 3 font, 4 info, then per page: page obj + content obj
  const pageIds = pages.map((_, i) => 5 + i * 2);
  objs[1] = `<< /Type /Catalog /Pages 2 0 R >>`;
  objs[2] = `<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(' ')}] /Count ${pages.length} >>`;
  objs[3] = `<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>`;
  objs[4] = `<< /Title (${esc(title)}) /Producer (Northwind Bank statements) >>`;
  pages.forEach((pl, i) => {
    const content = ['BT', '/F1 10 Tf', '14 TL', '50 800 Td', ...pl.map((l) => `(${esc(l)}) Tj T*`), 'ET'].join('\n');
    const z = deflateSync(Buffer.from(content, 'latin1'));
    objs[pageIds[i]!] = `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents ${pageIds[i]! + 1} 0 R >>`;
    objs[pageIds[i]! + 1] = Buffer.concat([
      Buffer.from(`<< /Length ${z.length} /Filter /FlateDecode >>\nstream\n`, 'latin1'), z, Buffer.from('\nendstream', 'latin1'),
    ]);
  });
  const chunks: Buffer[] = [Buffer.from('%PDF-1.4\n%\xe2\xe3\xcf\xd3\n', 'latin1')];
  let offset = chunks[0]!.length;
  const offsets: number[] = [];
  for (let id = 1; id < objs.length; id++) {
    const body = objs[id]!;
    const b = Buffer.concat([Buffer.from(`${id} 0 obj\n`, 'latin1'), typeof body === 'string' ? Buffer.from(body, 'latin1') : body, Buffer.from('\nendobj\n', 'latin1')]);
    offsets[id] = offset;
    chunks.push(b);
    offset += b.length;
  }
  const xref = [`xref`, `0 ${objs.length}`, `0000000000 65535 f `, ...offsets.slice(1).map((o) => `${String(o).padStart(10, '0')} 00000 n `)].join('\n');
  chunks.push(Buffer.from(`${xref}\ntrailer\n<< /Size ${objs.length} /Root 1 0 R /Info 4 0 R >>\nstartxref\n${offset}\n%%EOF\n`, 'latin1'));
  return Buffer.concat(chunks);
}
