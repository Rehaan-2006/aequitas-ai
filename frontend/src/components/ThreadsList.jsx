import { useEffect, useState, useCallback } from 'react'
import { api } from '../lib/api'

export default function ThreadsList({ accessToken, onSelectThread, refreshKey }) {
  const [threads, setThreads] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.listThreads(accessToken)
      setThreads(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [accessToken])

  useEffect(() => {
    load()
  }, [load, refreshKey])

  async function handleFeedback(threadId, value) {
    try {
      await api.setFeedback(accessToken, threadId, value)
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div style={{ border: '1px solid #ccc', padding: 16, marginTop: 16 }}>
      <h3>Past threads {loading && '(loading...)'}</h3>
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}
      {threads.length === 0 && !loading && <p>No threads yet.</p>}
      <ul style={{ listStyle: 'none', padding: 0 }}>
        {threads.map((t) => (
          <li key={t.id} style={{ borderBottom: '1px solid #eee', padding: '8px 0' }}>
            <button
              onClick={() => onSelectThread(t.id)}
              style={{ background: 'none', border: 'none', color: '#2563eb', cursor: 'pointer', textAlign: 'left' }}
            >
              {t.query}
            </button>
            <div style={{ fontSize: 12, color: '#666' }}>
              {new Date(t.created_at).toLocaleString()}
              {' · '}
              <button onClick={() => handleFeedback(t.id, 1)} style={{ cursor: 'pointer' }}>
                {t.feedback === 1 ? 'Upvoted' : 'Upvote'}
              </button>{' '}
              <button onClick={() => handleFeedback(t.id, -1)} style={{ cursor: 'pointer' }}>
                {t.feedback === -1 ? 'Downvoted' : 'Downvote'}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
