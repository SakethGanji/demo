// Minimal RFC 6455 WebSocket server side: handshake + text frames (enough for the "live sync" channel).
import { createHash } from 'node:crypto';
import type { IncomingMessage } from 'node:http';
import type { Duplex } from 'node:stream';

export interface WsConn { send(text: string): void; close(): void; onMessage(fn: (text: string) => void): void }

export function acceptWebSocket(req: IncomingMessage, socket: Duplex): WsConn | null {
  const key = req.headers['sec-websocket-key'];
  if (typeof key !== 'string') { socket.destroy(); return null; }
  const accept = createHash('sha1').update(key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
  socket.write(['HTTP/1.1 101 Switching Protocols', 'Upgrade: websocket', 'Connection: Upgrade', `Sec-WebSocket-Accept: ${accept}`, '', ''].join('\r\n'));
  const handlers: ((t: string) => void)[] = [];
  let buf = Buffer.alloc(0);
  socket.on('data', (d: Buffer) => {
    buf = Buffer.concat([buf, d]);
    while (buf.length >= 2) {
      const op = buf[0]! & 0x0f;
      const masked = (buf[1]! & 0x80) !== 0;
      let len = buf[1]! & 0x7f;
      let off = 2;
      if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
      else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
      const need = off + (masked ? 4 : 0) + len;
      if (buf.length < need) return;
      const mask = masked ? buf.subarray(off, off + 4) : null;
      const payload = Buffer.from(buf.subarray(off + (masked ? 4 : 0), need));
      if (mask) for (let i = 0; i < payload.length; i++) payload[i]! ^= mask[i % 4]!;
      buf = buf.subarray(need);
      if (op === 0x8) { socket.end(); return; }
      if (op === 0x1) for (const h of handlers) h(payload.toString('utf8'));
    }
  });
  socket.on('error', () => {});
  return {
    send(text) {
      const p = Buffer.from(text, 'utf8');
      const head = p.length < 126 ? Buffer.from([0x81, p.length]) : Buffer.from([0x81, 126, p.length >> 8, p.length & 0xff]);
      socket.write(Buffer.concat([head, p]));
    },
    close() { socket.end(Buffer.from([0x88, 0])); },
    onMessage(fn) { handlers.push(fn); },
  };
}
