// crypto.randomUUID only exists in secure contexts. The isolated output iframe
// (and file:// previews) are not one, and the widget comm router calls it at load.
const c = globalThis.crypto as Crypto | undefined
if (c && typeof c.randomUUID !== 'function') {
  ;(c as { randomUUID: () => string }).randomUUID = () => {
    const b = c.getRandomValues(new Uint8Array(16))
    b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80
    const h = [...b].map(x => x.toString(16).padStart(2, '0')).join('')
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
  }
}
export {}
