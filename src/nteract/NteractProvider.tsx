import type { ReactNode } from 'react'
import { IsolatedRendererProvider } from '@/components/isolated/isolated-renderer-context'
import { WidgetStoreProvider } from '@/components/widgets/widget-store-context'
// The isolated-iframe renderer (built by `npm run build:renderer` into public/isolated), inlined as text
// so the single-file build needs no server. HTML, markdown, SVG and widget outputs render inside it.
import rendererCode from '../../public/isolated/isolated-renderer.js?raw'
import rendererCss from '../../public/isolated/isolated-renderer.css?raw'

const loader = () => Promise.resolve({ rendererCode, rendererCss })

/** Everything the nteract-elements outputs and widgets need: the isolated renderer and the widget store. */
export function NteractProvider({ children }: { children: ReactNode }) {
  return (
    <IsolatedRendererProvider loader={loader}>
      <WidgetStoreProvider>{children}</WidgetStoreProvider>
    </IsolatedRendererProvider>
  )
}
