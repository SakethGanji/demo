import { useEffect, useState } from 'react'
import { api } from './api/client'

export default function App() {
  const [count, setCount] = useState<number | null>(null)
  const [reason, setReason] = useState('damaged')
  const [amount, setAmount] = useState('10')
  const [error, setError] = useState<string | null>(null)

  async function load() {
    try {
      const s = await api.refundStats()
      setCount(s.count)
      setError(null)
    } catch (e) {
      setError(String(e))
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    await api.createRefund({ reason, amount: Number(amount) })
    await load()
  }

  return (
    <main style={{ fontFamily: 'system-ui', padding: 24 }}>
      <h1>Refunds</h1>
      <p>
        Refunds: <strong data-testid="refund-count">{count === null ? '…' : count}</strong>
      </p>
      {error && <p data-testid="refund-error">{error}</p>}
      <button data-testid="refund-refresh" onClick={load}>
        Refresh
      </button>
      <form onSubmit={submit} style={{ marginTop: 16, display: 'flex', gap: 8 }}>
        <input data-testid="refund-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
        <input data-testid="refund-amount" value={amount} onChange={(e) => setAmount(e.target.value)} />
        <button data-testid="refund-submit" type="submit">
          Create refund
        </button>
      </form>
    </main>
  )
}
