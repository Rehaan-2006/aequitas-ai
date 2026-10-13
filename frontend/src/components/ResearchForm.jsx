import { useState } from 'react'
import { api } from '../lib/api'

export default function ResearchForm({ accessToken, onResult }) {
  const [query, setQuery] = useState('')
  const [jurisdiction, setJurisdiction] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const response = await api.research(accessToken, {
        query,
        jurisdiction: jurisdiction || null,
        date_from: dateFrom || null,
        date_to: dateTo || null,
      })
      onResult(response.thread_id, response.result)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ border: '1px solid #ccc', padding: 16 }}>
      <h3>New research query</h3>
      <form onSubmit={handleSubmit}>
        <textarea
          placeholder="Ask a legal research question..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          required
          rows={3}
          style={{ width: '100%', padding: 8, marginBottom: 8 }}
        />
        <div style={{ display: 'flex', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
          <input
            placeholder="jurisdiction (optional)"
            value={jurisdiction}
            onChange={(e) => setJurisdiction(e.target.value)}
            style={{ padding: 8, flex: 1 }}
          />
          <input
            type="date"
            placeholder="date from"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            style={{ padding: 8 }}
          />
          <input
            type="date"
            placeholder="date to"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            style={{ padding: 8 }}
          />
        </div>
        <button type="submit" disabled={loading || !query.trim()} style={{ padding: '8px 16px' }}>
          {loading ? 'Running pipeline...' : 'Run research'}
        </button>
      </form>
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}
    </div>
  )
}
