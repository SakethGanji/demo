import crest from '@/shared/assets/vela-crest.svg'

/** The Vela Crest, from the design system's logo files. Marks only Vela. */
export function VelaCrest({ size = 20 }: { size?: number }) {
  return <img src={crest} width={size} height={size} alt="" aria-hidden style={{ display: 'block', flex: 'none' }} />
}
