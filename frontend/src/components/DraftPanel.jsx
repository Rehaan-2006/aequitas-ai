import { useEffect, useState } from 'react'
import { api } from '../lib/api'

export default function DraftPanel({ accessToken, threadId }) {
  const [templates, setTemplates] = useState([])
  const [templateId, setTemplateId] = useState('')
  const [draft, setDraft] = useState(null)
  const [draftId, setDraftId] = useState(null)
  const [exportContent, setExportContent] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.listTemplates(accessToken).then(setTemplates).catch((err) => setError(err.message))
  }, [accessToken])

  useEffect(() => {
    setDraft(null)
    setDraftId(null)
    setExportContent(null)
  }, [threadId])

  async function handleDraft() {
    setError(null)
    setLoading(true)
    try {
      const response = await api.createDraft(accessToken, threadId, templateId)
      setDraft(response.draft)
      setDraftId(response.draft_id)
      setExportContent(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleApprove() {
    setError(null)
    try {
      await api.approveDraft(accessToken, draftId)
      setDraft({ ...draft, approval_status: 'approved' })
    } catch (err) {
      setError(err.message)
    }
  }

  async function handleReject() {
    setError(null)
    try {
      await api.rejectDraft(accessToken, draftId)
      setDraft({ ...draft, approval_status: 'rejected' })
    } catch (err) {
      setError(err.message)
    }
  }

  async function handleExport() {
    setError(null)
    try {
      const response = await api.exportThread(accessToken, threadId)
      setExportContent(response.content)
    } catch (err) {
      setError(err.message)
    }
  }

  if (!threadId) return null

  return (
    <div style={{ border: '1px solid #ccc', padding: 16, marginTop: 16 }}>
      <h3>Draft a document from this thread</h3>
      {error && <p style={{ color: '#b91c1c' }}>{error}</p>}

      <select value={templateId} onChange={(e) => setTemplateId(e.target.value)} style={{ padding: 8, marginRight: 8 }}>
        <option value="">Select a template...</option>
        {templates.map((t) => (
          <option key={t.id} value={t.id}>
            {t.title} ({t.category})
          </option>
        ))}
      </select>
      <button onClick={handleDraft} disabled={!templateId || loading} style={{ padding: '8px 16px' }}>
        {loading ? 'Drafting...' : 'Generate draft'}
      </button>

      {draft && (
        <div style={{ marginTop: 16 }}>
          <p>
            <strong>Verification:</strong> {draft.verification_status}
            {' · '}
            <strong>Approval:</strong> {draft.approval_status}
          </p>
          {draft.failure_reason && <p style={{ color: '#b91c1c' }}>{draft.failure_reason}</p>}

          {Object.entries(draft.content_json || {}).map(([section, text]) => (
            <div key={section} style={{ marginBottom: 12 }}>
              <strong>{section}</strong>
              <p style={{ whiteSpace: 'pre-wrap' }}>{text}</p>
            </div>
          ))}

          <div style={{ display: 'flex', gap: 8 }}>
            <button onClick={handleApprove} disabled={draft.approval_status === 'approved'}>
              Approve
            </button>
            <button onClick={handleReject} disabled={draft.approval_status === 'rejected'}>
              Reject
            </button>
            <button onClick={handleExport} disabled={draft.approval_status !== 'approved'}>
              Export
            </button>
          </div>

          {exportContent && (
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, background: '#f5f5f5', padding: 8, marginTop: 12 }}>
              {JSON.stringify(exportContent, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
