import type { SVGProps } from 'react'

// 1.75px-stroke line icons, 18px grid
const base = (p: SVGProps<SVGSVGElement>) => ({
  width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
  strokeWidth: 1.75, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, ...p,
})

export const Icon = {
  home: (p: SVGProps<SVGSVGElement>) => <svg {...base(p)}><path d="M3 11l9-7 9 7" /><path d="M5 10v10h14V10" /></svg>,
  model: (p: SVGProps<SVGSVGElement>) => <svg {...base(p)}><path d="M3 7c3 0 4 4 9 4s6-4 9-4" /><path d="M3 12h18" /><path d="M3 17c3 0 4-4 9-4s6 4 9 4" /><path d="M12 3v3M12 18v3" /></svg>,
  experiments: (p: SVGProps<SVGSVGElement>) => <svg {...base(p)}><path d="M9 3v6l-5 9a2 2 0 0 0 2 3h12a2 2 0 0 0 2-3l-5-9V3" /><path d="M8 3h8" /><path d="M7 15h10" /></svg>,
  registry: (p: SVGProps<SVGSVGElement>) => <svg {...base(p)}><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" /><path d="M12 12l8-4.5" /><path d="M12 12v9" /><path d="M12 12L4 7.5" /></svg>,
  vela: (p: SVGProps<SVGSVGElement>) => <svg {...base(p)}><path d="M3 14c3 0 3-6 6-6s3 8 6 8 3-6 6-6" /></svg>,
  settings: (p: SVGProps<SVGSVGElement>) => <svg {...base(p)}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></svg>,
  search: (p: SVGProps<SVGSVGElement>) => <svg {...base(p)}><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>,
  collapse: (p: SVGProps<SVGSVGElement>) => <svg {...base(p)}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></svg>,
  close: (p: SVGProps<SVGSVGElement>) => <svg {...base(p)}><path d="M6 6l12 12M18 6L6 18" /></svg>,
  send: (p: SVGProps<SVGSVGElement>) => <svg {...base(p)}><path d="M5 12h14" /><path d="M13 6l6 6-6 6" /></svg>,
}
