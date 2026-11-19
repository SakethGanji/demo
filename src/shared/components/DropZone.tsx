import { useRef, useState } from 'react'

const isCsv = (f: File) => /\.(csv|tsv|txt)$/i.test(f.name)

/** Drop or browse for one CSV. Calls `onFile` with it, or `onError` for anything that is not a CSV. */
export function DropZone({ title, helper, onFile, onError }: { title: string; helper: string; onFile: (f: File) => void; onError: (msg: string) => void }) {
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const take = (f?: File) => { if (!f) return; if (isCsv(f)) onFile(f); else onError(`${f.name} is not a CSV. Upload a .csv or .tsv file with a header row.`) }
  return (
    <div className={`drop ${over ? 'over' : ''}`} data-testid="dataset-drop" onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files[0]) }} onClick={() => input.current?.click()} role="button" tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && input.current?.click()}>
      <svg width="120" height="34" viewBox="0 0 120 34" aria-hidden><path d="M2 17C20 -8 40 42 60 17S100 -8 118 17" stroke="var(--accent-secondary)" strokeWidth="1.75" fill="none" /><path d="M2 17C20 4 40 30 60 17S100 4 118 17" stroke="var(--accent-primary)" strokeWidth="1.75" fill="none" opacity=".6" /><circle cx="2" cy="17" r="3" fill="var(--accent-primary)" /><circle cx="118" cy="17" r="3" fill="var(--accent-primary)" /></svg>
      <b className="card-title">{title}</b>
      <span className="helper">{helper}</span>
      <input ref={input} type="file" accept=".csv,.tsv,.txt" hidden data-testid="dataset-file" onChange={(e) => { take(e.target.files?.[0]); e.target.value = '' }} />
    </div>
  )
}
