import { useState } from 'react'
import { supabase } from '../lib/supabase'

export default function Auth() {
  const [mode, setMode] = useState('sign_in')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setStatus(null)
    setLoading(true)
    try {
      if (mode === 'sign_in') {
        const { error } = await supabase.auth.signInWithPassword({ email, password })
        if (error) throw error
      } else {
        const { error, data } = await supabase.auth.signUp({ email, password })
        if (error) throw error
        if (!data.session) {
          setStatus('Account created. Check your email to confirm, then sign in.')
        }
      }
    } catch (err) {
      setStatus(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ maxWidth: 360, margin: '80px auto', fontFamily: 'sans-serif' }}>
      <h2>Aequitas AI</h2>
      <p style={{ color: '#555' }}>{mode === 'sign_in' ? 'Sign in' : 'Create an account'}</p>
      <form onSubmit={handleSubmit}>
        <div style={{ marginBottom: 8 }}>
          <input
            type="email"
            placeholder="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            style={{ width: '100%', padding: 8 }}
          />
        </div>
        <div style={{ marginBottom: 8 }}>
          <input
            type="password"
            placeholder="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={6}
            style={{ width: '100%', padding: 8 }}
          />
        </div>
        <button type="submit" disabled={loading} style={{ width: '100%', padding: 8 }}>
          {loading ? 'Working...' : mode === 'sign_in' ? 'Sign in' : 'Sign up'}
        </button>
      </form>
      <button
        onClick={() => setMode(mode === 'sign_in' ? 'sign_up' : 'sign_in')}
        style={{ marginTop: 8, background: 'none', border: 'none', color: '#2563eb', cursor: 'pointer' }}
      >
        {mode === 'sign_in' ? 'Need an account? Sign up' : 'Have an account? Sign in'}
      </button>
      {status && <p style={{ color: '#b91c1c', marginTop: 12 }}>{status}</p>}
    </div>
  )
}
