/** the user's modules live under `…/modules/<name>.py` in the sandbox; anything in the runtime or site-packages is a library frame */
const LIB = /(site-packages|dist-packages|\/lib\/python|<frozen |\/usr\/|\/opt\/studio\/|\/runtime\/|\/sandbox\/|\/engine\/)/
export const isUserFrame = (file: string) => /(^|\/)modules\/[^/]+\.py$/.test(file) || !LIB.test(file)

export function moduleOfFrame(file: string): string {
  return file.split('/').pop()!.replace(/\.py$/, '')
}
