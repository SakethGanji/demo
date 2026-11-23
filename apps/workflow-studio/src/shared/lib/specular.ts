/**
 * Pointer specular for clickable glass (the `glass-hover` recipe in index.css): one passive pointermove
 * listener for the whole app writes the pointer position, relative to the hovered `.glass-hover` element,
 * into its --mx / --my. The CSS draws a soft light spot there and fades it in and out; no component code.
 *
 * Off on touch / coarse pointers and under reduced motion (the CSS gates the spot the same way). At most one
 * style write per animation frame.
 */

let installed = false

export function installSpecular() {
  if (installed || typeof window === 'undefined' || !window.matchMedia) return
  installed = true
  const fine = window.matchMedia('(hover: hover) and (pointer: fine)')
  const calm = window.matchMedia('(prefers-reduced-motion: reduce)')

  let target: HTMLElement | null = null
  let x = 0
  let y = 0
  let raf = 0

  const apply = () => {
    raf = 0
    if (!target?.isConnected) return
    const r = target.getBoundingClientRect()
    target.style.setProperty('--mx', `${Math.round(x - r.left)}px`)
    target.style.setProperty('--my', `${Math.round(y - r.top)}px`)
  }

  document.addEventListener(
    'pointermove',
    (e) => {
      if (e.pointerType !== 'mouse' || !fine.matches || calm.matches) return
      const el = e.target instanceof Element ? e.target.closest<HTMLElement>('.glass-hover') : null
      if (!el) return
      target = el
      x = e.clientX
      y = e.clientY
      if (!raf) raf = requestAnimationFrame(apply)
    },
    { passive: true },
  )
}
