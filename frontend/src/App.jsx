import { useEffect, useState } from 'react'
import { supabase } from './lib/supabase'
import { api } from './lib/api'
import Auth from './components/Auth'
import ResearchForm from './components/ResearchForm'
import ResultView from './components/ResultView'
import ThreadsList from './components/ThreadsList'
import DraftPanel from './components/DraftPanel'

function App() {
  const [session, setSession] = useState(undefined)
  const [activeThreadId, setActiveThreadId] = useState(null)
  const [activeResult, setActiveResult] = useState(null)
  const [threadsRefreshKey, setThreadsRefreshKey] = useState(0)
  const [threadLoadError, setThreadLoadError] = useState(null)

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setSession(data.session))
    const { data: listener } = supabase.auth.onAuthStateChange((_event, newSession) => {
      setSession(newSession)
    })
    return () => listener.subscription.unsubscribe()
  }, [])

  if (session === undefined) {
    return <p style={{ fontFamily: 'sans-serif', padding: 40 }}>Loading...</p>
  }

  if (!session) {
    return <Auth />
  }

  const accessToken = session.access_token

  function handleNewResult(threadId, result) {
    setActiveThreadId(threadId)
    setActiveResult(result)
    setThreadsRefreshKey((k) => k + 1)
  }

  async function handleSelectThread(threadId) {
    setThreadLoadError(null)
    try {
      const thread = await api.getThread(accessToken, threadId)
      setActiveThreadId(thread.id)
      setActiveResult(thread.result)
    } catch (err) {
      setThreadLoadError(err.message)
    }
  }

  return (
    <div style={{ fontFamily: 'sans-serif', maxWidth: 900, margin: '0 auto', padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1>Aequitas AI</h1>
        <div>
          <span style={{ marginRight: 12, color: '#555' }}>{session.user.email}</span>
          <button onClick={() => supabase.auth.signOut()}>Sign out</button>
        </div>
      </div>

      <ResearchForm accessToken={accessToken} onResult={handleNewResult} />

      {threadLoadError && <p style={{ color: '#b91c1c' }}>{threadLoadError}</p>}

      <ResultView result={activeResult} />

      {activeThreadId && <DraftPanel accessToken={accessToken} threadId={activeThreadId} />}

      <ThreadsList
        accessToken={accessToken}
        onSelectThread={handleSelectThread}
        refreshKey={threadsRefreshKey}
      />
    </div>
  )
}

export default App
