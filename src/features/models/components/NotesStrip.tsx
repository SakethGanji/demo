import { useState } from 'react'
import { catalog, type NodeKind, type Notes } from '@/shared/api/catalog'
import { useFetch } from '@/shared/hooks/useFetch'
import './project-tree.css'

const readActor = () => { try { return localStorage.getItem('ensemble.actor') || '' } catch { return '' } }
const saveActor = (s: string) => { try { localStorage.setItem('ensemble.actor', s) } catch { /* private mode */ } }

/** Tags and notes on a run or a version: one line, folded open to read or write notes. The tag `keep` (any case) pins a
 *  run, or a version's source run, against retention. It has its own "acting as" box (the same name the Registry keeps):
 *  names, not authenticated identities; each tag, note, edit and deletion records it. */
export function NotesStrip({ kind, id }: { kind: NodeKind; id: string }) {
  const { data, setData, err } = useFetch<Notes>(() => catalog.annotations(kind, id), [kind, id])
  const [tag, setTag] = useState('')
  const [note, setNote] = useState('')
  const [open, setOpen] = useState(false)
  const [msg, setMsg] = useState('')
  const [by, setByState] = useState(readActor)
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null)
  const setBy = (s: string) => { setByState(s); saveActor(s) }
  const act = (p: Promise<Notes>) => p.then((d) => { setData(d); setMsg('') }).catch((e) => setMsg((e as Error).message))
  if (err && !data) return null
  return (
    <div className="card card-body notes-card" data-testid={`notes-${kind}-${id}`}>
      <div className="tag-list">
        <span className="micro-label">tags</span>
        {(data?.tags ?? []).map((t) => <span key={t} className="tag" data-testid={`tag-${t}`}>#{t}{by && <button aria-label={`Remove ${t}`} onClick={() => act(catalog.untag(kind, id, t, by))}>×</button>}</span>)}
        <form onSubmit={(e) => { e.preventDefault(); if (tag.trim()) act(catalog.tag(kind, id, tag.trim(), by)).then(() => setTag('')) }} className="row" style={{ gap: 4 }}>
          <input className="input" style={{ width: 120, height: 26 }} placeholder={by ? 'add a tag' : 'name first'} disabled={!by} value={tag} onChange={(e) => setTag(e.target.value)} data-testid="tag-input" aria-label="Add a tag" />
        </form>
        <span className="spacer" />
        <label className="row" style={{ gap: 4 }} title="Names, not authenticated identities: there is no sign-in. The name goes in the record.">
          <span className="micro-label">acting as</span>
          <input className="input" style={{ width: 110, height: 26 }} placeholder="your name" maxLength={80} value={by} onChange={(e) => setBy(e.target.value)} data-testid="notes-actor" aria-label="Acting as" />
        </label>
        <button className="chip-btn" onClick={() => setOpen(!open)} data-testid="notes-toggle">{open ? 'Hide notes' : `Notes (${data?.notes.length ?? 0})`}</button>
      </div>
      {!by && <p className="helper" style={{ fontSize: 12, margin: '4px 0 0' }}>Type your name in “acting as” to tag or write notes (names, not authenticated identities: it goes in the record).</p>}
      {open && <div style={{ marginTop: 6 }}>
        {(data?.notes ?? []).map((n) => <div key={n.id} className="note" data-testid={`note-${n.id}`}>
          <span className="muted mono" style={{ fontSize: 11 }}>{n.actor} · {n.created_at.slice(0, 16).replace('T', ' ')}</span>
          {by && editing?.id !== n.id && <span style={{ marginLeft: 8 }}>
            <button className="chip-btn" onClick={() => setEditing({ id: n.id, text: n.value })}>Edit</button>{' '}
            <button className="chip-btn" onClick={() => { if (confirm('Delete this note? The deletion is audited.')) act(catalog.deleteNote(kind, id, n.id, by)) }}>Delete</button></span>}
          {editing?.id === n.id
            ? <form className="row" style={{ gap: 6 }} onSubmit={(e) => { e.preventDefault(); if (editing.text.trim()) act(catalog.editNote(kind, id, n.id, editing.text.trim(), by)).then(() => setEditing(null)) }}>
              <input className="input" style={{ flex: 1 }} value={editing.text} maxLength={2000} onChange={(e) => setEditing({ id: n.id, text: e.target.value })} aria-label="Edit the note" />
              <button className="btn sm" type="submit">Save</button><button className="btn sm" type="button" onClick={() => setEditing(null)}>Cancel</button></form>
            : <div>{n.value}</div>}
        </div>)}
        {by && <form onSubmit={(e) => { e.preventDefault(); if (note.trim()) act(catalog.note(kind, id, note.trim(), by)).then(() => setNote('')) }} className="row" style={{ gap: 6 }}>
          <input className="input" style={{ flex: 1 }} placeholder="add a note" maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} data-testid="note-input" aria-label="Add a note" />
          <button className="btn sm" type="submit">Add</button></form>}
      </div>}
      {msg && <p className="helper" role="alert" style={{ color: 'var(--status-danger)', fontSize: 12 }}>{msg}</p>}
    </div>
  )
}
